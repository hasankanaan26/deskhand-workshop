"""Error taxonomy for the dunning engine.

A copy of checkpoints/02-service/errors.py with two codes added at the end:
POLICY_INVALID (the policy file does not parse: the case escalates, nothing is
sent) and DRAFT_REJECTED (the model's draft failed the amount check and the
template was used instead; logged, never raised). Every failure carries one of
these internal codes. The code is for engineers (logs, dashboards); the
user-visible message is ALWAYS chosen separately from USER_MESSAGES.
"""
from __future__ import annotations

# Model calls. Only malformed output (truncated, refused, off-schema) is worth
# a repair re-prompt. Rate limits and timeouts already got the SDK's own
# retries; re-prompting cannot fix them, so callers let them propagate.
LLM_MALFORMED_OUTPUT = "LLM_MALFORMED_OUTPUT"
LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
LLM_TIMEOUT = "LLM_TIMEOUT"
# Order API (checkpoint 02 on), kept so llm.py's imports resolve unchanged.
TOOL_ORDER_API_TIMEOUT = "TOOL_ORDER_API_TIMEOUT"
TOOL_ORDER_API_ERROR = "TOOL_ORDER_API_ERROR"
TOOL_ORDER_NOT_FOUND = "TOOL_ORDER_NOT_FOUND"
VALIDATION_SCHEMA_FAILED = "VALIDATION_SCHEMA_FAILED"
VALIDATION_BUSINESS_RULE_FAILED = "VALIDATION_BUSINESS_RULE_FAILED"
REPAIR_EXHAUSTED = "REPAIR_EXHAUSTED"
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
GUARDRAIL_INJECTION_SUSPECTED = "GUARDRAIL_INJECTION_SUSPECTED"
STREAM_DISCONNECTED = "STREAM_DISCONNECTED"
INTERNAL_UNEXPECTED = "INTERNAL_UNEXPECTED"
# Dunning engine only.
# The policy file failed to parse or validate. The engine never guesses a
# cadence: the case escalates to a person and nothing is sent.
POLICY_INVALID = "POLICY_INVALID"
# The model's draft stated an amount that is not the ledger balance. The
# template draft was sent instead. Logged on the audit line, never raised.
DRAFT_REJECTED = "DRAFT_REJECTED"


class DeskhandError(Exception):
    """A typed, taxonomy-carrying failure.

    `message` is internal (safe to log, never shown to customers).
    """

    def __init__(self, internal_error_code: str, message: str) -> None:
        super().__init__(message)
        self.internal_error_code = internal_error_code


_GENERIC = (
    "Something went wrong while processing this case. "
    "It has been routed to a person."
)

USER_MESSAGES: dict[str, str] = {
    LLM_RATE_LIMITED: _GENERIC,
    LLM_TIMEOUT: _GENERIC,
    LLM_MALFORMED_OUTPUT: _GENERIC,
    POLICY_INVALID: (
        "The collections policy for this account could not be read. "
        "No reminder was sent; the case has been routed to a person."
    ),
    DRAFT_REJECTED: (
        "The drafted reminder did not match the ledger and was replaced by "
        "the standard wording."
    ),
    INTERNAL_UNEXPECTED: _GENERIC,
}


def user_message_for(internal_error_code: str | None) -> str:
    """Pick the safe user-visible message for an error code."""
    return USER_MESSAGES.get(internal_error_code or "", _GENERIC)
