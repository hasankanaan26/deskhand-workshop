"""The inbound path: the model classifies, code decides, the balance never
moves because of a reply."""
from __future__ import annotations

from datetime import datetime, timezone

import clock
import engine
import inbound
import store
from models import InboundIntent

UTC = timezone.utc


def intent(kind: str, **fields) -> InboundIntent:
    base = dict(intent=kind, promised_date=None, disputed_amount_usd=None, summary="scripted", confidence=0.9)
    base.update(fields)
    return InboundIntent(**base)


def test_dispute_pauses_the_schedule_and_flags_a_person(seeded, fake_model):
    engine.evaluate("C-1002", "manual")
    fake_model.intents.append(intent("dispute", disputed_amount_usd=60.0))
    r = engine.record_inbound("C-1002", "email", "Item 3 never arrived.")
    c = store.get_case("C-1002")
    assert c.status == "disputed" and c.next_due_at is None and c.balance_usd == 180.0
    assert r["intent"]["intent"] == "dispute"
    classified = store.last_event("C-1002", "inbound.classified")
    assert classified["actor"] == "model" and classified["data"]["text"] == "Item 3 never arrived."
    assert store.last_event("C-1002", "case.disputed")["data"]["disputed_amount_usd"] == 60.0


def test_promise_to_pay_pauses_until_the_morning_after(seeded, fake_model):
    engine.evaluate("C-1001", "manual")
    fake_model.intents.append(intent("promise_to_pay", promised_date="2026-11-06"))  # a Friday
    engine.record_inbound("C-1001", "email", "We'll pay on Friday.")
    c = store.get_case("C-1001")
    assert c.status == "paused"
    assert c.paused_until == datetime(2026, 11, 7, 9, 0, tzinfo=UTC)  # Sat 09:00 London (GMT)
    assert c.next_due_at == c.paused_until
    assert store.last_event("C-1001", "case.paused")["data"]["promised_date"] == "2026-11-06"
    # Nothing goes out while paused, even when a stage is eligible.
    clock.set_to(datetime(2026, 11, 6, 12, 0, tzinfo=UTC))
    assert "C-1001" not in [r["case_id"] for r in engine.tick()]  # the other three cases do wake
    assert store.get_case("C-1001").stage_history == {"soft_reminder": "sent"}
    # After the promise date, the schedule resumes on its own.
    clock.set_to(datetime(2026, 11, 9, 9, 0, tzinfo=UTC))  # D+3
    assert "C-1001" in [r["case_id"] for r in engine.tick()]
    c = store.get_case("C-1001")
    assert c.status == "active" and c.stage_history.get("first_notice") == "sent"
    assert "case.resumed" in [e["kind"] for e in store.events_for("C-1001")]


def test_payment_claim_changes_nothing_but_the_log(seeded, fake_model):
    engine.evaluate("C-1001", "manual")
    before = store.get_case("C-1001")
    fake_model.intents.append(intent("payment_claim"))
    engine.record_inbound("C-1001", "email", "We already paid this last week.")
    after = store.get_case("C-1001")
    assert after.balance_usd == before.balance_usd and after.status == "active"
    assert after.next_due_at == before.next_due_at
    assert store.last_event("C-1001", "payment.claimed") is not None


def test_stop_contact_and_low_confidence_escalate(seeded, fake_model):
    fake_model.intents.append(intent("stop_contact"))
    engine.record_inbound("C-1003", "sms", "Stop texting me.")
    assert store.get_case("C-1003").status == "escalated"
    fake_model.intents.append(intent("dispute", confidence=0.3))
    engine.record_inbound("C-1004", "email", "hmm")
    c = store.get_case("C-1004")
    assert c.status == "escalated"
    assert store.last_event("C-1004", "case.escalated")["data"]["reason"] == "low confidence"


def test_question_leaves_the_schedule_alone(seeded, fake_model):
    engine.evaluate("C-1001", "manual")
    before = store.get_case("C-1001").next_due_at
    fake_model.intents.append(intent("question"))
    engine.record_inbound("C-1001", "email", "Which bank account should we use?")
    c = store.get_case("C-1001")
    assert c.status == "active" and c.next_due_at == before
    assert store.last_event("C-1001", "inbound.noted") is not None


def test_bad_or_past_promise_dates_do_not_pause(seeded, fake_model):
    now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    assert inbound.parse_promised_date("next week", now) is None
    assert inbound.parse_promised_date("2026-10-22", now) is None  # in the past
    assert inbound.parse_promised_date("2026-11-13", now).isoformat() == "2026-11-13"
    fake_model.intents.append(intent("promise_to_pay", promised_date="soon"))
    engine.record_inbound("C-1001", "email", "soon")
    assert store.get_case("C-1001").status == "escalated"


def test_inbound_by_email_finds_the_open_case(seeded, fake_model):
    assert store.find_open_case_by_email("AP@acme.example").case_id == "C-1001"
    assert store.find_open_case_by_email("nobody@acme.example") is None
