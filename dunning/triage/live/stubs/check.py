r"""Step 4, code: hold the proposal to the policy.

TO BUILD (prompt 2). This docstring is the spec; tests/test_check.py is the
acceptance test, and tests/test_data.py runs your code on all 30 replies.

check(reply_text, reading, match, proposal, account, policy, now) -> list[CheckFailure]
    Plain code, no model. Every limit comes from `policy` (policy.py,
    triage_policy.json): nothing hard-coded. "today" is now.date(). Dates are
    ISO strings (YYYY-MM-DD) that may be missing or nonsense: a date that
    doesn't parse is a failure, never an exception. Return the failures in
    this order, each a CheckFailure(code=..., detail=<a short reason>), and
    [] when the proposal is fine. A check that doesn't apply to the proposed
    action adds nothing.

    action_not_allowed
        proposal.action is not in policy.allowed_actions[reading.intent].
    evidence_not_in_reply
        reading.evidence is empty, or a quote is not in reply_text. Compare
        both lower-cased with every run of whitespace as one space.
    low_confidence
        reading.confidence < policy.confidence_floor.
    invoice_not_matched
        proposal.invoice_ids has an id that is not in match.invoice_ids; or
        the action touches an invoice (record_payment_claim,
        pause_for_promise, offer_payment_plan, open_dispute) while
        match.ambiguous is True or match.invoice_ids is empty. One failure
        at most.
    unknown_invoice_ref
        match.unknown_refs is not empty: the customer named an invoice that
        isn't theirs or isn't open, and a person looks.
    bad_promise_date
        Only for pause_for_promise: proposal.pause_until is missing, doesn't
        parse, is before today, or is after today + policy.max_promise_days.
    bad_plan
        Only for offer_payment_plan: no instalments, or more than
        policy.max_instalments; any due date missing, unparseable, before
        today or after today + policy.max_plan_days; due dates not strictly
        ascending; or the instalment amounts don't add up (within 0.005) to
        the balance of the invoices in proposal.invoice_ids. One failure at most.
    bad_return_date
        Only for snooze_until_return: proposal.pause_until (the return date)
        is missing, doesn't parse, is before today, or is after today +
        policy.max_snooze_days.
    bad_contact
        Only for update_contact: proposal.new_contact_email is missing, is
        not a plausible email address, or does not appear in reply_text
        (ignoring case). Code never takes an address the customer didn't write.
    reply_amount_mismatch
        proposal.draft_reply states a dollar figure ($240, $1,250.00) that is
        not the balance of an invoice in match.invoice_ids or one of the
        instalment amounts (within 0.005). Find the figures with this regex,
        the same idea as dunning/drafting.py (copy it, don't import it):
            re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
        A draft with no dollar figures passes.
    reply_to_auto_reply
        reading.intent is out_of_office and proposal.draft_reply is not None.
        Never answer an auto-reply.
"""
from __future__ import annotations

from datetime import datetime

from models import Account, CheckFailure, Match, Proposal, ReplyReading
from policy import TriagePolicy


def check(
    reply_text: str,
    reading: ReplyReading,
    match: Match,
    proposal: Proposal,
    account: Account,
    policy: TriagePolicy,
    now: datetime,
) -> list[CheckFailure]:
    raise NotImplementedError("prompt 2: see the docstring at the top of check.py")
