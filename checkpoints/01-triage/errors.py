"""Canonical Deskhand error taxonomy.

Every exception the system raises carries one of these codes. The code is for
logs and debugging; what a user sees comes from user_message() — never from
str(exception).
"""

# A code says what the CALLER should do, which is why there are several and
# not one "LLM_ERROR". v1 retried every failure the same way, then shrugged.

# Model calls. Only malformed output (truncated, refused, off-schema) is worth
# a repair re-prompt. Rate limits and timeouts already got the SDK's own
# retries; re-prompting cannot fix them, so callers let them propagate.
LLM_MALFORMED_OUTPUT = "LLM_MALFORMED_OUTPUT"
LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
LLM_TIMEOUT = "LLM_TIMEOUT"
# Order API (checkpoint 02 on), raised after the tool's own retries. NOT_FOUND
# is an answer, not a failure: it is logged and the plan has no order data.
TOOL_ORDER_API_TIMEOUT = "TOOL_ORDER_API_TIMEOUT"
TOOL_ORDER_API_ERROR = "TOOL_ORDER_API_ERROR"
TOOL_ORDER_NOT_FOUND = "TOOL_ORDER_NOT_FOUND"
# Validation. A broken business rule (02 on) is logged and sent back for
# repair; REPAIR_EXHAUSTED ends the run when the bounded repair loop runs out.
# Nothing raises VALIDATION_SCHEMA_FAILED: a schema miss arrives as
# LLM_MALFORMED_OUTPUT from the model call.
VALIDATION_SCHEMA_FAILED = "VALIDATION_SCHEMA_FAILED"
VALIDATION_BUSINESS_RULE_FAILED = "VALIDATION_BUSINESS_RULE_FAILED"
REPAIR_EXHAUSTED = "REPAIR_EXHAUSTED"
# Deliberate stops: the run ends and a human takes the ticket. BUDGET_EXCEEDED
# is agent_bounded.py's step cap in 02 and budget.py's cost cap in 04. The
# injection guard is the provider's content filter from 02, plus Deskhand's
# own input guards in 04. Neither is retried: the same input trips it again.
BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
GUARDRAIL_INJECTION_SUSPECTED = "GUARDRAIL_INJECTION_SUSPECTED"
# The SSE client went away (02 on). Logged, never raised: the run carries on.
STREAM_DISCONNECTED = "STREAM_DISCONNECTED"
# Anything without a code of its own: a bug, a bad API key. The safe default.
INTERNAL_UNEXPECTED = "INTERNAL_UNEXPECTED"


class DeskhandError(Exception):
    """An error carrying a code from the taxonomy above.

    `detail` is internal-only (raw provider messages, validation output).
    """

    def __init__(self, internal_error_code: str, detail: str = "") -> None:
        self.internal_error_code = internal_error_code
        self.detail = detail
        super().__init__(f"{internal_error_code}: {detail}")


# Written for a customer: no codes, no provider text, and each says what
# happens next. str(exception) is provider text written for engineers.
_USER_MESSAGES: dict[str, str] = {
    REPAIR_EXHAUSTED: (
        "We couldn't generate a reliable resolution for this ticket. "
        "It has been routed to a human agent."
    ),
}
_DEFAULT_USER_MESSAGE = (
    "Something went wrong on our side while handling this ticket. "
    "It has been routed to a human agent."
)


def user_message(internal_error_code: str) -> str:
    """The safe, user-visible message for an error code."""
    return _USER_MESSAGES.get(internal_error_code, _DEFAULT_USER_MESSAGE)
