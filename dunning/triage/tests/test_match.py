"""match.py: the five rules in order, ref normalization, unknown refs, ambiguity."""
from __future__ import annotations

import pytest
from conftest import account, invoice, reading

from match import match, normalize_ref
from models import LedgerPayment


def pay(reference: str, amount: float, hint: str | None) -> LedgerPayment:
    return LedgerPayment(payment_id="PAY-1", received="2026-11-13", amount_usd=amount,
                         reference=reference, invoice_hint=hint)


TWO = account(invoice("INV-2041", 240.0), invoice("INV-2049", 480.0), invoice("INV-1990", 150.0, "closed"),
              payments=[pay("ACME-OCT-240", 240.0, "INV-2041"), pay("NO HINT 9", 99.0, None)])


@pytest.mark.parametrize("written", ["INV-2041", "inv-2041", "inv2041", "INV 2041", " Inv 2041 ", "#2041", "2041"])
def test_refs_are_normalized(written):
    assert normalize_ref(written) == "INV-2041"


def test_rule_1_invoice_ref():
    m = match(reading(invoice_refs=["inv 2049"]), TWO)
    assert (m.invoice_ids, m.method, m.ambiguous, m.unknown_refs) == (["INV-2049"], "invoice_ref", False, [])


def test_rule_1_keeps_unknown_refs_alongside_a_match():
    m = match(reading(invoice_refs=["INV-2041", "INV-1990", "INV-9999"]), TWO)
    assert m.invoice_ids == ["INV-2041"] and m.method == "invoice_ref"
    assert m.unknown_refs == ["INV-1990", "INV-9999"]  # a closed one, a made-up one


def test_rule_1_wins_over_a_payment_reference():
    m = match(reading(invoice_refs=["INV-2049"], payment_reference="ACME-OCT-240"), TWO)
    assert m.invoice_ids == ["INV-2049"] and m.method == "invoice_ref"


def test_rule_2_payment_reference_ignores_case_and_spaces():
    m = match(reading(payment_reference="acme-oct-240 "), TWO)
    assert (m.invoice_ids, m.method, m.ambiguous) == (["INV-2041"], "payment_reference", False)


def test_rule_2_payment_without_a_hint_falls_through():
    m = match(reading(payment_reference="NO HINT 9"), TWO)
    assert m.method == "none" and m.ambiguous


def test_only_unknown_refs_fall_through_and_are_kept():
    m = match(reading(invoice_refs=["INV-1990"], amounts_usd=[480.0]), TWO)
    assert m.invoice_ids == ["INV-2049"] and m.method == "amount" and m.unknown_refs == ["INV-1990"]


def test_rule_3_amount_matches_one_balance():
    m = match(reading(amounts_usd=[480.004]), TWO)
    assert (m.invoice_ids, m.method, m.ambiguous) == (["INV-2049"], "amount", False)


def test_rule_3_two_invoices_with_that_balance_is_ambiguous():
    twins = account(invoice("INV-2068", 640.0), invoice("INV-2069", 640.0))
    m = match(reading(amounts_usd=[640.0]), twins)
    assert m.method == "amount" and m.ambiguous and m.invoice_ids == ["INV-2068", "INV-2069"]


def test_rule_3_ignores_closed_invoices():
    m = match(reading(amounts_usd=[150.0]), TWO)
    assert m.method == "none"


def test_rule_4_only_open_invoice():
    one = account(invoice("INV-2052", 95.0), invoice("INV-1975", 95.0, "closed"))
    m = match(reading(), one)
    assert (m.invoice_ids, m.method, m.ambiguous) == (["INV-2052"], "only_open_invoice", False)


def test_rule_5_none_is_ambiguous():
    m = match(reading(), TWO)
    assert (m.invoice_ids, m.method, m.ambiguous, m.unknown_refs) == ([], "none", True, [])


def test_another_customers_invoice_is_unknown(accounts):
    # INV-2047 is Globex's. Initech's only open invoice still matches by rule 4.
    m = match(reading(invoice_refs=["INV-2047"]), accounts["A-03"])
    assert m.unknown_refs == ["INV-2047"] and m.invoice_ids == ["INV-2052"] and m.method == "only_open_invoice"
