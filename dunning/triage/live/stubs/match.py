"""Step 2, code: which of this customer's invoices is the reply about?

TO BUILD (prompt 1). This docstring is the spec; tests/test_match.py is the
acceptance test, and tests/test_data.py runs your code on all 30 replies.

The model read the reply (step 1) and wrote down the invoice numbers, the
payment reference and the amounts it saw, in a ReplyReading. Code looks
them up in the Account (models.py). The model never picks an invoice.

normalize_ref(ref) -> str
    Invoice numbers as customers write them: ignore case, spaces and "#",
    add a missing hyphen, add "INV-" to a bare number. "inv2041",
    "INV 2041", "#2041" and "2041" all become "INV-2041". Anything else
    comes back upper-cased with the spaces taken out.

match(reading, account) -> Match
    Normalize every ref in reading.invoice_refs (keep the customer's order,
    drop repeats). Refs that are not one of this account's OPEN invoices
    (another customer's, a closed one, a made-up one) go in unknown_refs,
    whichever rule below fires. Then the first rule that fires wins:

    1. Some refs are this customer's open invoices: those, method "invoice_ref".
    2. Else reading.payment_reference equals the reference of one of the
       account's unmatched_payments (ignore case and spaces), and that
       payment's invoice_hint is one of the account's open invoices: that
       invoice, method "payment_reference". A payment with no hint, or a hint
       that isn't open, falls through to rule 3.
    3. Else the open invoices whose balance_usd equals one of
       reading.amounts_usd (within 0.005). Exactly one: method "amount".
       More than one: all of them, method "amount", ambiguous=True.
    4. Else the account has exactly one open invoice: that one, method
       "only_open_invoice".
    5. Else no invoices, method "none", ambiguous=True.

    ambiguous is False in every other case.
"""
from __future__ import annotations

from models import Account, Match, ReplyReading


def normalize_ref(ref: str) -> str:
    raise NotImplementedError("prompt 1: see the docstring at the top of match.py")


def match(reading: ReplyReading, account: Account) -> Match:
    raise NotImplementedError("prompt 1: see the docstring at the top of match.py")
