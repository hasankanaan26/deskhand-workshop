"""choose_next_action: the engine's one decision point.

Every rule about WHEN a customer may be contacted lives in this file, so it
applies to every stage the same way (the brief's limitation 5: rules copied
into each named step drift, and the day someone updates three steps and
forgets the fourth is the day a notice goes out at 2 a.m.).

The function is pure: it reads the case, the policy, the clock and the
list of messages already sent, and returns a Decision. It writes nothing.
engine.py carries the decision out.

The order, top to bottom:

1. nothing owed            -> settle
2. a person owns the case  -> wait for them (disputed, awaiting approval)
3. paused (promise to pay) -> wait until the promise date
4. no stage left           -> escalate: the policy ran out, the balance did not
5. no stage eligible yet   -> wait until the next one is
6. the latest eligible stage is the one to act on; earlier unsent stages are
   stale and get skipped (a "friendly heads-up, due in two days" sent on
   day 10 would be wrong)
7. the stage needs a person -> park it for approval, with a draft
8. compliance: quiet hours, the weekend, the weekly cap -> defer
9. send
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from models import Case, Decision, Policy, Stage

UTC = timezone.utc
# A balance under half a cent is zero: floating point on money.
ZERO = 0.005


def stage_eligible_at(case: Case, stage: Stage) -> datetime:
    """A stage becomes eligible at the start (UTC) of its day, counted from
    the invoice due date. WHEN on that day it goes out is the compliance
    rules' business, below."""
    day = case.invoice_due + timedelta(days=stage.day)
    return datetime.combine(day, time(0, 0), tzinfo=UTC)


def _parse_hm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def in_quiet_hours(local: datetime, policy: Policy) -> bool:
    start, end = _parse_hm(policy.quiet_hours.start), _parse_hm(policy.quiet_hours.end)
    t = local.time()
    if start > end:  # wraps midnight: 21:00 to 08:00
        return t >= start or t < end
    return start <= t < end


def next_allowed_time(
    now: datetime, policy: Policy, tz_name: str, sent: list[datetime]
) -> tuple[datetime, str | None]:
    """The earliest instant at or after `now` when a message may go out, and
    the first rule that pushed it later (None if now is fine)."""
    tz = ZoneInfo(tz_name)
    candidate = now.astimezone(tz)
    reason: str | None = None
    allowed_from = _parse_hm(policy.quiet_hours.end)
    for _ in range(10):  # each rule can move the time; re-check until stable
        moved = False
        if in_quiet_hours(candidate, policy):
            start = _parse_hm(policy.quiet_hours.start)
            day = candidate.date()
            if start > allowed_from and candidate.time() >= start:
                day = day + timedelta(days=1)  # past 21:00: tomorrow at 08:00
            candidate = datetime.combine(day, allowed_from, tzinfo=tz)
            reason = reason or "quiet_hours"
            moved = True
        if policy.weekend_pause and candidate.weekday() >= 5:
            monday = candidate.date() + timedelta(days=7 - candidate.weekday())
            candidate = datetime.combine(monday, allowed_from, tzinfo=tz)
            reason = reason or "weekend"
            moved = True
        window = [t for t in sent if candidate - timedelta(days=7) < t.astimezone(tz) <= candidate]
        if len(window) >= policy.max_contacts_per_week and window:
            oldest = min(window)
            candidate = (oldest + timedelta(days=7)).astimezone(tz)
            reason = reason or "weekly_cap"
            moved = True
        if not moved:
            break
    return candidate.astimezone(UTC), reason


def choose_next_action(
    case: Case, policy: Policy, now: datetime, sent: list[datetime]
) -> Decision:
    if case.balance_usd <= ZERO:
        return Decision("settle")
    if case.status in ("disputed", "awaiting_approval"):
        return Decision("wait", until=None, reason=case.status)
    if case.status == "paused":
        return Decision("wait", until=case.paused_until, reason="paused")

    pending = [s for s in policy.stages if s.name not in case.stage_history]
    if not pending:
        return Decision("escalate", reason="policy exhausted: every stage sent, balance still open")
    eligible = [s for s in pending if stage_eligible_at(case, s) <= now]
    if not eligible:
        return Decision("wait", until=stage_eligible_at(case, pending[0]))

    stage = eligible[-1]
    skipped = [s.name for s in eligible[:-1]]
    if stage.requires_human and case.approved_stage != stage.name:
        return Decision("park", stage=stage, skipped=skipped)
    until, reason = next_allowed_time(now, policy, case.customer_tz, sent)
    if until > now:
        return Decision("defer", stage=stage, until=until, reason=reason, skipped=skipped)
    return Decision("send", stage=stage, skipped=skipped)
