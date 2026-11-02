"""Step 5, code: what runs now, and what waits for a person.

Any check failure: escalate now, send nothing. A person gets the reply, the
reading, the proposal and the failures, and decides.

No failures: the proposed action runs now, unless the policy lists it under
needs_person (it can't be taken back), in which case it runs after a
person's yes. A draft reply is never sent from here: every customer-facing
reply waits for a person, because a sent email can't be recalled and free
text can promise things code can't see.
"""
from __future__ import annotations

from models import CheckFailure, Disposition, Proposal
from policy import TriagePolicy


def gate(proposal: Proposal, failures: list[CheckFailure], policy: TriagePolicy) -> Disposition:
    if failures:
        return Disposition(action="escalate", runs="now", reply="none", failures=failures, escalated=True)
    return Disposition(
        action=proposal.action,
        runs="after_approval" if proposal.action in policy.needs_person else "now",
        reply="queued_for_approval" if proposal.draft_reply else "none",
        failures=[],
        escalated=proposal.action == "escalate",
    )
