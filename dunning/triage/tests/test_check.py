"""check.py: one passing and one failing case for every code."""
from __future__ import annotations

from conftest import NOW, account, invoice, proposal, reading

from check import check
from models import Instalment, Match

ACCOUNT = account(invoice("INV-2041", 240.0), invoice("INV-2049", 480.0))
TEXT = "Hi, we will pay INV-2041 on 27 November. Write to new.ap@acme.example please."
MATCHED = Match(invoice_ids=["INV-2041"], method="invoice_ref", ambiguous=False, unknown_refs=[])


def codes(policy, r=None, p=None, m=MATCHED, text=TEXT) -> list[str]:
    r = r or reading(evidence=["we will pay INV-2041"])
    p = p or proposal()
    return [f.code for f in check(text, r, m, p, ACCOUNT, policy, NOW)]


def promise(**fields):
    base = dict(action="pause_for_promise", invoice_ids=["INV-2041"], pause_until="2026-11-27")
    base.update(fields)
    return proposal(**base)


PROMISE = dict(intent="promise_to_pay", evidence=["we will pay INV-2041"])


def test_a_good_proposal_has_no_failures(policy):
    assert codes(policy, reading(**PROMISE), promise()) == []


def test_action_not_allowed(policy):
    assert "action_not_allowed" not in codes(policy, reading(**PROMISE), promise())
    assert codes(policy, reading(**PROMISE), proposal(action="record_payment_claim", invoice_ids=["INV-2041"])) \
        == ["action_not_allowed"]


def test_evidence_not_in_reply(policy):
    # Case and whitespace don't count; words do.
    assert codes(policy, reading(evidence=["WE  WILL\npay inv-2041"])) == []
    assert codes(policy, reading(evidence=["we already paid"])) == ["evidence_not_in_reply"]
    assert codes(policy, reading(evidence=[])) == ["evidence_not_in_reply"]


def test_low_confidence(policy):
    assert codes(policy, reading(evidence=["INV-2041"], confidence=0.6)) == []
    assert codes(policy, reading(evidence=["INV-2041"], confidence=0.59)) == ["low_confidence"]


def test_invoice_not_matched(policy):
    assert codes(policy, reading(**PROMISE), promise(invoice_ids=["INV-2041"])) == []
    # An invoice match didn't find.
    assert codes(policy, reading(**PROMISE), promise(invoice_ids=["INV-2049"])) == ["invoice_not_matched"]
    # An invoice action on an ambiguous or empty match.
    vague = Match(invoice_ids=[], method="none", ambiguous=True, unknown_refs=[])
    assert codes(policy, reading(**PROMISE), promise(invoice_ids=[]), m=vague) == ["invoice_not_matched"]
    # Not an invoice action: an empty match is fine.
    assert codes(policy, reading(evidence=["INV-2041"]), proposal(action="reply_only"), m=vague) == []


def test_unknown_invoice_ref(policy):
    assert codes(policy) == []
    stray = Match(invoice_ids=["INV-2041"], method="invoice_ref", ambiguous=False, unknown_refs=["INV-2047"])
    assert codes(policy, m=stray) == ["unknown_invoice_ref"]


def test_bad_promise_date(policy):
    assert codes(policy, reading(**PROMISE), promise(pause_until="2026-12-16")) == []  # today + 30
    for bad in [None, "next week", "2026-11-15", "2026-12-17"]:
        assert codes(policy, reading(**PROMISE), promise(pause_until=bad)) == ["bad_promise_date"], bad


def plan(*parts, invoice_ids=("INV-2041",)):
    return proposal(action="offer_payment_plan", invoice_ids=list(invoice_ids),
                    instalments=[Instalment(due=d, amount_usd=a) for d, a in parts])


PLAN = dict(intent="partial_payment_request", evidence=["INV-2041"])


def test_bad_plan(policy):
    assert codes(policy, reading(**PLAN), plan(("2026-11-23", 120.0), ("2027-01-15", 120.0))) == []
    bad_plans = [
        plan(),                                                         # nothing
        plan(*[("2026-11-%d" % d, 60.0) for d in range(23, 27)]),       # four instalments
        plan(("2026-11-23", 120.0), ("2027-01-16", 120.0)),             # past today + 60
        plan(("2026-11-15", 120.0), ("2026-11-23", 120.0)),             # in the past
        plan(("2026-12-23", 120.0), ("2026-11-23", 120.0)),             # not ascending
        plan(("soon", 120.0), ("2026-11-23", 120.0)),                   # not a date
        plan(("2026-11-23", 100.0), ("2026-12-23", 100.0)),             # adds up to 200, owes 240
    ]
    for p in bad_plans:
        assert codes(policy, reading(**PLAN), p) == ["bad_plan"], p.instalments


def snooze(until):
    return proposal(action="snooze_until_return", pause_until=until)


OOO = dict(intent="out_of_office", evidence=["INV-2041"])


def test_bad_return_date(policy):
    assert codes(policy, reading(**OOO), snooze("2026-12-07")) == []  # today + 21
    for bad in [None, "Monday", "2026-11-10", "2026-12-08"]:
        assert codes(policy, reading(**OOO), snooze(bad)) == ["bad_return_date"], bad


def contact(email):
    return proposal(action="update_contact", new_contact_email=email)


WRONG = dict(intent="wrong_contact", evidence=["Write to new.ap@acme.example"])


def test_bad_contact(policy):
    assert codes(policy, reading(**WRONG), contact("new.ap@acme.example")) == []
    for bad in [None, "", "new.ap at acme", "other@acme.example"]:
        assert codes(policy, reading(**WRONG), contact(bad)) == ["bad_contact"], bad


def test_reply_amount_mismatch(policy):
    ok = proposal(draft_reply="Your balance of $240.00 on INV-2041.")
    assert codes(policy, p=ok) == []
    wrong = proposal(draft_reply="We've applied a $20 credit, so you owe $220.00.")
    assert codes(policy, p=wrong) == ["reply_amount_mismatch"]
    # An unmatched invoice's balance is not allowed either.
    assert codes(policy, p=proposal(draft_reply="And $480.00 on INV-2049.")) == ["reply_amount_mismatch"]


def test_reply_amount_allows_instalments(policy):
    p = plan(("2026-11-23", 120.0), ("2026-12-23", 120.0))
    p.draft_reply = "Two payments of $120.00, $240.00 in all."
    assert codes(policy, reading(**PLAN), p) == []


def test_reply_to_auto_reply(policy):
    assert codes(policy, reading(**OOO), snooze("2026-11-23")) == []
    p = snooze("2026-11-23")
    p.draft_reply = "Thanks, we'll be in touch when you're back."
    assert codes(policy, reading(**OOO), p) == ["reply_to_auto_reply"]


def test_checks_for_other_actions_are_skipped(policy):
    # A reply_only proposal with no date, no plan and no address fails nothing.
    assert codes(policy, p=proposal(action="reply_only", pause_until=None, new_contact_email=None)) == []
