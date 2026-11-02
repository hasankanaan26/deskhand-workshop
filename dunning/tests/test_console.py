"""The console's teaching layer, offline: the practice book, Why now, Start
over, and the tour, walked step by step through the API exactly as the
console's buttons walk it. The tour's answers (console/tour.js) are claims
about what the engine does; this file is where those claims are checked.

Not copied into dunning-live (live/start.py skips it): the build's test
counts stay what the run sheet says."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app as app_module
import explain
import seed
import store

UTC = timezone.utc
TOUR_JS = Path(__file__).resolve().parent.parent / "console" / "tour.js"


def load_tour() -> list[dict]:
    text = TOUR_JS.read_text(encoding="utf-8")
    return json.loads(text.split("window.DUNNING_TOUR = ", 1)[1].rstrip().rstrip(";"))


@pytest.fixture()
def client(fake_model):
    seed.seed(reset=True, case_set="practice")
    return TestClient(app_module.app)


def kinds(case_id: str) -> list[str]:
    return [e["kind"] for e in store.events_for(case_id)]


def sent(case_id: str) -> dict[str, dict]:
    return {m["stage"]: m for m in store.messages_for(case_id)}


def events(case_id: str, kind: str) -> list[dict]:
    return [e for e in store.events_for(case_id) if e["kind"] == kind]


def why(client, case_id: str) -> dict:
    return client.get(f"/cases/{case_id}/explain").json()


def test_the_sets(fake_model):
    assert [c.case_id for c in seed.seed(reset=True)] == ["C-1001", "C-1002", "C-1003", "C-1004"]
    practice = seed.seed(reset=True, case_set="practice")
    assert len(practice) == 13 and len(store.list_cases()) == 13
    assert set(seed.STORIES) == {c.case_id for c in practice}
    assert {c.tenant for c in practice} == {"nimbus-retail", "nimbus-business", "nimbus-subscriptions"}


def test_start_over_from_the_console(client):
    client.post("/clock/advance", json={"days": 3})
    client.post("/tick")
    r = client.post("/reset", json={"set": "class"}).json()
    assert r["cases"] == ["C-1001", "C-1002", "C-1003", "C-1004"] and r["day_label"] == "D-2"
    assert store.feed() == [] and len(client.get("/cases").json()["cases"]) == 4
    assert client.post("/reset", json={"set": "everything"}).status_code == 422


def test_cards_carry_the_story_and_the_local_time(client):
    cards = {c["case_id"]: c for c in client.get("/cases").json()["cases"]}
    assert cards["C-1003"]["local_time"] == "Wed 01:00"  # 09:00 UTC in Los Angeles (PST)
    assert cards["C-1003"]["story"]["title"] == "Never answers"
    assert cards["C-1006"]["day_label"] == "D+8"


def test_why_now_is_read_only(client):
    before = (store.feed(), store.policy_versions(), store.get_case("C-1003"))
    x = why(client, "C-1003")
    assert (store.feed(), store.policy_versions(), store.get_case("C-1003")) == before
    assert x["rule"] == 8 and x["decision"]["kind"] == "defer" and x["decision"]["holding"] == ["quiet_hours"]
    assert x["in_quiet_hours"] is True and x["local_now"] == "Wed 04 Nov 01:00"
    assert "quiet hours holds it back until Wed 04 Nov 08:00 their time" in x["sentence"]
    assert [s["state"] for s in x["stages"]] == ["eligible", "upcoming", "upcoming", "upcoming"]


def test_why_now_names_each_rule(client):
    assert why(client, "C-1001")["rule"] == 9
    hooli = why(client, "C-1006")
    assert hooli["rule"] == 9 and hooli["decision"]["skipped"] == ["soft_reminder"]
    assert "soft reminder is stale" in hooli["sentence"]
    assert why(client, "C-1004")["rule"] == 5
    client.post("/cases/C-1001/payments", json={"amount_usd": 240})
    settled = why(client, "C-1001")
    assert settled["rule"] is None and settled["closed"] and "settled" in settled["sentence"]


def test_every_rule_that_holds_a_message_back():
    """The engine's reason is the FIRST rule that moved the time; Why now
    lists every rule whose absence would let the message out sooner."""
    seed.seed(reset=True, case_set="practice")
    case = store.get_case("C-1008")  # Madrid; first notice due Saturday 14 November
    case.stage_history = {"soft_reminder": "sent"}
    import policy as policy_module
    import rules
    retail = policy_module.all_policies()["nimbus-retail"]
    saturday_night = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)  # 01:00 in Madrid
    until, first = rules.next_allowed_time(saturday_night, retail, case.customer_tz, [])
    assert first == "quiet_hours" and until == datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
    assert explain.holding_rules(case, retail, saturday_night, []) == ["weekend"]
    assert explain.holding_rules(case, retail, datetime(2026, 11, 16, 9, 0, tzinfo=UTC), []) == []


def test_why_now_says_when_the_rules_are_still_a_stub(client, monkeypatch):
    import rules

    def stub(*_args, **_kwargs):
        raise NotImplementedError("built live")

    monkeypatch.setattr(rules, "choose_next_action", stub)
    assert "still a stub" in why(client, "C-1001")["error"]


# ---------------------------------------------------------------------------
# The tour, stop by stop
# ---------------------------------------------------------------------------

def run_step(client, step: dict) -> None:
    kind = step["type"]
    if kind == "tick":
        client.post("/tick")
    elif kind == "advance":
        client.post("/clock/advance", json={"days": step.get("days", 0), "hours": step.get("hours", 0)})
        client.post("/tick")
    elif kind == "play":  # console.js play(): steps of at most three hours, a tick after each
        target = datetime.combine(seed.D + timedelta(days=step["to"]), datetime.min.time(), tzinfo=UTC) + timedelta(hours=9)
        while True:
            now = datetime.fromisoformat(client.get("/clock").json()["now"])
            left = int((target - now).total_seconds() // 60)
            if left <= 0:
                break
            client.post("/clock/advance", json={"minutes": min(180, left)})
            client.post("/tick")
    elif kind == "pay":
        assert client.post(f"/cases/{step['case']}/payments", json={"amount_usd": step["amount"]}).status_code == 200
    elif kind == "reply":
        assert client.post(f"/cases/{step['case']}/inbound", json={"channel": "email", "text": step["text"]}).status_code == 200
    elif kind == "resolve":
        assert client.post(f"/cases/{step['case']}/resolve",
                           json={"credit_usd": step["credit"], "note": step["note"], "by": "the room"}).status_code == 200
    elif kind == "approve":
        assert client.post(f"/cases/{step['case']}/approve",
                           json={"approved": step["approved"], "by": "the room"}).status_code == 200
    elif kind == "open":
        pass  # the browser only
    else:
        raise AssertionError(f"unknown step {kind}")


def local(iso: str, tz: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz))


def test_the_tour_does_what_it_says(client):
    tour = {stop["id"]: stop for stop in load_tour()}
    assert len(tour) == 10

    def stop(name: str) -> None:
        for step in tour[name]["steps"]:
            run_step(client, step)

    # 1. Six sends, two deferrals, five waits; Hooli's soft reminder skipped as stale.
    stop("first-look")
    senders = sorted(e["case_id"] + ":" + e["data"]["stage"] for e in store.feed(0, 1000) if e["kind"] == "reminder.sent")
    assert senders == ["C-1001:soft_reminder", "C-1002:soft_reminder", "C-1005:soft_reminder", "C-1006:first_notice",
                       "C-1010:soft_reminder", "C-1013:soft_reminder"]
    assert events("C-1006", "stage.skipped")[0]["data"]["stage"] == "soft_reminder"
    assert sorted(e["case_id"] for e in store.feed(0, 1000) if e["kind"] == "reminder.deferred") == ["C-1003", "C-1009"]

    # 2. +7 hours: 08:00 in Los Angeles, 11:00 in New York; both go out.
    stop("ask-why")
    assert "soft_reminder" in sent("C-1003") and "soft_reminder" in sent("C-1009")
    assert why(client, "C-1003")["rule"] == 5

    # 3. Play to D+3: Proseware sends on a Sunday; Hooli's second notice on Friday, the first tick after 08:00 Dublin;
    #    Initech's Monday first notice is held by the weekend (still Sunday in Los Angeles).
    stop("to-d3")
    assert local(sent("C-1012")["retry_notice"]["sent_at"], "Europe/Istanbul").strftime("%a") == "Sun"
    assert local(sent("C-1006")["second_notice"]["sent_at"], "Europe/Dublin").strftime("%a %H:%M") == "Fri 10:00"
    assert events("C-1003", "reminder.deferred")[-1]["data"]["reason"] == "weekend"
    assert {"first_notice"} <= set(sent("C-1001")) and {"first_notice"} <= set(sent("C-1005"))

    # 4. Money moves: Acme settles, Cedar & Pine owes $210, Wingtip's claim changes nothing until the ledger pays.
    stop("money")
    assert store.get_case("C-1001").status == "settled"
    assert store.get_case("C-1005").balance_usd == 210.0
    wingtip = kinds("C-1013")
    claim = wingtip.index("payment.claimed")
    assert wingtip[claim + 1:] == ["payment.received", "balance.settled"]

    # 5. Replies: the injection moves no money; the dispute and credit; stop; the promise.
    stop("replies")
    assert store.get_case("C-1010").balance_usd == 880.0 and store.get_case("C-1010").status == "active"
    assert store.get_case("C-1002").balance_usd == 120.0 and store.get_case("C-1002").status == "active"
    assert store.get_case("C-1009").status == "escalated"
    tailspin = store.get_case("C-1011")
    assert tailspin.status == "paused" and tailspin.paused_until == datetime(2026, 11, 17, 1, 0, tzinfo=UTC)

    # 6. Play to D+5: Sydney first, Paris at 09:00 local; Proseware held by the weekly cap.
    stop("to-d5")
    koala, umbrella = sent("C-1007")["first_notice"], sent("C-1004")["first_notice"]
    assert koala["sent_at"] < umbrella["sent_at"]
    assert local(umbrella["sent_at"], "Europe/Paris").hour >= 9
    proseware = why(client, "C-1012")
    assert proseware["rule"] == 8 and proseware["decision"]["holding"] == ["weekly_cap"]
    assert events("C-1012", "reminder.deferred")[-1]["data"]["reason"] == "quiet_hours"  # the engine's first rule

    # 7. Play to D+10: the weekend, a stale stage, a park; the second notices state today's balances.
    stop("to-d10")
    northwind = local(sent("C-1008")["first_notice"]["sent_at"], "Europe/Madrid")
    assert northwind.strftime("%a %d") == "Mon 16"
    assert events("C-1008", "reminder.deferred")[-1]["data"]["reason"] == "quiet_hours"
    assert [e["data"]["stage"] for e in events("C-1012", "stage.skipped")] == ["second_retry"]
    assert "last_call" in sent("C-1012")
    assert store.get_case("C-1012").status == "awaiting_approval"
    assert "$120.00" in sent("C-1002")["second_notice"]["body"]
    assert "$210.00" in sent("C-1005")["second_notice"]["body"]

    # 8. Approve the last stage: sent, pre_legal, escalated (the policy ran out).
    stop("decide")
    tail = kinds("C-1012")[-3:]
    assert tail == ["reminder.sent", "case.flagged", "case.escalated"]
    assert events("C-1012", "case.escalated")[-1]["data"]["reason"].startswith("policy exhausted")

    # 9. Play to D+14: Tailspin wakes by itself, then the weekly cap holds its second notice to 24 November.
    stop("to-d14")
    assert "case.resumed" in kinds("C-1011") and "first_notice" in sent("C-1011")
    assert local(sent("C-1011")["first_notice"]["sent_at"], "Asia/Singapore").strftime("%d") == "17"
    t = why(client, "C-1011")
    assert t["rule"] == 8 and t["decision"]["holding"] == ["weekly_cap"]
    assert t["decision"]["until"].startswith("2026-11-24")
    assert store.get_case("C-1006").status == "awaiting_approval"

    # 10. Play to D+21: five final notices wait for a person.
    stop("to-d21")
    waiting = sorted(c.case_id for c in store.list_cases() if c.status == "awaiting_approval")
    assert waiting == ["C-1002", "C-1003", "C-1005", "C-1006", "C-1010"]
    statuses = {c.case_id: c.status for c in store.list_cases()}
    assert [k for k, v in statuses.items() if v == "settled"] == ["C-1001", "C-1013"]
    assert sorted(k for k, v in statuses.items() if v == "escalated") == ["C-1009", "C-1012"]


# ── the Triage view: GET /triage/replies, POST /triage/run ─────────────────
# These call triage/try_reply.py in a separate process, with the labels
# standing in for the model: no model call, the same answer every run.

def test_triage_lists_the_labelled_replies():
    r = TestClient(app_module.app).get("/triage/replies")
    assert r.status_code == 200
    body = r.json()
    assert len(body["replies"]) == 30 and body["accounts"]
    assert {"id", "intent", "hard_because", "account_name"} <= set(body["replies"][0])


def test_triage_runs_a_reply_with_its_labels():
    r = TestClient(app_module.app).post("/triage/run", json={"reply_id": "R-10"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "labels"
    assert [f["code"] for f in body["failures"]] == ["bad_plan"]
    assert body["disposition"]["escalated"] is True
    assert body["labelled"]["outcome_agrees"] is True


def test_triage_what_if_is_caught_by_a_check():
    override = {"reading": {"intent": "already_paid"}}
    r = TestClient(app_module.app).post("/triage/run", json={"reply_id": "R-05", "override": override})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "override"
    assert "action_not_allowed" in [f["code"] for f in body["failures"]]


def test_triage_refuses_an_unlabelled_reply_without_the_model():
    r = TestClient(app_module.app).post("/triage/run", json={"account_id": "A-01", "text": "We paid."})
    assert r.status_code == 400 and "--live" in r.json()["detail"]
