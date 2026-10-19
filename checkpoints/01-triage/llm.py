"""llm.py — the only module that talks to the LLM provider.

Swapping providers (or mocking in tests) touches this file alone. Checkpoint 01
only uses structured_completion; text_completion (reply drafts) and stream_text
(streaming replies) are part of the same contract and come into play from
checkpoint 02 on.

`LLM_PROVIDER` in the repo `.env` picks the provider:

- "azure"     -> Azure OpenAI. The Azure quirk worth knowing: `model=` is a
                 DEPLOYMENT name you chose in Azure AI Foundry, not a model
                 name (AZURE_OPENAI_DEPLOYMENT). Structured outputs need
                 api-version 2024-08-01-preview or newer on a
                 structured-output-capable model (gpt-4.1, gpt-4o-2024-08-06+).
- "openai"    -> api.openai.com with OPENAI_API_KEY / OPENAI_MODEL.
- "anthropic" -> the Claude API with ANTHROPIC_API_KEY / ANTHROPIC_MODEL.

Azure and OpenAI share an SDK and the calls below — only the client
construction and the meaning of `model` differ. Anthropic is its own SDK, so
each function has an `if PROVIDER == "anthropic"` branch: system messages move
to the top-level `system` field and structured outputs use
`client.messages.parse`.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache

import anthropic
import openai
import pydantic
from dotenv import load_dotenv
from pydantic import BaseModel

import errors

load_dotenv()

PROVIDER = (os.getenv("LLM_PROVIDER") or "azure").strip().lower()
# Fail at import on an unknown name. Every branch below falls through to
# OpenAI, so a typo like "azrue" would otherwise call a different provider.
if PROVIDER not in {"azure", "openai", "anthropic"}:
    raise RuntimeError(
        f"LLM_PROVIDER={PROVIDER!r} is not supported — use 'azure', 'openai' "
        "or 'anthropic' (see .env.example)"
    )

# Names only — reading them never fails, so importing this module (as the tests
# do) never needs credentials. Missing keys surface on the first real call.
if PROVIDER == "azure":
    DEFAULT_MODEL = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
elif PROVIDER == "anthropic":
    DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
else:
    DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# USD per 1M tokens. Illustrative — check the provider's pricing page and edit.
# Keys are model FAMILIES: on Azure the model string is a deployment name
# ("deskhand-gpt-4.1-mini"), so we look for the family inside it.
# Worked example: 1,000 prompt + 100 completion tokens on gpt-4o-mini cost
# (1000 * 0.15 + 100 * 0.60) / 1M = $0.00021.
PRICE_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},  # prompts up to 100K tokens
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

# Anthropic only. Thinking tokens count toward max_tokens on current Claude
# models, so a 1024-token cap meant for the answer alone would truncate it;
# give it a floor. ANTHROPIC_EFFORT (low|medium|high|xhigh|max) sets how hard
# the model thinks — leave it unset on Haiku 4.5, which rejects it.
ANTHROPIC_MIN_MAX_TOKENS = 16000
ANTHROPIC_EFFORT = (os.getenv("ANTHROPIC_EFFORT") or "").strip().lower() or None


class Usage(BaseModel):
    """Tokens and dollars for one LLM call. Callers add `cost_usd` up per
    ticket, so every run can report what it spent."""

    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


def _cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prices = PRICE_TABLE.get(model)
    if prices is None:
        families = [family for family in PRICE_TABLE if family in model.lower()]
        if not families:  # unknown deployment: track tokens, price 0 rather than guess
            return 0.0
        prices = PRICE_TABLE[max(families, key=len)]  # "gpt-4o-mini" beats "gpt-4o"
    return (prompt_tokens * prices["input"] + completion_tokens * prices["output"]) / 1_000_000


def _usage(response_usage, model: str) -> Usage:  # noqa: ANN001 - openai or anthropic usage
    # Same counts, different field names: Anthropic says input/output,
    # OpenAI says prompt/completion.
    if PROVIDER == "anthropic":
        prompt, completion = response_usage.input_tokens, response_usage.output_tokens
    else:
        prompt, completion = response_usage.prompt_tokens, response_usage.completion_tokens
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=_cost_usd(model, prompt, completion),
    )


def _require(name: str) -> str:
    """An env var that must be set. A value still reading "your-..." is the
    .env.example placeholder, so it counts as unset."""
    value = (os.getenv(name) or "").strip()
    if not value or value.startswith("your-"):
        raise RuntimeError(
            f"{name} is not set — fill it in the repo .env "
            f"(LLM_PROVIDER={PROVIDER}, see .env.example)"
        )
    return value


@lru_cache(maxsize=1)
def _client() -> openai.OpenAI | openai.AzureOpenAI | anthropic.Anthropic:
    # Created lazily so importing this module never requires an API key.
    # lru_cache builds it once per process. The SDK clients retry 429s, 5xx
    # and timeouts twice with backoff by default (max_retries=2), so an error
    # that reaches the except blocks below has already failed three times.
    if PROVIDER == "anthropic":
        return anthropic.Anthropic(api_key=_require("ANTHROPIC_API_KEY"))
    if PROVIDER == "azure":
        return openai.AzureOpenAI(
            api_key=_require("AZURE_OPENAI_API_KEY"),
            azure_endpoint=_require("AZURE_OPENAI_ENDPOINT"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
        )
    return openai.OpenAI(api_key=_require("OPENAI_API_KEY"))


def _anthropic_kwargs(messages: list[dict], model: str, max_output_tokens: int) -> dict:
    """OpenAI-style messages -> Messages API arguments. No `temperature`:
    current Claude models reject sampling params."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    kwargs: dict = {
        "model": model,
        "max_tokens": max(max_output_tokens, ANTHROPIC_MIN_MAX_TOKENS),
        "messages": [m for m in messages if m["role"] != "system"],
    }
    if system:
        kwargs["system"] = system
    if ANTHROPIC_EFFORT:
        kwargs["output_config"] = {"effort": ANTHROPIC_EFFORT}
    return kwargs


def structured_completion(
    messages: list[dict],
    response_model: type[BaseModel],
    model: str | None = None,
    temperature: float = 0.0,  # low randomness for labels; v1 used 1.0 (incident #8)
    max_output_tokens: int = 1024,
) -> tuple[BaseModel, Usage]:
    """One LLM call that must return a valid `response_model` instance.

    Uses the provider's structured-output mode (json_schema). Provider
    exceptions never escape this module — they're mapped to taxonomy codes:
    rate limits and timeouts get their own codes, every other provider error
    (a bad key, a bad request, a content filter) is INTERNAL_UNEXPECTED.
    """
    model = model or DEFAULT_MODEL
    if PROVIDER == "anthropic":
        return _anthropic_structured(messages, response_model, model, max_output_tokens)
    # parse() sends the Pydantic model as a strict JSON schema
    # (response_format json_schema, strict: true). The provider then generates
    # only replies that fit it, and the SDK validates the reply back into the
    # model. Strict mode needs every object's fields listed up front, so an
    # untyped `dict` field is rejected by the API.
    try:
        response = _client().beta.chat.completions.parse(
            model=model,
            messages=messages,
            response_format=response_model,
            temperature=temperature,
            max_tokens=max_output_tokens,
        )
    # Typed error mapping. Order matters: RateLimitError and APITimeoutError
    # are subclasses of APIError, so they must come before the catch-all.
    # `detail` keeps the raw provider text for logs; users only ever see
    # errors.user_message(). LengthFinishReasonError is v1's Incident A: the
    # reply hit max_tokens and the JSON was cut off. Here it gets a name.
    except openai.LengthFinishReasonError as exc:
        raise errors.DeskhandError(errors.LLM_MALFORMED_OUTPUT, f"output truncated: {exc}") from exc
    except openai.RateLimitError as exc:
        raise errors.DeskhandError(errors.LLM_RATE_LIMITED, str(exc)) from exc
    except openai.APITimeoutError as exc:
        raise errors.DeskhandError(errors.LLM_TIMEOUT, str(exc)) from exc
    except openai.APIError as exc:  # auth, bad request, content filter, connection
        raise errors.DeskhandError(errors.INTERNAL_UNEXPECTED, f"{type(exc).__name__}: {exc}") from exc
    message = response.choices[0].message
    if message.parsed is None:  # model refused, or output failed schema parsing
        raise errors.DeskhandError(errors.LLM_MALFORMED_OUTPUT, message.refusal or "no parsed output")
    return message.parsed, _usage(response.usage, model)


def _anthropic_structured(
    messages: list[dict], response_model: type[BaseModel], model: str, max_output_tokens: int
) -> tuple[BaseModel, Usage]:
    """The Anthropic version of the call above. `messages.parse` sends the
    Pydantic model as the output format and validates the reply into
    `parsed_output`; the stop_reason checks name truncation and refusals."""
    try:
        response = _client().messages.parse(
            **_anthropic_kwargs(messages, model, max_output_tokens),
            output_format=response_model,
        )
    except anthropic.RateLimitError as exc:
        raise errors.DeskhandError(errors.LLM_RATE_LIMITED, str(exc)) from exc
    except anthropic.APIConnectionError as exc:  # includes APITimeoutError
        raise errors.DeskhandError(errors.LLM_TIMEOUT, str(exc)) from exc
    except anthropic.APIError as exc:  # auth, bad request, overloaded
        raise errors.DeskhandError(errors.INTERNAL_UNEXPECTED, f"{type(exc).__name__}: {exc}") from exc
    except pydantic.ValidationError as exc:
        raise errors.DeskhandError(errors.LLM_MALFORMED_OUTPUT, str(exc)) from exc
    if response.stop_reason == "max_tokens":
        raise errors.DeskhandError(errors.LLM_MALFORMED_OUTPUT, "output truncated: hit max_tokens")
    if response.stop_reason == "refusal" or response.parsed_output is None:
        detail = "refused" if response.stop_reason == "refusal" else "no parsed output"
        raise errors.DeskhandError(errors.LLM_MALFORMED_OUTPUT, detail)
    return response.parsed_output, _usage(response.usage, model)


def text_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = 1024,
) -> tuple[str, Usage]:
    """Plain-text completion — for customer reply drafts, never machine output."""
    model = model or DEFAULT_MODEL
    if PROVIDER == "anthropic":
        try:
            response = _client().messages.create(**_anthropic_kwargs(messages, model, max_output_tokens))
        except anthropic.RateLimitError as exc:
            raise errors.DeskhandError(errors.LLM_RATE_LIMITED, str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise errors.DeskhandError(errors.LLM_TIMEOUT, str(exc)) from exc
        except anthropic.APIError as exc:
            raise errors.DeskhandError(errors.INTERNAL_UNEXPECTED, f"{type(exc).__name__}: {exc}") from exc
        # Text blocks only. With thinking on, `content` also holds thinking
        # blocks, and those must never reach a customer.
        text = "".join(block.text for block in response.content if block.type == "text")
        return text, _usage(response.usage, model)
    try:
        response = _client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_output_tokens,
        )
    except openai.RateLimitError as exc:
        raise errors.DeskhandError(errors.LLM_RATE_LIMITED, str(exc)) from exc
    except openai.APITimeoutError as exc:
        raise errors.DeskhandError(errors.LLM_TIMEOUT, str(exc)) from exc
    except openai.APIError as exc:  # auth, bad request, content filter, connection
        raise errors.DeskhandError(errors.INTERNAL_UNEXPECTED, f"{type(exc).__name__}: {exc}") from exc
    # `content` can be None (a refusal, for one); callers always get a str.
    return response.choices[0].message.content or "", _usage(response.usage, model)


def stream_text(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = 1024,
) -> Iterator[str]:
    """Yield text deltas as they arrive. Only ever used for the customer reply
    (checkpoint 02+) — machine output must never stream partially.

    No Usage comes back: this version neither asks for stream usage nor
    returns it, so a streamed call goes unpriced. Checkpoint 02 adds both."""
    model = model or DEFAULT_MODEL
    if PROVIDER == "anthropic":
        # The try wraps the whole loop: a stream can fail halfway, while the
        # caller is already iterating, not only when it opens.
        try:
            with _client().messages.stream(**_anthropic_kwargs(messages, model, max_output_tokens)) as stream:
                yield from stream.text_stream
        except anthropic.RateLimitError as exc:
            raise errors.DeskhandError(errors.LLM_RATE_LIMITED, str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise errors.DeskhandError(errors.LLM_TIMEOUT, str(exc)) from exc
        except anthropic.APIError as exc:
            raise errors.DeskhandError(errors.INTERNAL_UNEXPECTED, f"{type(exc).__name__}: {exc}") from exc
        return
    try:
        stream = _client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_output_tokens,
            stream=True,
        )
        # Some chunks carry no text: Azure's first chunk has empty `choices`
        # (prompt-filter results only), and the last one only says why the
        # model stopped. v1 hit the empty one too (00-broken-bot/bot.py).
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except openai.RateLimitError as exc:
        raise errors.DeskhandError(errors.LLM_RATE_LIMITED, str(exc)) from exc
    except openai.APITimeoutError as exc:
        raise errors.DeskhandError(errors.LLM_TIMEOUT, str(exc)) from exc
    except openai.APIError as exc:  # auth, bad request, content filter, connection
        raise errors.DeskhandError(errors.INTERNAL_UNEXPECTED, f"{type(exc).__name__}: {exc}") from exc
