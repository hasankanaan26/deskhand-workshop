"""Canonical Pydantic models for checkpoint 01.

v1 ran json.loads on whatever text came back. Here the Pydantic model IS the
contract: llm.structured_completion hands it to the provider as the output
schema, and a reply that does not fit fails at that one boundary, not three
functions later.
"""
from typing import Literal

from pydantic import BaseModel


class TriageResult(BaseModel):
    """The triage step's structured output: one ticket's classification.

    category, priority and sentiment are closed Literal sets. The JSON schema
    sent to the provider lists the allowed values, so the model cannot answer
    "billing" or "very urgent", and Pydantic re-checks every value on parse.
    """

    category: Literal[
        "refund_request",
        "replacement",
        "shipping_delay",
        "product_defect",
        "account",
        "other",
    ]
    priority: Literal["low", "medium", "high", "urgent"]
    sentiment: Literal["calm", "frustrated", "angry"]
    confidence: float  # 0..1 — the model's self-estimate; business rules act on it later
