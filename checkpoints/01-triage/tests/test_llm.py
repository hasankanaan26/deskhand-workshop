"""Offline tests for the provider boundary in llm.py.

The provider client is replaced with a stub that raises — no network, no API
key, no cost. The point: whatever the SDK throws, what leaves llm.py is a
DeskhandError with a taxonomy code, never a raw provider exception.
"""
import anthropic
import httpx
import httpx2
import openai
import pytest

import errors
import llm
from models import TriageResult

MESSAGES = [{"role": "system", "content": "triage"}, {"role": "user", "content": "hi"}]


def _openai_error(cls, status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid/v1"))
    return cls("boom", response=response, body=None)


# The anthropic SDK here is built on httpx2, so its errors take httpx2 objects.
def _anthropic_error(cls, status):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://example.invalid/v1"))
    return cls("boom", response=response, body=None)


class _RaisingOpenAI:
    """Mirrors client.beta.chat.completions.parse, then raises."""

    def __init__(self, exc):
        exc_ = exc

        class _Completions:
            def parse(self, **kwargs):
                raise exc_

        class _Chat:
            completions = _Completions()

        class _Beta:
            chat = _Chat()

        self.beta = _Beta()


class _RaisingAnthropic:
    """Mirrors client.messages.parse, then raises."""

    def __init__(self, exc):
        exc_ = exc

        class _Messages:
            def parse(self, **kwargs):
                raise exc_

        self.messages = _Messages()


# Swapping `_client` is the whole offline trick: llm.py looks the name up on
# every call, so the stub wins over the lru_cache'd real client. A 401 or 400
# is no one's to retry, so it maps to INTERNAL_UNEXPECTED, never a raw error.
@pytest.mark.parametrize(
    "cls, status, code",
    [
        (openai.RateLimitError, 429, errors.LLM_RATE_LIMITED),
        (openai.AuthenticationError, 401, errors.INTERNAL_UNEXPECTED),  # bad key
        (openai.BadRequestError, 400, errors.INTERNAL_UNEXPECTED),  # e.g. Azure content filter
    ],
)
def test_openai_errors_become_taxonomy_codes(monkeypatch, cls, status, code):
    monkeypatch.setattr(llm, "PROVIDER", "azure")
    monkeypatch.setattr(llm, "_client", lambda: _RaisingOpenAI(_openai_error(cls, status)))
    with pytest.raises(errors.DeskhandError) as info:
        llm.structured_completion(MESSAGES, TriageResult)
    assert info.value.internal_error_code == code


@pytest.mark.parametrize(
    "cls, status, code",
    [
        (anthropic.RateLimitError, 429, errors.LLM_RATE_LIMITED),
        (anthropic.AuthenticationError, 401, errors.INTERNAL_UNEXPECTED),
        (anthropic.BadRequestError, 400, errors.INTERNAL_UNEXPECTED),
    ],
)
def test_anthropic_errors_become_taxonomy_codes(monkeypatch, cls, status, code):
    monkeypatch.setattr(llm, "PROVIDER", "anthropic")
    monkeypatch.setattr(llm, "_client", lambda: _RaisingAnthropic(_anthropic_error(cls, status)))
    with pytest.raises(errors.DeskhandError) as info:
        llm.structured_completion(MESSAGES, TriageResult)
    assert info.value.internal_error_code == code


def test_haiku_5_5_is_priced():
    # The lab prices checkpoint 00's calls with this table too: a missing row
    # silently reports $0.
    assert llm._cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
