"""The engine: one small fixed graph, one entry point, five responsibilities.

    evaluate the case  -> what is true right now? (balance, status, policy version)
    choose             -> rules.choose_next_action, the single decision point
    carry_out          -> draft (the model), check the amount (code), write the outbox
    park               -> a person must approve this stage; draft it and stop
    wait               -> record when to wake up, and exit; nothing stays in memory
    settle / escalate  -> terminal states

It is a LangGraph StateGraph, like checkpoint 02's, with one difference that
matters: no checkpointer. A pass is a short-lived worker; the case row in
SQLite is the durable thing (the brief's Option B). Every wake-up, whether
the scheduler, a payment, a reply or a person's decision, comes through
`evaluate(case_id, trigger)` and starts at the first node, so the engine
always acts on the latest truth and never sends a reminder for a balance
that was paid in the meantime.

"Move to the next reminder" is not a wire between boxes here. It is a lookup
into the policy. Add a stage to policies.json and the same graph finds one
more entry to process.
"""
from __future__ import annotations

import operator
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command

import clock
import drafting
import errors
import inbound
import obslog
import policy as policy_module
import queues as queue_module
import rules
import store
from models import Case, Decision, InboundIntent, Policy


@dataclass
class EngineContext:
    """Per-invoke context: which database. Tests pass a temp one."""

    db_path: Path | None = None


class PassState(TypedDict, total=False):
    case_id: str
    trigger: str
    now: str
    decision: str
    policy_version: int | None
    events_added: int
    log: Annotated[list[str], operator.add]


# ---------------------------------------------------------------------------
# Writing the audit trail: one function, so every line carries the version
# ---------------------------------------------------------------------------

def record(case: Case, at: datetime, kind: str, actor: str, data: dict, db_path: Path | None) -> int:
    """An audit row in SQLite plus a mirror line in logs/runs.jsonl, with the
    policy version in force on both."""
    seq = store.append_event(case.case_id, at, kind, actor, case.policy_version, data, db_path)
    obslog.log_event(case.case_id, kind, at=at.isoformat(), actor=actor,
                     policy_version=case.policy_version, seq=seq, **data)
    return seq


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _load(state: PassState, runtime: Runtime[EngineContext]) -> tuple[Case, datetime, Path | None]:
    db_path = runtime.context.db_path
    case = store.get_case(state["case_id"], db_path)
    if case is None:
        raise KeyError(f"unknown case {state['case_id']}")
    return case, datetime.fromisoformat(state["now"]), db_path


def _evaluate(state: PassState, runtime: Runtime[EngineContext]) -> Command:
    case, now, db_path = _load(state, runtime)
    added = 0
    if not case.open:
        return Command(update={"decision": f"nothing: case is {case.status}",
                               "log": [f"{case.case_id}: {case.status}, nothing to do"]}, goto=END)
    try:
        current = policy_module.get(case.tenant, db_path)
    except errors.DeskhandError as exc:
        case.status = "escalated"
        case.next_due_at = None
        store.save_case(case, db_path)
        record(case, now, "case.escalated", "system",
               {"reason": exc.internal_error_code, "detail": str(exc)[:200]}, db_path)
        return Command(update={"decision": "escalate (policy invalid)", "events_added": 1,
                               "log": [f"{case.case_id}: escalated, {exc.internal_error_code}"]}, goto=END)
    if current.version != case.policy_version:
        old = case.policy_version
        case.policy_version = current.version
        store.save_case(case, db_path)
        record(case, now, "policy.applied", "system",
               {"from_version": old, "to_version": current.version, "tenant": case.tenant}, db_path)
        added += 1
    if case.status == "paused" and case.paused_until and now >= case.paused_until:
        case.status = "active"
        case.paused_until = None
        store.save_case(case, db_path)
        record(case, now, "case.resumed", "system", {"reason": "promise date passed"}, db_path)
        added += 1
    return Command(update={"policy_version": case.policy_version, "events_added": added,
                           "log": [f"{case.case_id}: balance ${case.balance_usd:.2f}, {case.status},"
                                   f" policy v{case.policy_version}"]}, goto="choose")


def _choose(state: PassState, runtime: Runtime[EngineContext]) -> Command:
    case, now, db_path = _load(state, runtime)
    current = policy_module.get(case.tenant, db_path)
    decision = rules.choose_next_action(case, current, now, store.sent_times(case.case_id, db_path))
    added = 0
    for name in decision.skipped:
        case.stage_history[name] = "skipped"
        store.save_case(case, db_path)
        record(case, now, "stage.skipped", "system",
               {"stage": name, "reason": "superseded by a later stage"}, db_path)
        added += 1
    _decisions[state["case_id"]] = decision
    goto = {"send": "carry_out", "defer": "wait", "wait": "wait", "park": "park",
            "settle": "settle", "escalate": "escalate"}[decision.kind]
    return Command(update={"decision": decision.describe(), "events_added": state.get("events_added", 0) + added,
                           "log": [f"{case.case_id}: {decision.describe()}"]}, goto=goto)


# The decision travels from choose to the next node here rather than in the
# state: it holds a Stage object, and the state must stay JSON-plain. One
# pass per case at a time (the lock in evaluate), so the dict cannot race.
_decisions: dict[str, Decision] = {}


def _carry_out(state: PassState, runtime: Runtime[EngineContext]) -> Command:
    case, now, db_path = _load(state, runtime)
    decision = _decisions[state["case_id"]]
    stage = decision.stage
    assert stage is not None
    approved = case.approved_stage == stage.name and case.pending_draft is not None
    if approved:
        # The person approved this exact text; who drafted it is on the request event.
        requested = store.last_event(case.case_id, "approval.requested", db_path)
        text, usage, problem = case.pending_draft, None, None
        drafted_by = (requested or {}).get("data", {}).get("drafted_by", "model")
        if not drafting.check_amounts(f"{text.subject}\n{text.body}", case.balance_usd):
            # The balance changed between the approval and the send.
            text, drafted_by, problem = drafting.template_draft(case, stage, now), "template", errors.DRAFT_REJECTED
    else:
        text, drafted_by, usage, problem = drafting.draft(case, stage, now)
    key = f"{case.case_id}:{stage.name}:{case.policy_version}"
    fresh = store.insert_message(key, case.case_id, stage.name, stage.channel, stage.tone,
                                 text.subject, text.body, drafted_by, now, db_path)
    added = 0
    if not fresh:
        record(case, now, "reminder.skipped_duplicate", "system", {"stage": stage.name, "message_key": key}, db_path)
        added += 1
    else:
        record(case, now, "draft.checked", "system",
               {"stage": stage.name, "result": "ok" if problem is None else "replaced",
                "drafted_by": drafted_by, "problem": problem,
                "cost_usd": round(usage.cost_usd, 6) if usage else 0.0}, db_path)
        record(case, now, "reminder.sent", "model" if drafted_by == "model" else "system",
               {"stage": stage.name, "channel": stage.channel, "tone": stage.tone,
                "balance_usd": case.balance_usd, "message_key": key, "subject": text.subject}, db_path)
        added += 2
        if stage.tone == "final":
            record(case, now, "case.flagged", "system", {"flag": "pre_legal", "stage": stage.name}, db_path)
            added += 1
    case.stage_history[stage.name] = "sent"
    case.approved_stage = None
    case.pending_stage = None
    case.pending_draft = None
    store.save_case(case, db_path)
    return Command(update={"events_added": state.get("events_added", 0) + added,
                           "log": [f"{case.case_id}: {stage.name} {'sent' if fresh else 'already sent'} ({drafted_by})"]},
                   goto="choose")


def _park(state: PassState, runtime: Runtime[EngineContext]) -> Command:
    case, now, db_path = _load(state, runtime)
    decision = _decisions[state["case_id"]]
    stage = decision.stage
    assert stage is not None
    text, drafted_by, usage, problem = drafting.draft(case, stage, now)
    case.status = "awaiting_approval"
    case.pending_stage = stage.name
    case.pending_draft = text
    case.next_due_at = None
    case.claimed_at = None
    store.save_case(case, db_path)
    queue_module.from_env().cancel(case.case_id)
    record(case, now, "approval.requested", "system",
           {"stage": stage.name, "balance_usd": case.balance_usd, "drafted_by": drafted_by,
            "subject": text.subject, "body": text.body,
            "cost_usd": round(usage.cost_usd, 6) if usage else 0.0}, db_path)
    return Command(update={"events_added": state.get("events_added", 0) + 1,
                           "log": [f"{case.case_id}: parked, {stage.name} waits for a person"]}, goto=END)


def _wait(state: PassState, runtime: Runtime[EngineContext]) -> Command:
    case, now, db_path = _load(state, runtime)
    decision = _decisions[state["case_id"]]
    case.next_due_at = decision.until
    case.claimed_at = None
    store.save_case(case, db_path)
    q = queue_module.from_env()
    if decision.until:
        q.schedule(case.case_id, decision.until)
    else:
        q.cancel(case.case_id)
    added = 0
    if decision.kind == "defer":
        record(case, now, "reminder.deferred", "system",
               {"stage": decision.stage.name, "reason": decision.reason,
                "until": decision.until.isoformat()}, db_path)
        added = 1
    return Command(update={"events_added": state.get("events_added", 0) + added,
                           "log": [f"{case.case_id}: next wake-up "
                                   f"{decision.until.isoformat() if decision.until else 'none (a person owns it)'}"]},
                   goto=END)


def _settle(state: PassState, runtime: Runtime[EngineContext]) -> Command:
    case, now, db_path = _load(state, runtime)
    case.status = "settled"
    case.next_due_at = None
    case.claimed_at = None
    case.paused_until = None
    store.save_case(case, db_path)
    queue_module.from_env().cancel(case.case_id)
    record(case, now, "balance.settled", "system", {"balance_usd": case.balance_usd}, db_path)
    return Command(update={"events_added": state.get("events_added", 0) + 1,
                           "log": [f"{case.case_id}: settled"]}, goto=END)


def _escalate(state: PassState, runtime: Runtime[EngineContext]) -> Command:
    case, now, db_path = _load(state, runtime)
    decision = _decisions[state["case_id"]]
    case.status = "escalated"
    case.next_due_at = None
    case.claimed_at = None
    store.save_case(case, db_path)
    queue_module.from_env().cancel(case.case_id)
    record(case, now, "case.escalated", "system", {"reason": decision.reason}, db_path)
    return Command(update={"events_added": state.get("events_added", 0) + 1,
                           "log": [f"{case.case_id}: escalated ({decision.reason})"]}, goto=END)


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------

def build() -> StateGraph:
    builder = StateGraph(PassState, context_schema=EngineContext)
    builder.add_node("evaluate", _evaluate, destinations=("choose", END))
    builder.add_node("choose", _choose, destinations=("carry_out", "wait", "park", "settle", "escalate"))
    builder.add_node("carry_out", _carry_out, destinations=("choose",))
    builder.add_node("park", _park, destinations=(END,))
    builder.add_node("wait", _wait, destinations=(END,))
    builder.add_node("settle", _settle, destinations=(END,))
    builder.add_node("escalate", _escalate, destinations=(END,))
    builder.add_edge(START, "evaluate")
    return builder


_compiled = None
_compile_lock = threading.Lock()


def graph():
    global _compiled
    with _compile_lock:
        if _compiled is None:
            _compiled = build().compile()  # no checkpointer: the case row is the memory
        return _compiled


def draw() -> str:
    return graph().get_graph().draw_mermaid()


_case_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _lock_for(case_id: str) -> threading.Lock:
    with _locks_lock:
        return _case_locks.setdefault(case_id, threading.Lock())


def evaluate(case_id: str, trigger: str, db_path: Path | None = None) -> dict:
    """THE entry point. Every timer, event and decision ends up here."""
    with _lock_for(case_id):
        now = clock.now(db_path)
        obslog.log_event(case_id, "pass.started", trigger=trigger, at=now.isoformat())
        result = graph().invoke(
            {"case_id": case_id, "trigger": trigger, "now": now.isoformat(), "events_added": 0, "log": []},
            context=EngineContext(db_path=db_path),
        )
        _decisions.pop(case_id, None)
        summary = {"case_id": case_id, "trigger": trigger, "now": now.isoformat(),
                   "decision": result.get("decision"), "events_added": result.get("events_added", 0),
                   "log": result.get("log", [])}
        obslog.log_event(case_id, "pass.finished", trigger=trigger, decision=summary["decision"],
                         events_added=summary["events_added"])
        return summary


# ---------------------------------------------------------------------------
# The other doors: every one of them records an event, then evaluates
# ---------------------------------------------------------------------------

def record_payment(case_id: str, amount_usd: float, db_path: Path | None = None) -> dict:
    """The ledger's webhook. No model anywhere near it."""
    if amount_usd <= 0:
        raise ValueError("a payment is a positive amount")
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        now = clock.now(db_path)
        case.balance_usd = round(case.balance_usd - amount_usd, 2)
        store.save_case(case, db_path)
        record(case, now, "payment.received", "customer",
               {"amount_usd": amount_usd, "balance_usd": case.balance_usd}, db_path)
    return evaluate(case_id, "payment", db_path)


def record_inbound(case_id: str, channel: str, text: str, db_path: Path | None = None) -> dict:
    """A customer's reply: the model classifies, code applies, evaluate runs."""
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        now = clock.now(db_path)
        intent, usage = inbound.classify(text, case, now)
        record(case, now, "inbound.classified", "model",
               {"channel": channel, "intent": intent.intent, "confidence": intent.confidence,
                "summary": intent.summary, "promised_date": intent.promised_date,
                "disputed_amount_usd": intent.disputed_amount_usd, "text": text[:500],
                "cost_usd": round(usage.cost_usd, 6)}, db_path)
        for kind, actor, data in inbound.apply(case, intent, now):
            store.save_case(case, db_path)
            record(case, now, kind, actor, data, db_path)
        store.save_case(case, db_path)
        if case.status == "paused" and case.next_due_at:
            queue_module.from_env().schedule(case.case_id, case.next_due_at)
        elif case.status in ("disputed", "escalated"):
            queue_module.from_env().cancel(case.case_id)
    result = evaluate(case_id, "inbound", db_path)
    result["intent"] = intent.model_dump()
    return result


def decide_approval(case_id: str, approved: bool, by: str, db_path: Path | None = None) -> dict:
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        if case.status != "awaiting_approval" or not case.pending_stage:
            raise PermissionError(f"case is {case.status}, not awaiting_approval")
        now = clock.now(db_path)
        record(case, now, "approval.decided", "person",
               {"stage": case.pending_stage, "approved": approved, "by": by}, db_path)
        if approved:
            case.approved_stage = case.pending_stage
            case.status = "active"
            case.owner = by
        else:
            case.status = "escalated"
            case.owner = by
            case.next_due_at = None
            store.save_case(case, db_path)
            record(case, now, "case.escalated", "person",
                   {"reason": f"{case.pending_stage} rejected", "by": by}, db_path)
        store.save_case(case, db_path)
    return evaluate(case_id, "approval", db_path)


def resolve_dispute(case_id: str, credit_usd: float, note: str, by: str, db_path: Path | None = None) -> dict:
    if credit_usd < 0:
        raise ValueError("a credit is zero or more")
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        if case.status != "disputed":
            raise PermissionError(f"case is {case.status}, not disputed")
        now = clock.now(db_path)
        case.balance_usd = round(case.balance_usd - credit_usd, 2)
        case.status = "active"
        case.owner = by
        store.save_case(case, db_path)
        record(case, now, "dispute.resolved", "person",
               {"credit_usd": credit_usd, "note": note, "by": by, "balance_usd": case.balance_usd}, db_path)
        record(case, now, "case.resumed", "person", {"reason": "dispute resolved"}, db_path)
    return evaluate(case_id, "dispute_resolved", db_path)


def resume_case(case_id: str, by: str, db_path: Path | None = None) -> dict:
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        if case.status not in ("escalated", "paused"):
            raise PermissionError(f"case is {case.status}; only escalated or paused cases resume")
        now = clock.now(db_path)
        case.status = "active"
        case.paused_until = None
        case.owner = by
        store.save_case(case, db_path)
        record(case, now, "case.resumed", "person", {"by": by}, db_path)
    return evaluate(case_id, "resumed", db_path)


def tick(db_path: Path | None = None) -> list[dict]:
    """One pass of the scheduler: wake every case whose time has come."""
    q = queue_module.from_env()
    now = clock.now(db_path)
    results = []
    for case_id in q.due(now, db_path):
        obslog.log_event(case_id, "worker.woke", at=now.isoformat())
        results.append(evaluate(case_id, "timer", db_path))
    return results


__all__ = ["EngineContext", "PassState", "build", "draw", "evaluate", "record_payment",
           "record_inbound", "decide_approval", "resolve_dispute", "resume_case", "tick",
           "InboundIntent", "Policy"]
