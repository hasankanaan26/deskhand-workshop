"""pipeline.triage with fake model steps: the order is fixed, code has the last word."""
from __future__ import annotations

from conftest import NOW, proposal

import data
from pipeline import triage

REPLIES = {r.id: r for r in data.load_replies()}


def fakes(reply, proposal_override=None):
    """read_fn and propose_fn that return the gold labels and record their calls."""
    calls = []

    def read_fn(r, now):
        calls.append(("read", r.id))
        return reply.gold_reading, None

    def propose_fn(reading, invoices, allowed, policy, now):
        calls.append(("propose", [i.invoice_id for i in invoices], list(allowed)))
        return proposal_override or reply.gold_proposal, None

    return read_fn, propose_fn, calls


def test_two_model_calls_in_order_with_facts_from_code(accounts):
    r = REPLIES["R-09"]  # Hooli's fair payment plan
    read_fn, propose_fn, calls = fakes(r)
    result = triage(r, accounts[r.account_id], NOW, read_fn=read_fn, propose_fn=propose_fn)
    assert calls == [("read", "R-09"),
                     ("propose", ["INV-2080"], ["offer_payment_plan", "escalate"])]
    assert result.match.method == "invoice_ref"
    assert result.disposition.action == "offer_payment_plan"
    assert result.disposition.runs == "after_approval"
    assert result.usage == [None, None]


def test_the_harness_overrules_an_obedient_model(accounts):
    # The prompt injection, read by a model that believed it.
    r = REPLIES["R-30"]
    obeyed = r.gold_reading.model_copy(update={"intent": "dispute"})
    paid = proposal(action="record_payment_claim", invoice_ids=["INV-2078"],
                    draft_reply="Done: INV-2078 is marked paid and your balance is $0.00.")

    def read_fn(reply, now):
        return obeyed, None

    def propose_fn(*args):
        return paid, None

    result = triage(r, accounts[r.account_id], NOW, read_fn=read_fn, propose_fn=propose_fn)
    codes = [f.code for f in result.failures]
    assert codes == ["action_not_allowed", "reply_amount_mismatch"]
    assert (result.disposition.action, result.disposition.reply) == ("escalate", "none")


def test_every_gold_reply_through_the_pipeline(accounts):
    for r in REPLIES.values():
        read_fn, propose_fn, _ = fakes(r)
        d = triage(r, accounts[r.account_id], NOW, read_fn=read_fn, propose_fn=propose_fn).disposition
        assert (d.action, d.runs, d.reply) == (r.expected.action, r.expected.runs, r.expected.reply), r.id
