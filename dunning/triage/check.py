"""Step 4, code: hold the proposal to the policy.

Every rule is a plain function of the reply text, the reading, the match,
the proposal, the account and the policy. Each problem is a CheckFailure
with a fixed code; an empty list means the proposal may go to the gate.
Checks that don't apply to the proposed action return nothing.

The codes, in the order they are checked:

  action_not_allowed     the action is not in allowed_actions for the intent
  evidence_not_in_reply  a quote is not in the reply text (or there are none)
  low_confidence         the model's own confidence is under the floor
  invoice_not_matched    the proposal names an invoice match didn't find, or
                         touches an invoice when the match is ambiguous or empty
  unknown_invoice_ref    the customer named an invoice that isn't theirs or isn't open
  bad_promise_date       pause_for_promise: no date, a bad one, past, or too far
  bad_plan               offer_payment_plan: too many or no instalments, bad
                         dates, or a sum that isn't the balance
  bad_return_date        snooze_until_return: no date, a bad one, past, or too far
  bad_contact            update_contact: no address, not an email, not in the reply
  reply_amount_mismatch  the draft states a dollar figure code didn't supply
  reply_to_auto_reply    a draft reply to an out-of-office message
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from models import Account, CheckFailure, Match, Proposal, ReplyReading
from policy import TriagePolicy

# The actions that change something about an invoice, so need a clear match.
INVOICE_ACTIONS = {"record_payment_claim", "pause_for_promise", "offer_payment_plan", "open_dispute"}

# Copied from dunning/drafting.py (the same idea, not an import: this folder stands alone).
_AMOUNT = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def _squash(text: str) -> str:
    """Lower case, every run of whitespace one space: how quotes are compared."""
    return " ".join(text.lower().split())


def _parse(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _date_problem(value: str | None, today: date, max_days: int) -> str | None:
    """Why this date can't be used, or None when it can."""
    if not value:
        return "no date"
    d = _parse(value)
    if d is None:
        return f"{value!r} is not a YYYY-MM-DD date"
    if d < today:
        return f"{value} is in the past"
    if d > today + timedelta(days=max_days):
        return f"{value} is more than {max_days} days away"
    return None


def check(
    reply_text: str,
    reading: ReplyReading,
    match: Match,
    proposal: Proposal,
    account: Account,
    policy: TriagePolicy,
    now: datetime,
) -> list[CheckFailure]:
    failures: list[CheckFailure] = []

    def fail(code: str, detail: str) -> None:
        failures.append(CheckFailure(code=code, detail=detail))

    today = now.date()
    balances = {i.invoice_id: i.balance_usd for i in account.open_invoices()}
    action = proposal.action

    allowed = policy.allowed_actions[reading.intent]
    if action not in allowed:
        fail("action_not_allowed", f"{action} is not allowed for {reading.intent} (allowed: {', '.join(allowed)})")

    text = _squash(reply_text)
    missing = [q for q in reading.evidence if _squash(q) not in text]
    if not reading.evidence:
        fail("evidence_not_in_reply", "no evidence quoted")
    elif missing:
        fail("evidence_not_in_reply", f"not in the reply: {missing!r}")

    if reading.confidence < policy.confidence_floor:
        fail("low_confidence", f"{reading.confidence:.2f} is under the floor of {policy.confidence_floor:.2f}")

    strays = [i for i in proposal.invoice_ids if i not in match.invoice_ids]
    if strays:
        fail("invoice_not_matched", f"{', '.join(strays)} not in the match ({', '.join(match.invoice_ids) or 'none'})")
    elif action in INVOICE_ACTIONS and (match.ambiguous or not match.invoice_ids):
        fail("invoice_not_matched", f"{action} needs one clear invoice; the match was "
             f"{'ambiguous' if match.ambiguous else 'empty'} ({match.method})")

    if match.unknown_refs:
        fail("unknown_invoice_ref", f"not this customer's open invoices: {', '.join(match.unknown_refs)}")

    if action == "pause_for_promise":
        problem = _date_problem(proposal.pause_until, today, policy.max_promise_days)
        if problem:
            fail("bad_promise_date", problem)

    if action == "offer_payment_plan":
        problem = _plan_problem(proposal, balances, today, policy)
        if problem:
            fail("bad_plan", problem)

    if action == "snooze_until_return":
        problem = _date_problem(proposal.pause_until, today, policy.max_snooze_days)
        if problem:
            fail("bad_return_date", problem)

    if action == "update_contact":
        email = (proposal.new_contact_email or "").strip()
        if not email:
            fail("bad_contact", "no new address")
        elif not _EMAIL.fullmatch(email):
            fail("bad_contact", f"{email!r} is not an email address")
        elif email.lower() not in reply_text.lower():
            fail("bad_contact", f"{email} does not appear in the reply")

    if proposal.draft_reply:
        known = [balances[i] for i in match.invoice_ids if i in balances]
        known += [x.amount_usd for x in proposal.instalments]
        stated = [float(a.replace(",", "")) for a in _AMOUNT.findall(proposal.draft_reply)]
        wrong = [a for a in stated if not any(abs(a - k) < 0.005 for k in known)]
        if wrong:
            fail("reply_amount_mismatch", "the draft states " + ", ".join(f"${a:,.2f}" for a in wrong)
                 + ", which is not a matched balance or an instalment")

    if reading.intent == "out_of_office" and proposal.draft_reply is not None:
        fail("reply_to_auto_reply", "never answer an auto-reply")

    return failures


def _plan_problem(proposal: Proposal, balances: dict[str, float], today: date, policy: TriagePolicy) -> str | None:
    plan = proposal.instalments
    if not plan:
        return "no instalments"
    if len(plan) > policy.max_instalments:
        return f"{len(plan)} instalments, the most is {policy.max_instalments}"
    dates = []
    for x in plan:
        problem = _date_problem(x.due, today, policy.max_plan_days)
        if problem:
            return f"instalment due {x.due}: {problem}"
        dates.append(_parse(x.due))
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        return "instalment dates are not in ascending order"
    # The plan covers the invoices it names (invoice_not_matched checks they were matched).
    owed = sum(balances.get(i, 0.0) for i in proposal.invoice_ids)
    total = sum(x.amount_usd for x in plan)
    if abs(total - owed) >= 0.005:
        return f"instalments add up to ${total:,.2f}, the balance is ${owed:,.2f}"
    return None
