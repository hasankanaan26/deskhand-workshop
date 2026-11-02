"""The dunning engine's front door: FastAPI over engine.py.

Every door here records an event and then calls engine.evaluate, the single
entry point. The console at /console/ is a client of exactly these
endpoints; Invoke-RestMethod can do everything it does.

    GET  /cases                         the cards
    GET  /cases/{id}                    case + audit log + outbox
    GET  /cases/{id}/explain            why now? the rules replayed, read only
    POST /cases/{id}/evaluate           one pass, by hand
    POST /cases/{id}/payments           {amount_usd}            the ledger's webhook
    POST /cases/{id}/inbound            {channel, text}         a customer reply
    POST /inbound                       {customer_email, channel, text}
    POST /cases/{id}/approve            {approved, by}          409 unless awaiting
    POST /cases/{id}/resolve            {credit_usd, note, by}  409 unless disputed
    POST /cases/{id}/resume             {by}
    GET  /clock · POST /clock/advance   {days, hours}
    GET  /policies                      the records and the versions applied
    POST /tick                          one scheduler pass in this process
    GET  /feed?after=N                  events across cases, for the console
    POST /reset                         {set}                   start over (seed.py --reset)
    GET  /triage/replies                the labelled replies in triage/data/
    POST /triage/run                    {reply_id | account_id + text, live, override}
                                        one reply through triage/'s five steps

Run: py -m uvicorn app:app --port 8006   (and py worker.py in another terminal)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import clock
import engine
import explain as explain_module
import obslog
import policy as policy_module
import queues as queue_module
import seed
import store
import worker

CONSOLE_DIR = Path(__file__).resolve().parent / "console"
_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    obslog.log_event("startup", "service.started", queue=queue_module.from_env().name, clock=clock.mode())
    # DUNNING_TICKER=inprocess runs the scheduler inside the API for a
    # one-process setup. The default is a separate `py worker.py`, so the
    # shape students see is the production one: an API and a worker.
    thread = None
    if (os.getenv("DUNNING_TICKER") or "").strip().lower() == "inprocess":
        _stop.clear()
        thread = threading.Thread(target=worker.run_forever, args=(_stop,), daemon=True, name="ticker")
        thread.start()
    yield
    _stop.set()


app = FastAPI(title="Dunning engine", lifespan=lifespan)


class PaymentRequest(BaseModel):
    amount_usd: float = Field(gt=0)


class InboundRequest(BaseModel):
    channel: str = "email"
    text: str = Field(min_length=1)


class InboundByEmail(InboundRequest):
    customer_email: str


class ApprovalRequest(BaseModel):
    approved: bool
    by: str = "a person"


class ResolveRequest(BaseModel):
    credit_usd: float = Field(ge=0)
    note: str = ""
    by: str = "a person"


class ResumeRequest(BaseModel):
    by: str = "a person"


class ResetRequest(BaseModel):
    set: Literal["class", "practice"] = "class"


class AdvanceRequest(BaseModel):
    days: int = Field(default=0, ge=0)
    hours: int = Field(default=0, ge=0)
    minutes: int = Field(default=0, ge=0)


def _case_or_404(case_id: str):
    case = store.get_case(case_id)
    if case is None:
        raise HTTPException(404, f"unknown case {case_id}")
    return case


def _card(case) -> dict:
    now = clock.now()
    title, lesson = seed.STORIES.get(case.case_id, (None, None))
    return {
        **case.model_dump(mode="json"),
        "day_label": clock.day_label(now, case.invoice_due),
        "days_from_due": (now.date() - case.invoice_due).days,
        "local_time": now.astimezone(ZoneInfo(case.customer_tz)).strftime("%a %H:%M"),
        "story": {"title": title, "lesson": lesson} if title else None,
    }


@app.get("/cases")
def list_cases() -> dict:
    return {"cases": [_card(c) for c in store.list_cases()]}


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> dict:
    case = _case_or_404(case_id)
    return {"case": _card(case), "events": store.events_for(case_id), "messages": store.messages_for(case_id)}


@app.get("/cases/{case_id}/explain")
def explain_case(case_id: str) -> dict:
    _case_or_404(case_id)
    return explain_module.explain(case_id)


@app.post("/cases/{case_id}/evaluate")
def evaluate_case(case_id: str) -> dict:
    _case_or_404(case_id)
    return engine.evaluate(case_id, "manual")


@app.post("/cases/{case_id}/payments")
def payment(case_id: str, body: PaymentRequest) -> dict:
    _case_or_404(case_id)
    return engine.record_payment(case_id, body.amount_usd)


@app.post("/cases/{case_id}/inbound")
def inbound_for_case(case_id: str, body: InboundRequest) -> dict:
    _case_or_404(case_id)
    return engine.record_inbound(case_id, body.channel, body.text)


@app.post("/inbound")
def inbound_by_email(body: InboundByEmail) -> dict:
    case = store.find_open_case_by_email(body.customer_email)
    if case is None:
        raise HTTPException(404, f"no open case for {body.customer_email}")
    return engine.record_inbound(case.case_id, body.channel, body.text)


@app.post("/cases/{case_id}/approve")
def approve(case_id: str, body: ApprovalRequest) -> dict:
    _case_or_404(case_id)
    try:
        return engine.decide_approval(case_id, body.approved, body.by)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/cases/{case_id}/resolve")
def resolve(case_id: str, body: ResolveRequest) -> dict:
    _case_or_404(case_id)
    try:
        return engine.resolve_dispute(case_id, body.credit_usd, body.note, body.by)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/cases/{case_id}/resume")
def resume(case_id: str, body: ResumeRequest) -> dict:
    _case_or_404(case_id)
    try:
        return engine.resume_case(case_id, body.by)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


def _clock_payload() -> dict:
    now = clock.now()
    seen = store.worker_seen_at()
    cases = store.list_cases()
    due = cases[0].invoice_due if cases else now.date()
    return {
        "now": now.isoformat(),
        "mode": clock.mode(),
        "day_label": clock.day_label(now, due),
        "invoice_due": due.isoformat(),
        "worker_seen_s_ago": round((store.now_wall() - seen).total_seconds(), 1) if seen else None,
        "queue": queue_module.from_env().name,
    }


@app.get("/clock")
def get_clock() -> dict:
    return _clock_payload()


@app.post("/clock/advance")
def advance_clock(body: AdvanceRequest) -> dict:
    try:
        clock.advance(days=body.days, hours=body.hours, minutes=body.minutes)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    obslog.log_event("startup", "clock.advanced", days=body.days, hours=body.hours, minutes=body.minutes,
                     now=clock.now().isoformat())
    return _clock_payload()


@app.get("/policies")
def policies() -> dict:
    return {
        "file": policy_module.file_info(),
        "policies": {t: p.model_dump() for t, p in policy_module.all_policies().items()},
        "versions_applied": store.policy_versions(),
    }


@app.post("/tick")
def tick() -> dict:
    results = engine.tick()
    store.touch_worker()
    return {"woke": [r["case_id"] for r in results], "results": results}


@app.get("/feed")
def feed(after: int = 0, limit: int = 200) -> dict:
    events = store.feed(after, limit)
    return {"events": events, "cursor": events[-1]["id"] if events else after}


@app.post("/reset")
def reset(body: ResetRequest) -> dict:
    """seed.py --reset from the console: the clock back to D-2, the chosen set."""
    cases = seed.seed(reset=True, case_set=body.set)
    return {"set": body.set, "cases": [c.case_id for c in cases], **_clock_payload()}


# ── the triage kit (triage/), for the console's Triage view ──────────────
# triage/ is a separate workflow with its own models.py, so it runs in its own
# process: try_reply.py --json, one reply per call. "live" makes two real
# model calls; without it the reply's human labels stand in for the model.

TRIAGE_DIR = Path(__file__).resolve().parent / "triage"


class TriageRunRequest(BaseModel):
    reply_id: str | None = None
    account_id: str | None = None
    text: str | None = None
    live: bool = False
    override: dict | None = None


def _triage(args: list[str], timeout: int) -> dict:
    if not (TRIAGE_DIR / "try_reply.py").exists():
        raise HTTPException(404, "no triage/ folder next to app.py")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        done = subprocess.run([sys.executable, "try_reply.py", *args, "--json"], cwd=TRIAGE_DIR,
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"triage took longer than {timeout} s")
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["failed"]
        raise HTTPException(400, tail[0])
    return json.loads(done.stdout)


@app.get("/triage/replies")
def triage_replies() -> dict:
    return _triage(["--list"], 30)


@app.post("/triage/run")
def triage_run(body: TriageRunRequest) -> dict:
    args: list[str] = []
    if body.reply_id:
        args.append(body.reply_id)
    else:
        args += ["--account", body.account_id or "", "--text", body.text or ""]
    if body.live:
        args.append("--live")
    if body.override:
        args += ["--override", json.dumps(body.override)]
    return _triage(args, 180 if body.live else 30)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/console/")


if CONSOLE_DIR.is_dir():
    app.mount("/console", StaticFiles(directory=CONSOLE_DIR, html=True), name="console")
