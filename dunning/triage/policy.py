"""The triage policy is data: triage_policy.json, edited by operations.

It says which actions each intent may lead to, which actions wait for a
person, and the limits the checks hold proposals to. The numbers are this
course's choices. Code reads them; nothing in check.py or gate.py hard-codes
a limit or an action list.

Two actions need a person, because they can't be taken back:
offer_payment_plan (a commitment to the customer) and update_contact
(account details would go to a new address). Everything else is reversible:
it runs now, is logged, and a person can undo it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, ConfigDict, model_validator

from models import Action, Intent

POLICY_PATH = Path(__file__).resolve().parent / "triage_policy.json"


class TriagePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = ""
    confidence_floor: float
    max_promise_days: int
    max_snooze_days: int
    max_instalments: int
    max_plan_days: int
    allowed_actions: dict[Intent, list[Action]]
    needs_person: list[Action]

    @model_validator(mode="after")
    def _every_intent(self) -> "TriagePolicy":
        missing = [i for i in get_args(Intent) if i not in self.allowed_actions]
        if missing:
            raise ValueError(f"allowed_actions has no entry for {missing}")
        return self


def load(path: Path | None = None) -> TriagePolicy:
    raw = json.loads(Path(path or POLICY_PATH).read_text(encoding="utf-8"))
    return TriagePolicy.model_validate(raw)
