"""choose_next_action and the compliance rules, with no engine around them."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import rules
from models import Case, Policy, QuietHours, Stage

UTC = timezone.utc
D = date(2026, 11, 6)  # Friday


def policy(**overrides) -> Policy:
    base = dict(
        tenant="t", version=4,
        stages=[Stage(name="soft", day=-2, channel="email", tone="friendly"),
                Stage(name="first", day=3, channel="email", tone="formal"),
                Stage(name="second", day=10, channel="sms+email", tone="firm"),
                Stage(name="final", day=21, channel="email", tone="final", requires_human=True)],
        quiet_hours=QuietHours(start="21:00", end="08:00"), max_contacts_per_week=3, weekend_pause=True,
    )
    base.update(overrides)
    return Policy(**base)


def case(**overrides) -> Case:
    base = dict(case_id="C-1", tenant="t", customer_name="Acme", customer_email="a@x",
                customer_tz="Europe/London", invoice_id="I", invoice_due=D, amount_usd=240.0, balance_usd=240.0)
    base.update(overrides)
    return Case(**base)


def utc(day_offset: int, hour: int = 9) -> datetime:
    return datetime.combine(D + timedelta(days=day_offset), datetime.min.time(), tzinfo=UTC) + timedelta(hours=hour)


def test_decision_order_settle_first():
    d = rules.choose_next_action(case(balance_usd=0.0, status="disputed"), policy(), utc(3), [])
    assert d.kind == "settle"


def test_people_first_then_pause():
    assert rules.choose_next_action(case(status="disputed"), policy(), utc(3), []).kind == "wait"
    assert rules.choose_next_action(case(status="awaiting_approval"), policy(), utc(3), []).kind == "wait"
    paused = case(status="paused", paused_until=utc(6))
    d = rules.choose_next_action(paused, policy(), utc(3), [])
    assert d.kind == "wait" and d.until == utc(6)


def test_nothing_eligible_waits_for_the_next_stage():
    c = case(stage_history={"soft": "sent"})
    d = rules.choose_next_action(c, policy(), utc(0), [])
    assert d.kind == "wait" and d.until == rules.stage_eligible_at(c, policy().stages[1])
    assert d.until == datetime(2026, 11, 9, 0, 0, tzinfo=UTC)


def test_latest_eligible_stage_wins_and_earlier_ones_are_skipped():
    d = rules.choose_next_action(case(), policy(), utc(3), [])  # soft and first both eligible
    assert d.kind == "send" and d.stage.name == "first" and d.skipped == ["soft"]


def test_policy_exhausted_escalates():
    c = case(stage_history={"soft": "sent", "first": "sent", "second": "sent", "final": "sent"})
    assert rules.choose_next_action(c, policy(), utc(30), []).kind == "escalate"


def test_requires_human_parks_before_compliance():
    c = case(stage_history={"soft": "sent", "first": "sent", "second": "sent"}, customer_tz="America/Los_Angeles")
    d = rules.choose_next_action(c, policy(), utc(21), [])  # 01:00 in LA: quiet, but the park comes first
    assert d.kind == "park" and d.stage.name == "final"
    c.approved_stage = "final"
    d = rules.choose_next_action(c, policy(), utc(21), [])
    assert d.kind == "defer" and d.reason == "quiet_hours"


@pytest.mark.parametrize("tz,hour,expected", [
    ("Europe/London", 9, "send"),             # 09:00 local
    ("America/Los_Angeles", 9, "defer"),      # 01:00 PST
    ("Asia/Tokyo", 13, "defer"),              # 22:00 local
    ("Asia/Dubai", 9, "send"),                # 13:00 local
])
def test_quiet_hours_in_three_timezones(tz, hour, expected):
    c = case(customer_tz=tz, stage_history={"soft": "sent"})
    d = rules.choose_next_action(c, policy(), utc(3, hour), [])
    assert d.kind == expected
    if expected == "defer":
        assert d.reason == "quiet_hours"
        local = d.until.astimezone(__import__("zoneinfo").ZoneInfo(tz))
        assert local.hour == 8 and local.minute == 0


def test_quiet_hours_after_start_defers_to_tomorrow():
    c = case(customer_tz="Europe/London", stage_history={"soft": "sent"})
    d = rules.choose_next_action(c, policy(), utc(3, 22), [])  # 23:00 London on Monday
    assert d.kind == "defer" and d.until == datetime(2026, 11, 10, 8, 0, tzinfo=UTC)  # Tue 08:00 GMT


def test_weekend_pause_moves_to_monday():
    c = case(invoice_due=date(2026, 11, 4), stage_history={"soft": "sent"})  # Wednesday: first notice on Saturday
    d = rules.choose_next_action(c, policy(), datetime(2026, 11, 7, 9, 0, tzinfo=UTC), [])
    assert d.kind == "defer" and d.reason == "weekend"
    assert d.until == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)  # Monday 08:00 GMT
    d2 = rules.choose_next_action(c, policy(weekend_pause=False), datetime(2026, 11, 7, 9, 0, tzinfo=UTC), [])
    assert d2.kind == "send"


def test_weekly_cap_counts_the_trailing_seven_days():
    sent = [utc(-2), utc(-1)]  # two touches already this week
    c = case(stage_history={"soft": "sent", "extra": "sent"})
    capped = rules.choose_next_action(c, policy(max_contacts_per_week=2), utc(3), sent)
    assert capped.kind == "defer" and capped.reason == "weekly_cap"
    assert capped.until == utc(-2) + timedelta(days=7)
    fine = rules.choose_next_action(c, policy(max_contacts_per_week=3), utc(3), sent)
    assert fine.kind == "send"


def test_in_quiet_hours_window_wrapping_midnight():
    p = policy()
    london = __import__("zoneinfo").ZoneInfo("Europe/London")
    assert rules.in_quiet_hours(datetime(2026, 11, 9, 23, 0, tzinfo=london), p)
    assert rules.in_quiet_hours(datetime(2026, 11, 9, 7, 59, tzinfo=london), p)
    assert not rules.in_quiet_hours(datetime(2026, 11, 9, 8, 0, tzinfo=london), p)
    assert not rules.in_quiet_hours(datetime(2026, 11, 9, 20, 59, tzinfo=london), p)
