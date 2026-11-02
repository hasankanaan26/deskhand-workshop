"""gate.py: failures escalate; needs_person waits; every draft waits."""
from __future__ import annotations

from conftest import proposal

from gate import gate
from models import CheckFailure


def test_any_failure_escalates_and_sends_nothing(policy):
    f = [CheckFailure(code="bad_plan", detail="adds up to $200.00")]
    d = gate(proposal(action="offer_payment_plan", draft_reply="Here is your plan."), f, policy)
    assert (d.action, d.runs, d.reply, d.escalated) == ("escalate", "now", "none", True)
    assert d.failures == f


def test_reversible_action_runs_now(policy):
    d = gate(proposal(action="open_dispute"), [], policy)
    assert (d.action, d.runs, d.reply, d.escalated, d.failures) == ("open_dispute", "now", "none", False, [])


def test_needs_person_runs_after_approval(policy):
    for action in ["offer_payment_plan", "update_contact"]:
        assert gate(proposal(action=action), [], policy).runs == "after_approval"


def test_a_draft_reply_always_waits_for_a_person(policy):
    d = gate(proposal(action="reply_only", draft_reply="Thanks, we'll check."), [], policy)
    assert (d.runs, d.reply) == ("now", "queued_for_approval")


def test_proposed_escalation_is_escalated(policy):
    d = gate(proposal(action="escalate"), [], policy)
    assert (d.action, d.runs, d.escalated) == ("escalate", "now", True)
