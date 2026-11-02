"""The class, replayed: the three histories from the brief, on the simulated
clock, with the fake model. Block by block, as the run sheet runs it."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import clock
import engine
import store

UTC = timezone.utc


def kinds(case_id: str) -> list[str]:
    return [e["kind"] for e in store.events_for(case_id)]


def bump_policy(path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime + 2, stat.st_mtime + 2))  # a new mtime even inside one second


def add_sms_stage(data: dict) -> None:
    retail = data["nimbus-retail"]
    retail["stages"].insert(1, {"name": "sms_heads_up", "day": -1, "channel": "sms", "tone": "friendly"})
    retail["version"] = 5


def test_block_1_one_pass_then_a_policy_edit(seeded, isolated, fake_model):
    assert clock.now() == datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    result = engine.evaluate("C-1001", "manual")
    assert result["decision"].startswith("wait until 2026-11-09T00:00:00")
    case = store.get_case("C-1001")
    assert case.stage_history == {"soft_reminder": "sent"}
    assert case.policy_version == 4
    assert kinds("C-1001") == ["policy.applied", "draft.checked", "reminder.sent"]
    assert all(e["policy_version"] == 4 for e in store.events_for("C-1001"))
    assert fake_model.calls == ["Draft"]
    messages = store.messages_for("C-1001")
    assert len(messages) == 1 and "$240.00" in messages[0]["body"] and messages[0]["drafted_by"] == "model"

    bump_policy(isolated["policies"], add_sms_stage)
    clock.advance(days=1)
    engine.evaluate("C-1001", "manual")
    events = store.events_for("C-1001")
    assert [e["kind"] for e in events[3:]] == ["policy.applied", "draft.checked", "reminder.sent"]
    assert events[3]["data"] == {"from_version": 4, "to_version": 5, "tenant": "nimbus-retail"}
    assert [e["policy_version"] for e in events] == [4, 4, 4, 5, 5, 5]  # old rows keep v4
    assert store.get_case("C-1001").stage_history == {"soft_reminder": "sent", "sms_heads_up": "sent"}
    assert store.messages_for("C-1001")[1]["message_key"] == "C-1001:sms_heads_up:5"


def test_the_whole_class_sequence(seeded, isolated, fake_model):
    # Block 1, by hand on Acme only.
    engine.evaluate("C-1001", "manual")
    bump_policy(isolated["policies"], add_sms_stage)
    clock.advance(days=1)  # D-1
    engine.evaluate("C-1001", "manual")

    # Block 2: the worker starts and catches up the cases nobody touched.
    woke = [r["case_id"] for r in engine.tick()]
    assert woke == ["C-1002", "C-1003", "C-1004"]
    assert store.get_case("C-1002").stage_history == {"soft_reminder": "skipped", "sms_heads_up": "sent"}
    initech = store.get_case("C-1003")
    assert initech.stage_history == {"soft_reminder": "skipped"}
    assert kinds("C-1003")[-1] == "reminder.deferred"
    assert initech.next_due_at == datetime(2026, 11, 5, 16, 0, tzinfo=UTC)  # 08:00 PST
    assert store.get_case("C-1004").next_due_at == datetime(2026, 11, 11, 0, 0, tzinfo=UTC)  # D+5

    clock.advance(days=4)  # D+3, 09:00 UTC
    woke = sorted(r["case_id"] for r in engine.tick())
    assert woke == ["C-1001", "C-1002", "C-1003"]
    assert store.get_case("C-1001").stage_history["first_notice"] == "sent"
    assert store.get_case("C-1002").stage_history["first_notice"] == "sent"
    initech = store.get_case("C-1003")
    assert initech.stage_history == {"soft_reminder": "skipped", "sms_heads_up": "skipped"}
    deferred = store.last_event("C-1003", "reminder.deferred")
    assert deferred["data"]["reason"] == "quiet_hours" and deferred["data"]["stage"] == "first_notice"
    assert initech.next_due_at == datetime(2026, 11, 9, 16, 0, tzinfo=UTC)
    assert engine.tick() == []  # nothing else is due

    clock.advance(hours=7)  # 16:00 UTC = 09:00 in Los Angeles
    assert [r["case_id"] for r in engine.tick()] == ["C-1003"]
    assert store.get_case("C-1003").stage_history["first_notice"] == "sent"

    # The worker is "killed": two days pass with no tick. Then it comes back.
    clock.advance(days=2)  # D+5
    woke = [r["case_id"] for r in engine.tick()]
    assert woke == ["C-1004"]
    assert store.get_case("C-1004").stage_history == {"first_notice": "sent"}
    assert store.get_case("C-1004").policy_version == 2

    # Block 3: Acme pays in full (the whiteboard version still wrote to them on
    # day 10), then a dispute, with the room as the collections rep.
    engine.record_payment("C-1001", 240.0)
    acme = store.get_case("C-1001")
    assert acme.status == "settled" and acme.balance_usd == 0.0 and acme.next_due_at is None
    assert kinds("C-1001")[-2:] == ["payment.received", "balance.settled"]

    r = engine.record_inbound("C-1002", "email",
                              "We never received item 3 on this invoice and we are not paying until this is sorted out.")
    assert r["intent"]["intent"] == "dispute"
    globex = store.get_case("C-1002")
    assert globex.status == "disputed" and globex.next_due_at is None
    assert engine.tick() == []  # a disputed case is never woken

    engine.resolve_dispute("C-1002", 60.0, "item 3 credited", "the room")
    globex = store.get_case("C-1002")
    assert globex.status == "active" and globex.balance_usd == 120.0
    assert globex.next_due_at == datetime(2026, 11, 16, 0, 0, tzinfo=UTC)  # second notice, D+10
    assert kinds("C-1002")[-2:] == ["dispute.resolved", "case.resumed"]

    clock.advance(days=5)  # D+10, 16:00 UTC
    woke = sorted(r["case_id"] for r in engine.tick())
    assert woke == ["C-1002", "C-1003"]  # Acme is settled: nothing wakes it
    globex_second = store.last_event("C-1002", "reminder.sent")
    assert globex_second["data"]["stage"] == "second_notice" and globex_second["data"]["balance_usd"] == 120.0
    assert "$120.00" in store.messages_for("C-1002")[-1]["body"]  # on the adjusted balance
    assert store.get_case("C-1003").stage_history["second_notice"] == "sent"  # 09:00 PST: allowed
    assert [m["stage"] for m in store.messages_for("C-1001")] == ["soft_reminder", "sms_heads_up", "first_notice"]

    clock.advance(days=11)  # D+21, 16:00 UTC
    woke = sorted(r["case_id"] for r in engine.tick())
    assert woke == ["C-1002", "C-1003", "C-1004"]
    for case_id in ("C-1002", "C-1003"):
        c = store.get_case(case_id)
        assert c.status == "awaiting_approval" and c.pending_stage == "final_notice" and c.pending_draft is not None
    assert store.get_case("C-1004").stage_history == {"first_notice": "sent", "second_notice": "sent"}

    engine.decide_approval("C-1003", True, "the room")
    initech = store.get_case("C-1003")
    assert initech.stage_history["final_notice"] == "sent"
    assert "case.flagged" in kinds("C-1003")
    assert initech.status == "escalated"  # the policy is exhausted: a person takes it from here
    assert len(store.messages_for("C-1003")) == 3  # first, second, final: skipped stages never went out

    engine.decide_approval("C-1002", False, "the room")
    globex = store.get_case("C-1002")
    assert globex.status == "escalated" and "final_notice" not in globex.stage_history
    assert store.last_event("C-1002", "case.escalated")["data"]["reason"] == "final_notice rejected"

    # Every audit line carries a policy version once the engine has touched the case.
    for case_id in ("C-1001", "C-1002", "C-1003", "C-1004"):
        versions = [e["policy_version"] for e in store.events_for(case_id) if e["actor"] != "customer"]
        assert all(v is not None for v in versions), case_id
    assert {e["policy_version"] for e in store.events_for("C-1004")} == {2}


def test_history_three_alone_matches_the_brief(seeded, fake_model):
    """5.3 in the brief: no response, a deferral, a final notice a person approves."""
    clock.set_to(datetime(2026, 11, 9, 9, 0, tzinfo=UTC))  # D+3 09:00 UTC, 01:00 PST
    engine.evaluate("C-1003", "timer")
    assert kinds("C-1003")[-1] == "reminder.deferred"
    clock.advance(hours=7)
    engine.evaluate("C-1003", "timer")
    assert store.get_case("C-1003").stage_history["first_notice"] == "sent"
    clock.set_to(datetime(2026, 11, 16, 16, 0, tzinfo=UTC))  # D+10
    engine.evaluate("C-1003", "timer")
    clock.set_to(datetime(2026, 11, 27, 16, 0, tzinfo=UTC))  # D+21
    engine.evaluate("C-1003", "timer")
    c = store.get_case("C-1003")
    assert c.status == "awaiting_approval"
    engine.decide_approval("C-1003", True, "a manager")
    sent = [m["stage"] for m in store.messages_for("C-1003")]
    assert sent == ["first_notice", "second_notice", "final_notice"]
    assert store.last_event("C-1003", "case.flagged")["data"]["flag"] == "pre_legal"
