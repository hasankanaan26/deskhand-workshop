"""The whole workflow: read, match, propose, check, gate. Always in that order.

    read     model   reply text -> ReplyReading
    match    code    reading + account -> Match
    propose  model   reading + matched invoices + allowed actions -> Proposal
    check    code    -> list[CheckFailure]
    gate     code    -> Disposition

The model proposes; the harness disposes. The order is fixed in this file:
code never asks the model what to do next, and a step's output only feeds
the steps after it. The two model steps are arguments, so the tests pass
fakes and no test ever calls a real model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import policy as policy_module
from check import check
from gate import gate
from match import match
from models import Account, CheckFailure, Disposition, Match, Proposal, Reply, ReplyReading
from policy import TriagePolicy


@dataclass
class TriageResult:
    reading: ReplyReading
    match: Match
    proposal: Proposal
    failures: list[CheckFailure]
    disposition: Disposition
    usage: list[Any] = field(default_factory=list)  # llm.Usage per model call, None from a fake


def _real_read(reply, now):
    import read
    return read.read(reply, now)


def _real_propose(reading, invoices, allowed, policy, now):
    import propose
    return propose.propose(reading, invoices, allowed, policy, now)


def triage(
    reply: Reply,
    account: Account,
    now: datetime,
    read_fn: Callable = _real_read,
    propose_fn: Callable = _real_propose,
    policy: TriagePolicy | None = None,
) -> TriageResult:
    """read_fn(reply, now) and propose_fn(reading, invoices, allowed, policy, now)
    each return (model object, usage)."""
    policy = policy or policy_module.load()

    reading, read_usage = read_fn(reply, now)
    found = match(reading, account)
    invoices = [i for i in account.open_invoices() if i.invoice_id in found.invoice_ids]
    allowed = policy.allowed_actions[reading.intent]
    proposal, propose_usage = propose_fn(reading, invoices, allowed, policy, now)
    failures = check(reply.body, reading, found, proposal, account, policy, now)
    disposition = gate(proposal, failures, policy)
    return TriageResult(reading, found, proposal, failures, disposition, [read_usage, propose_usage])
