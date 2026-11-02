"""Policy as data: picked up without a restart, versioned on every line,
and never guessed when the file is broken."""
from __future__ import annotations

import json
import os

import clock
import engine
import errors
import policy
import store


def rewrite(path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime + 2, stat.st_mtime + 2))


def test_versions_are_recorded_once(seeded):
    policy.get("nimbus-retail")
    policy.get("nimbus-retail")
    policy.get("nimbus-business")
    rows = store.policy_versions()
    assert [(r["tenant"], r["version"]) for r in rows] == [("nimbus-business", 2), ("nimbus-retail", 4)]


def test_new_version_applies_mid_case_and_old_rows_keep_theirs(seeded, isolated):
    engine.evaluate("C-1001", "manual")
    data = json.loads(isolated["policies"].read_text(encoding="utf-8"))
    data["nimbus-retail"]["stages"][1]["day"] = 2  # first notice a day earlier
    data["nimbus-retail"]["version"] = 5
    rewrite(isolated["policies"], data)
    clock.advance(days=5)  # D+3, a Monday (D+2 is a Sunday: the weekend pause would defer)
    engine.evaluate("C-1001", "manual")
    events = store.events_for("C-1001")
    assert events[0]["policy_version"] == 4
    assert events[-1]["kind"] == "reminder.sent" and events[-1]["policy_version"] == 5
    assert store.get_case("C-1001").policy_version == 5
    assert [(r["tenant"], r["version"]) for r in store.policy_versions() if r["tenant"] == "nimbus-retail"] == [
        ("nimbus-retail", 4), ("nimbus-retail", 5)]


def test_a_broken_policy_file_escalates_and_sends_nothing(seeded, isolated):
    isolated["policies"].write_text("{ not json", encoding="utf-8")
    stat = isolated["policies"].stat()
    os.utime(isolated["policies"], (stat.st_atime + 2, stat.st_mtime + 2))
    result = engine.evaluate("C-1001", "manual")
    case = store.get_case("C-1001")
    assert case.status == "escalated"
    assert store.messages_for("C-1001") == []
    assert store.last_event("C-1001", "case.escalated")["data"]["reason"] == errors.POLICY_INVALID
    assert "escalate" in result["decision"]


def test_invalid_stage_order_is_a_named_failure(isolated):
    rewrite(isolated["policies"], {"t": {"version": 1, "stages": [
        {"name": "b", "day": 5, "channel": "email", "tone": "firm"},
        {"name": "a", "day": 1, "channel": "email", "tone": "formal"}]}})
    try:
        policy.get("t")
    except errors.DeskhandError as exc:
        assert exc.internal_error_code == errors.POLICY_INVALID
    else:
        raise AssertionError("stages out of day order must not validate")
