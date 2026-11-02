"""choose_next_action: the engine's one decision point.

BUILT LIVE IN CLASS (build 1). This docstring is the spec;
tests/test_rules.py is the acceptance test.

Every rule about WHEN a customer may be contacted lives in this file, so it
applies to every stage of every case the same way. The function is pure: it
reads the case, the policy, the clock and the times messages were sent, and
returns a Decision (models.py). It writes nothing; engine.py carries it out.

The order, top to bottom. The first rule that applies wins:

1. balance_usd <= ZERO                      -> Decision("settle")
2. status disputed or awaiting_approval     -> Decision("wait", until=None, reason=<the status>)
3. status paused                            -> Decision("wait", until=case.paused_until, reason="paused")
4. no pending stage (every stage name is already in case.stage_history)
                                            -> Decision("escalate", reason="policy exhausted: ...")
5. no pending stage eligible yet            -> Decision("wait", until=<the first pending stage's eligible time>)
6. of the eligible pending stages the LATEST is the one to act on; the earlier
   ones are stale and are listed in `skipped` (a "due in two days" note sent
   on day 10 would be wrong)
7. that stage requires_human and case.approved_stage != its name
                                            -> Decision("park", stage=..., skipped=...)
   (a person decides as soon as it is due; the send still respects rule 8)
8. next_allowed_time(now, ...) is later than now
                                            -> Decision("defer", stage=..., until=..., reason=..., skipped=...)
9. otherwise                                -> Decision("send", stage=..., skipped=...)

A stage is eligible from 00:00 UTC on its day: invoice_due + stage.day days.

next_allowed_time(now, policy, tz_name, sent) moves a candidate time, in the
customer's time zone, until no rule moves it any more:
- quiet hours (policy.quiet_hours, local time; the window may wrap midnight,
  21:00 to 08:00): move to quiet_hours.end that morning, or the next morning
  when the candidate is already past quiet_hours.start;
- weekend pause (policy.weekend_pause, Saturday or Sunday local): move to
  Monday at quiet_hours.end;
- weekly cap: when max_contacts_per_week messages (times in `sent`) fall in
  the 7 days up to the candidate (candidate - 7 days < t <= candidate), move
  to the oldest of those + 7 days.
It returns (the allowed time in UTC, the first rule that moved it, or None):
"quiet_hours", "weekend" or "weekly_cap".
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from models import Case, Decision, Policy, Stage

UTC = timezone.utc
# A balance under half a cent is zero: floating point on money.
ZERO = 0.005

_TODO = "rules.py is built live in class (build 1)"


def stage_eligible_at(case: Case, stage: Stage) -> datetime:
    """00:00 UTC on invoice_due + stage.day."""
    raise NotImplementedError(_TODO)


def in_quiet_hours(local: datetime, policy: Policy) -> bool:
    """True when `local`'s time of day falls inside policy.quiet_hours."""
    raise NotImplementedError(_TODO)


def next_allowed_time(
    now: datetime, policy: Policy, tz_name: str, sent: list[datetime]
) -> tuple[datetime, str | None]:
    """The earliest time at or after `now` a message may go out, and why it moved."""
    raise NotImplementedError(_TODO)


def choose_next_action(
    case: Case, policy: Policy, now: datetime, sent: list[datetime]
) -> Decision:
    """The decision order in the module docstring."""
    raise NotImplementedError(_TODO)
