"""The shapes the engine works with.

Three kinds of thing, kept apart on purpose (the brief's section 3):

- the POLICY: how often, through which channel, with which rules. Data, edited
  by operations, versioned (`Policy`, loaded from policies.json);
- the CASE: where one invoice is right now. Data, written by the running
  system (`Case`, one row in SQLite);
- the ENGINE: how a case is processed. Code, in engine.py and rules.py.

Plus the two things the model returns (`Draft`, `InboundIntent`) and the one
thing the rules return (a `Decision`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Status = Literal["active", "paused", "disputed", "awaiting_approval", "settled", "escalated"]
Channel = Literal["email", "sms", "sms+email"]
Tone = Literal["friendly", "formal", "firm", "final"]


# ---------------------------------------------------------------------------
# The policy: cadence as configuration
# ---------------------------------------------------------------------------

class Stage(BaseModel):
    """One row of the cadence. `day` is relative to the invoice due date:
    -2 is two days before it, 21 is three weeks after."""

    name: str
    day: int
    channel: Channel
    tone: Tone
    requires_human: bool = False


class QuietHours(BaseModel):
    """No messages between start and end, in the customer's local time.
    The window may wrap midnight (21:00 to 08:00)."""

    start: str = "21:00"
    end: str = "08:00"


class Policy(BaseModel):
    tenant: str
    name: str = ""
    version: int
    stages: list[Stage]
    quiet_hours: QuietHours = QuietHours()
    max_contacts_per_week: int = 2
    weekend_pause: bool = True

    @model_validator(mode="after")
    def _sane(self) -> "Policy":
        names = [s.name for s in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")
        days = [s.day for s in self.stages]
        if days != sorted(days):
            raise ValueError("stages must be in day order")
        if self.version < 1:
            raise ValueError("version starts at 1")
        return self


# ---------------------------------------------------------------------------
# What the model returns
# ---------------------------------------------------------------------------

class Draft(BaseModel):
    """A reminder the model proposes. Code checks the amount before it is
    sent, and a person approves it when the stage says so."""

    subject: str
    body: str


Intent = Literal["payment_claim", "promise_to_pay", "dispute", "question", "stop_contact", "other"]


class InboundIntent(BaseModel):
    """What a customer's reply means, as the model reads it. The model only
    classifies: nothing here changes a balance."""

    intent: Intent
    # ISO date (YYYY-MM-DD) when the customer names one, else null. A string,
    # not a date: strict structured output has no date type, and a bad string
    # is handled in code rather than failing the whole reply.
    promised_date: str | None
    disputed_amount_usd: float | None
    summary: str
    confidence: float


# ---------------------------------------------------------------------------
# The case: where one invoice is right now
# ---------------------------------------------------------------------------

class Case(BaseModel):
    case_id: str
    tenant: str
    customer_name: str
    customer_email: str
    customer_tz: str  # IANA name; quiet hours and weekends are judged in it
    invoice_id: str
    invoice_due: date  # "D": every stage's day counts from here
    amount_usd: float
    balance_usd: float
    status: Status = "active"
    # stage name -> "sent" | "skipped". A stage in here is never considered again.
    stage_history: dict[str, str] = Field(default_factory=dict)
    next_due_at: datetime | None = None  # when the scheduler should wake this case
    claimed_at: datetime | None = None  # wall time a worker took it (a lease)
    paused_until: datetime | None = None
    pending_stage: str | None = None  # waiting for a person's yes or no
    pending_draft: Draft | None = None  # the text they are asked to approve
    approved_stage: str | None = None  # the yes, consumed by the next send
    policy_version: int | None = None  # the version last applied to this case
    owner: str | None = None  # the person who took it (dispute, escalation)
    updated_at: datetime | None = None

    @property
    def open(self) -> bool:
        return self.status not in ("settled", "escalated")


# ---------------------------------------------------------------------------
# What the rules decide (rules.py), one of these per pass
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    kind: Literal["send", "defer", "wait", "park", "settle", "escalate"]
    stage: Stage | None = None
    until: datetime | None = None
    reason: str | None = None
    skipped: list[str] = field(default_factory=list)  # stale stages passed over

    def describe(self) -> str:
        if self.kind == "send":
            return f"send {self.stage.name}"
        if self.kind == "defer":
            return f"defer {self.stage.name} until {self.until.isoformat()} ({self.reason})"
        if self.kind == "wait":
            return "wait" + (f" until {self.until.isoformat()}" if self.until else " for a person")
        if self.kind == "park":
            return f"park {self.stage.name} for approval"
        if self.kind == "settle":
            return "settle"
        return f"escalate ({self.reason})"
