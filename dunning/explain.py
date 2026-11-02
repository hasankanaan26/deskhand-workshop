"""Why now? A read-only replay of the rules, for the console.

`explain(case_id)` asks rules.choose_next_action the question the next pass
will ask, at the simulated now, and reports which of the nine rules answered
it, in plain words. It writes nothing: no event, no policy version, no
wake-up. The engine's own pass (engine.evaluate) stays the only thing that
acts; this is the same pure function, called to look.

It also lays the tenant's cadence out stage by stage (sent, skipped, waiting
for a person, eligible, upcoming) and says what time it is where the customer
is, because "quiet hours" means nothing until you see 01:00 in Los Angeles.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import clock
import errors
import policy as policy_module
import rules
import store
from models import Case, Policy

# The ladder in rules.py, top to bottom. The console draws it.
RULES = [
    (1, "Nothing owed", "settle"),
    (2, "A person owns the case", "wait for them"),
    (3, "Paused by a promise to pay", "wait for the date"),
    (4, "No stage left", "escalate"),
    (5, "No stage eligible yet", "wait for the next one"),
    (6, "Stale stages", "skip all but the latest eligible"),
    (7, "The stage needs a person", "park it with a draft"),
    (8, "Quiet hours, the weekend, the weekly cap", "defer"),
    (9, "Nothing stops it", "send"),
]

REASONS = {
    "quiet_hours": "quiet hours",
    "weekend": "the weekend pause",
    "weekly_cap": "the weekly cap",
}


def _local(instant: datetime | None, tz_name: str) -> str | None:
    if instant is None:
        return None
    return instant.astimezone(ZoneInfo(tz_name)).strftime("%a %d %b %H:%M")


def _stages(case: Case, policy: Policy, now: datetime) -> list[dict]:
    out = []
    for s in policy.stages:
        eligible_at = rules.stage_eligible_at(case, s)
        state = case.stage_history.get(s.name)
        if state is None:
            if case.pending_stage == s.name:
                state = "awaiting_approval"
            elif eligible_at <= now:
                state = "eligible"
            else:
                state = "upcoming"
        out.append({"name": s.name, "day": s.day, "channel": s.channel, "tone": s.tone,
                    "requires_human": s.requires_human, "eligible_at": eligible_at.isoformat(),
                    "state": state})
    return out


def holding_rules(case: Case, policy: Policy, now: datetime, sent: list[datetime]) -> list[str]:
    """Every compliance rule that holds a message back right now, not just
    the first one next_allowed_time names: replay it with each rule switched
    off, and keep the rules whose absence would let the message out sooner."""
    until, first = rules.next_allowed_time(now, policy, case.customer_tz, sent)
    if first is None:
        return []
    end = policy.quiet_hours.end
    without = {
        "quiet_hours": policy.model_copy(update={"quiet_hours": policy.quiet_hours.model_copy(update={"start": end})}),
        "weekend": policy.model_copy(update={"weekend_pause": False}),
        "weekly_cap": policy.model_copy(update={"max_contacts_per_week": 10 ** 6}),
    }
    holding = [name for name, relaxed in without.items()
               if rules.next_allowed_time(now, relaxed, case.customer_tz, sent)[0] < until]
    return holding or [first]


def _sentence(case: Case, decision, rule: int, tz: str, holds: list[str]) -> str:
    who = case.customer_name
    if rule == 1:
        return f"{who} owes nothing. The next pass settles the case."
    if rule == 2:
        if case.status == "disputed":
            return f"{who} disputes the invoice. A person owns the case, so no timer runs until they resolve it."
        return f"The {case.pending_stage.replace('_', ' ')} is drafted and waits for a person's yes or no."
    if rule == 3:
        return f"{who} promised to pay. The case sleeps until {_local(decision.until, tz)} their time."
    if rule == 4:
        return "Every stage is sent or skipped and the balance is still open. The policy has run out: a person takes it."
    if rule == 5:
        return f"No stage is due yet. The next one becomes eligible on {_local(decision.until, tz)} their time."
    stage = decision.stage.name.replace("_", " ")
    skipped = ""
    if decision.skipped:
        names = ", ".join(n.replace("_", " ") for n in decision.skipped)
        skipped = f" The {names} {'is' if len(decision.skipped) == 1 else 'are'} stale and get skipped first."
    if rule == 7:
        return f"The {stage} needs a person: the next pass drafts it and parks the case for approval.{skipped}"
    if rule == 8:
        why = " and ".join(REASONS.get(h, h) for h in holds)
        why = why + (" hold" if len(holds) > 1 else " holds")
        return (f"The {stage} is due, but {why} it back until {_local(decision.until, tz)} their time"
                f" ({decision.until.strftime('%a %H:%M')} UTC).{skipped}")
    return f"The {stage} is due and no rule stops it: the next pass drafts it, checks the amount and sends it.{skipped}"


def _rule_for(decision) -> int:
    if decision.kind == "settle":
        return 1
    if decision.kind == "wait":
        if decision.reason in ("disputed", "awaiting_approval"):
            return 2
        if decision.reason == "paused":
            return 3
        return 5
    if decision.kind == "escalate":
        return 4
    if decision.kind == "park":
        return 7
    if decision.kind == "defer":
        return 8
    return 9


def explain(case_id: str, db_path: Path | None = None) -> dict:
    case = store.get_case(case_id, db_path)
    if case is None:
        raise KeyError(case_id)
    now = clock.now(db_path)
    tz = case.customer_tz
    out: dict = {
        "case_id": case_id, "now": now.isoformat(), "rules": [
            {"n": n, "title": title, "then": then} for n, title, then in RULES],
        "customer_tz": tz, "local_now": _local(now, tz),
        "rule": None, "decision": None, "sentence": None, "stages": [], "policy": None,
        "in_quiet_hours": None, "sent_last_7_days": None, "error": None,
    }
    if not case.open:
        out["sentence"] = (f"The case is {case.status}. No pass acts on it"
                           + (" until a person resumes it." if case.status == "escalated" else "."))
        out["closed"] = True
    try:
        current = policy_module.all_policies().get(case.tenant)  # read only: get() records a version
    except errors.DeskhandError as exc:
        out["error"] = f"{exc.internal_error_code}: the next pass escalates the case instead of guessing a cadence"
        return out
    if current is None:
        out["error"] = f"no policy for tenant {case.tenant}"
        return out
    out["policy"] = {"tenant": current.tenant, "name": current.name, "version": current.version,
                     "applied_version": case.policy_version,
                     "quiet_hours": current.quiet_hours.model_dump(),
                     "max_contacts_per_week": current.max_contacts_per_week,
                     "weekend_pause": current.weekend_pause}
    sent = store.sent_times(case_id, db_path)
    out["sent_last_7_days"] = sum(1 for t in sent if (now - t).total_seconds() < 7 * 86400)
    try:
        out["stages"] = _stages(case, current, now)
        out["in_quiet_hours"] = rules.in_quiet_hours(now.astimezone(ZoneInfo(tz)), current)
        if not case.open:
            return out
        probe = case.model_copy(deep=True)
        if probe.status == "paused" and probe.paused_until and now >= probe.paused_until:
            probe.status, probe.paused_until = "active", None  # what _evaluate does first
        decision = rules.choose_next_action(probe, current, now, sent)
    except NotImplementedError:
        out["error"] = "rules.py is still a stub: the build writes it (build 1)"
        return out
    rule = _rule_for(decision)
    holds = holding_rules(probe, current, now, sent) if decision.kind == "defer" else []
    out["rule"] = rule
    out["decision"] = {"kind": decision.kind, "stage": decision.stage.name if decision.stage else None,
                       "until": decision.until.isoformat() if decision.until else None,
                       "until_local": _local(decision.until, tz), "reason": decision.reason,
                       "skipped": decision.skipped, "holding": holds,
                       "describe": decision.describe()}
    out["sentence"] = _sentence(probe, decision, rule, tz, holds)
    return out
