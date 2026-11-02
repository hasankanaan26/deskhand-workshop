"""Offline fixtures: no model, ever. The real data files, the real policy."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import data  # noqa: E402
import policy as policy_module  # noqa: E402
from models import Account, Invoice, LedgerPayment, Proposal, ReplyReading  # noqa: E402

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)  # the set's fixed "now", a Monday


def reading(**fields) -> ReplyReading:
    """A ReplyReading with quiet defaults; pass only what the test is about."""
    base = dict(intent="question", other_intents=[], churn_risk=False, invoice_refs=[], payment_reference=None,
                amounts_usd=[], promised_date=None, return_date=None, forward_to_email=None, disputed_items=[],
                evidence=["hello"], summary="scripted", confidence=0.9)
    base.update(fields)
    return ReplyReading(**base)


def proposal(**fields) -> Proposal:
    base = dict(action="reply_only", invoice_ids=[], pause_until=None, instalments=[], new_contact_email=None,
                draft_reply=None, reason="scripted")
    base.update(fields)
    return Proposal(**base)


def invoice(invoice_id: str, balance: float, status: str = "open") -> Invoice:
    return Invoice(invoice_id=invoice_id, status=status, issued="2026-10-07", due="2026-11-06",
                   amount_usd=balance, balance_usd=balance if status == "open" else 0.0, items=[])


def account(*invoices: Invoice, payments: list[LedgerPayment] | None = None) -> Account:
    return Account(account_id="A-99", name="Test Co", contact_email="ap@test.example",
                   invoices=list(invoices), unmatched_payments=payments or [])


@pytest.fixture()
def policy():
    return policy_module.load()


@pytest.fixture()
def accounts():
    found, now = data.load_accounts()
    assert now == NOW
    return found
