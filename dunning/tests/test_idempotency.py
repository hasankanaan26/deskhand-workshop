"""One message per stage per policy version, however many times a pass runs."""
from __future__ import annotations

from datetime import datetime, timezone

import engine
import store

UTC = timezone.utc


def test_the_outbox_key_rejects_a_second_insert(isolated):
    now = datetime(2026, 11, 4, 9, tzinfo=UTC)
    assert store.insert_message("C-1:soft:4", "C-1", "soft", "email", "friendly", "s", "b", "model", now)
    assert not store.insert_message("C-1:soft:4", "C-1", "soft", "email", "friendly", "s", "b again", "model", now)
    assert len(store.messages_for("C-1")) == 1


def test_a_replayed_pass_sends_nothing_twice(seeded, fake_model):
    engine.evaluate("C-1001", "manual")
    assert len(store.messages_for("C-1001")) == 1
    # Pretend the case row was rolled back after the send (a crash between the
    # outbox write and the row write): the stage looks unsent again.
    case = store.get_case("C-1001")
    case.stage_history = {}
    case.next_due_at = None
    store.save_case(case)
    engine.evaluate("C-1001", "replay")
    assert len(store.messages_for("C-1001")) == 1
    kinds = [e["kind"] for e in store.events_for("C-1001")]
    assert "reminder.skipped_duplicate" in kinds
    assert store.get_case("C-1001").stage_history == {"soft_reminder": "sent"}


def test_two_ticks_on_one_due_case_wake_it_once(seeded, fake_model):
    first = [r["case_id"] for r in engine.tick()]
    second = [r["case_id"] for r in engine.tick()]
    assert sorted(first) == ["C-1001", "C-1002", "C-1003", "C-1004"]
    assert second == []
    assert len(store.messages_for("C-1001")) == 1
