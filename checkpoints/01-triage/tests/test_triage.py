"""Offline tests for the triage node.

llm.structured_completion is monkeypatched — no network, no API key, no cost.
"""
import pytest

import errors
import llm
import triage
from models import TriageResult

# Faking the LLM works because llm.py is the only module that talks to the
# provider: replace llm.structured_completion and triage never knows. Each
# fake returns or raises what the real one can, so these tests pin
# down the repair policy (what gets retried, how often, with what message)
# without any model in the loop. conftest.py keeps the log writes in tmp_path.

# Same shape as a record in shared/data/tickets.json.
TICKET = {
    "ticket_id": "T-1001",
    "customer_name": "Dana Whitfield",
    "order_id": "O-48291",
    "channel": "email",
    "messages": [{"from": "customer", "text": "Cracked case, want a refund or replacement."}],
    "tags": ["happy_path"],
}

RESULT = TriageResult(
    category="refund_request", priority="medium", sentiment="frustrated", confidence=0.85
)
USAGE = llm.Usage(prompt_tokens=200, completion_tokens=30, cost_usd=0.0005)


def test_valid_first_try(monkeypatch):
    """The happy path costs one call and returns the model's result as is."""
    calls = []

    def fake_completion(messages, response_model, **kwargs):
        # list(...) snapshots the conversation: triage appends the repair turn
        # to the same list object, so storing the reference would rewrite history.
        calls.append(list(messages))
        assert response_model is TriageResult
        return RESULT, USAGE

    monkeypatch.setattr(llm, "structured_completion", fake_completion)

    result, cost_usd = triage.classify_ticket(TICKET, request_id="req-1")

    assert result == RESULT
    assert cost_usd == pytest.approx(USAGE.cost_usd)
    assert len(calls) == 1


def test_malformed_then_repaired(monkeypatch):
    """One malformed answer triggers one repair re-prompt that quotes the error."""
    calls = []

    def fake_completion(messages, response_model, **kwargs):
        calls.append(list(messages))
        if len(calls) == 1:
            raise errors.DeskhandError(
                errors.LLM_MALFORMED_OUTPUT, "confidence was the string 'high'"
            )
        return RESULT, USAGE

    monkeypatch.setattr(llm, "structured_completion", fake_completion)

    result, cost_usd = triage.classify_ticket(TICKET, request_id="req-2")

    assert result == RESULT
    # Two calls, one call's cost: the malformed attempt raised with no usage,
    # so classify_ticket had nothing to add for it.
    assert cost_usd == pytest.approx(USAGE.cost_usd)
    assert len(calls) == 2
    # The repair re-prompt tells the model exactly what was wrong.
    repair_prompt = calls[1][-1]
    assert repair_prompt["role"] == "user"
    assert "confidence was the string 'high'" in repair_prompt["content"]


def test_repair_exhausted(monkeypatch):
    """The repair is bounded: after it fails, triage stops with a typed error
    instead of looping like v1's `while True`."""
    call_count = 0

    def fake_completion(messages, response_model, **kwargs):
        nonlocal call_count
        call_count += 1
        raise errors.DeskhandError(errors.LLM_MALFORMED_OUTPUT, "still not valid")

    monkeypatch.setattr(llm, "structured_completion", fake_completion)

    with pytest.raises(errors.DeskhandError) as excinfo:
        triage.classify_ticket(TICKET, request_id="req-3")

    assert excinfo.value.internal_error_code == errors.REPAIR_EXHAUSTED
    assert call_count == 2  # the original attempt + exactly one repair


def test_non_repairable_errors_propagate(monkeypatch):
    """A rate limit is not the model's fault, so no re-prompt: the code surfaces."""
    def fake_completion(messages, response_model, **kwargs):
        raise errors.DeskhandError(errors.LLM_RATE_LIMITED, "429 from provider")

    monkeypatch.setattr(llm, "structured_completion", fake_completion)

    with pytest.raises(errors.DeskhandError) as excinfo:
        triage.classify_ticket(TICKET, request_id="req-4")

    # Re-prompting can't fix a rate limit — the error surfaces with its own code.
    assert excinfo.value.internal_error_code == errors.LLM_RATE_LIMITED
