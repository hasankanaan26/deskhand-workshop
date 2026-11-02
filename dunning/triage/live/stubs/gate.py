"""Step 5, code: what runs now, and what waits for a person.

TO BUILD (prompt 3). This docstring is the spec; tests/test_gate.py is the
acceptance test, and tests/test_data.py and tests/test_pipeline.py run your
code on all 30 replies.

gate(proposal, failures, policy) -> Disposition

    Any failures: Disposition(action="escalate", runs="now", reply="none",
    failures=failures, escalated=True). Nothing is sent; a person gets the
    reply, the reading, the proposal and the failures.

    No failures:
      action     proposal.action
      runs       "after_approval" when the action is in policy.needs_person
                 (it can't be taken back: a payment plan is a commitment, a
                 new contact address gets account details), else "now"
                 (reversible: it runs, is logged, and a person can undo it)
      reply      "queued_for_approval" when proposal.draft_reply is non-empty,
                 else "none". Every customer-facing reply waits for a person:
                 a sent email can't be recalled, and free text can promise
                 things code can't see.
      failures   []
      escalated  True only when the action is "escalate"
"""
from __future__ import annotations

from models import CheckFailure, Disposition, Proposal
from policy import TriagePolicy


def gate(proposal: Proposal, failures: list[CheckFailure], policy: TriagePolicy) -> Disposition:
    raise NotImplementedError("prompt 3: see the docstring at the top of gate.py")
