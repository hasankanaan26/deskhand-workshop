"""The shapes triage works with.

Three groups, kept apart on purpose:

- what the MODEL returns: a `ReplyReading` (step 1, read) and a `Proposal`
  (step 3, propose). Both go to the provider as strict JSON schemas, so every
  field is typed, required and listed: no `dict`, no defaults, extra="forbid";
- what CODE returns: a `Match` (step 2), `CheckFailure`s (step 4) and a
  `Disposition` (step 5);
- the DATA files: accounts with their invoices and unmatched payments, and
  the labelled replies in data/replies.json.

Dates are ISO strings (YYYY-MM-DD), never `date`: strict structured output
has no date type, and a bad string ("next week", "2026-13-01") is caught by
a check in code instead of failing the whole reply. The data files use the
same strings so a gold label looks exactly like a model's answer.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "already_paid", "promise_to_pay", "partial_payment_request", "dispute",
    "out_of_office", "wrong_contact", "stop_contact", "question", "other",
]

Action = Literal[
    "record_payment_claim", "pause_for_promise", "offer_payment_plan", "open_dispute",
    "snooze_until_return", "update_contact", "stop_contact", "reply_only", "escalate", "no_action",
]

MatchMethod = Literal["invoice_ref", "payment_reference", "amount", "only_open_invoice", "none"]


class Strict(BaseModel):
    """Unknown fields are an error, here and in the JSON schema the provider sees."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# What the model returns
# ---------------------------------------------------------------------------

class DisputedItem(Strict):
    item: str
    reason: str


class ReplyReading(Strict):
    """What a reply says, as the model reads it (step 1). Reading only:
    nothing here picks an invoice or an action."""

    intent: Intent  # the main one
    other_intents: list[Intent]  # also present, may be empty
    # Angry, or threatening to leave. A flag, not an intent: "a dispute, and
    # furious" is one reply, and it is still a dispute.
    churn_risk: bool
    invoice_refs: list[str]  # invoice numbers as the customer wrote them
    payment_reference: str | None
    amounts_usd: list[float]
    promised_date: str | None  # ISO date
    return_date: str | None  # ISO date, out of office
    forward_to_email: str | None  # wrong contact
    disputed_items: list[DisputedItem]
    evidence: list[str]  # short verbatim quotes from the reply that support the intent
    summary: str
    confidence: float  # 0..1, self-reported: not calibrated


class Instalment(Strict):
    due: str  # ISO date
    amount_usd: float


class Proposal(Strict):
    """What the model proposes to do (step 3). A proposal: check.py and
    gate.py decide whether it runs, now or after a person's yes."""

    action: Action
    invoice_ids: list[str]
    pause_until: str | None  # ISO date: the promise date, or the return date
    instalments: list[Instalment]
    new_contact_email: str | None
    draft_reply: str | None
    reason: str


# ---------------------------------------------------------------------------
# What code returns
# ---------------------------------------------------------------------------

class Match(Strict):
    invoice_ids: list[str]
    method: MatchMethod
    ambiguous: bool
    unknown_refs: list[str]  # refs the customer wrote that are not this customer's open invoices


class CheckFailure(Strict):
    code: str
    detail: str


class Disposition(Strict):
    action: Action
    runs: Literal["now", "after_approval"]
    reply: Literal["none", "queued_for_approval"]
    failures: list[CheckFailure]
    escalated: bool


# ---------------------------------------------------------------------------
# The data files
# ---------------------------------------------------------------------------

class Item(Strict):
    item_ref: str
    description: str
    amount_usd: float


class Invoice(Strict):
    invoice_id: str
    status: Literal["open", "closed"]
    issued: str
    due: str
    amount_usd: float
    balance_usd: float
    items: list[Item]


class LedgerPayment(Strict):
    """Money that arrived but is not yet applied to an invoice. The bank's
    reference is free text; invoice_hint is our best guess, and can be wrong."""

    payment_id: str
    received: str
    amount_usd: float
    reference: str
    invoice_hint: str | None


class Account(Strict):
    account_id: str
    name: str
    contact_email: str
    invoices: list[Invoice]
    unmatched_payments: list[LedgerPayment]

    def open_invoices(self) -> list[Invoice]:
        return [i for i in self.invoices if i.status == "open"]


class Reply(Strict):
    """One customer email, as it arrives."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    account_id: str
    from_: str = Field(alias="from")  # `from` is a Python keyword
    subject: str
    body: str
    received_at: str


class Expected(Strict):
    """What match -> check -> gate produce on the gold labels."""

    action: Action
    runs: Literal["now", "after_approval"]
    reply: Literal["none", "queued_for_approval"]
    escalated: bool
    failure_codes: list[str]


class LabelledReply(Reply):
    """A reply plus what a careful person would read and propose. Some gold
    proposals break the policy on purpose: `expected` then shows the escalation."""

    hard_because: str
    gold_reading: ReplyReading
    gold_proposal: Proposal
    expected: Expected
