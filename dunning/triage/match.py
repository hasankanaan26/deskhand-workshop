"""Step 2, code: which of this customer's invoices is the reply about?

The model read the reply (step 1) and wrote down the invoice numbers, the
payment reference and the amounts it saw. Code looks them up in the
account. The model never picks an invoice: it can't see the ledger, and a
reply can name anything.

Rules, first one that fires wins:

1. invoice_refs that are this customer's OPEN invoices -> method invoice_ref.
2. else a payment_reference found among the account's unmatched incoming
   payments, whose invoice hint is one of its open invoices -> that invoice,
   method payment_reference. (A payment without a usable hint falls through.)
3. else exactly one open invoice whose balance equals one of amounts_usd
   (within half a cent) -> method amount. More than one -> ambiguous.
4. else the customer has exactly one open invoice -> only_open_invoice.
5. else none, ambiguous.

Whatever rule fires, refs that are not this customer's open invoices
(another customer's, a closed one, a made-up one) go in unknown_refs.
"""
from __future__ import annotations

import re

from models import Account, Match, ReplyReading

_INV = re.compile(r"INV-?(\d+)")
_DIGITS = re.compile(r"\d+")


def normalize_ref(ref: str) -> str:
    """'inv2041', 'INV 2041', '#2041', '2041' -> 'INV-2041'. Anything else
    comes back upper-cased with the spaces taken out."""
    s = re.sub(r"[\s#]", "", ref).upper()
    m = _INV.fullmatch(s)
    if m:
        return f"INV-{m.group(1)}"
    if _DIGITS.fullmatch(s):
        return f"INV-{s}"
    return s


def _same_reference(a: str, b: str) -> bool:
    """Bank references: case and spaces don't count."""
    return re.sub(r"\s", "", a).upper() == re.sub(r"\s", "", b).upper()


def match(reading: ReplyReading, account: Account) -> Match:
    open_ids = [i.invoice_id for i in account.open_invoices()]

    # Every ref, normalized, once, in the order the customer wrote them.
    refs: list[str] = []
    for ref in reading.invoice_refs:
        n = normalize_ref(ref)
        if n not in refs:
            refs.append(n)
    found = [r for r in refs if r in open_ids]
    unknown = [r for r in refs if r not in open_ids]

    def result(ids: list[str], method: str, ambiguous: bool = False) -> Match:
        return Match(invoice_ids=ids, method=method, ambiguous=ambiguous, unknown_refs=unknown)

    # 1. The invoice numbers they wrote.
    if found:
        return result(found, "invoice_ref")

    # 2. The bank reference, if we have that payment and it hints at an open invoice.
    if reading.payment_reference:
        for payment in account.unmatched_payments:
            if _same_reference(payment.reference, reading.payment_reference):
                if payment.invoice_hint in open_ids:
                    return result([payment.invoice_hint], "payment_reference")
                break

    # 3. An amount that is exactly one open invoice's balance.
    by_amount = [
        i.invoice_id for i in account.open_invoices()
        if any(abs(i.balance_usd - a) < 0.005 for a in reading.amounts_usd)
    ]
    if len(by_amount) == 1:
        return result(by_amount, "amount")
    if len(by_amount) > 1:
        return result(by_amount, "amount", ambiguous=True)

    # 4. Only one thing it could be.
    if len(open_ids) == 1:
        return result(open_ids, "only_open_invoice")

    # 5. Nothing to go on.
    return result([], "none", ambiguous=True)
