"""The labelled set: every row is well formed, and `expected` is what the
harness really does with the gold reading and the gold proposal."""
from __future__ import annotations

from collections import Counter
from typing import get_args

import pytest
from conftest import NOW

import data
from models import Intent

REPLIES = data.load_replies()  # validates every row against the models


def squash(text: str) -> str:
    return " ".join(text.lower().split())


def test_thirty_replies_with_unique_ids():
    assert [r.id for r in REPLIES] == [f"R-{n:02d}" for n in range(1, 31)]


def test_every_intent_at_least_three_times():
    counts = Counter(r.gold_reading.intent for r in REPLIES)
    assert {i: counts[i] for i in get_args(Intent) if counts[i] < 3} == {}


@pytest.mark.parametrize("reply", REPLIES, ids=lambda r: r.id)
def test_evidence_is_in_the_body(reply):
    for quote in reply.gold_reading.evidence:
        assert squash(quote) in squash(reply.body), quote


@pytest.mark.parametrize("reply", REPLIES, ids=lambda r: r.id)
def test_gold_runs_to_expected(reply, accounts, policy):
    # Imported here so the tests above still run while these files are stubs.
    from check import check
    from gate import gate
    from match import match

    acct = accounts[reply.account_id]
    reading, proposal = reply.gold_reading, reply.gold_proposal
    found = match(reading, acct)
    failures = check(reply.body, reading, found, proposal, acct, policy, NOW)
    d = gate(proposal, failures, policy)
    got = {"action": d.action, "runs": d.runs, "reply": d.reply, "escalated": d.escalated,
           "failure_codes": [f.code for f in failures]}
    assert got == reply.expected.model_dump()
