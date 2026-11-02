"""The MCP server: Deskhand's read tools for an agent that is not Deskhand.

What a client's model can see and set is the tool list and each tool's JSON
schema, so that is what these tests read. The reader group must not be in
any schema; it comes from the server's environment. kb_search.search_kb is
replaced with a fake (no Qdrant, no embedding call) and the order API is the
httpx MockTransport from conftest.py, so nothing here touches the network or
qdrant_data/.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

import errors
import kb_search
import llm
import mcp_server
import nodes


def _tools() -> dict:
    return {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}


def _call(name: str, arguments: dict) -> dict:
    """Call a tool the way a client would, and decode its JSON text result."""
    result = asyncio.run(mcp_server.server.call_tool(name, arguments))
    assert not result.is_error
    return json.loads(result.content[0].text)


class FakeSearch:
    """Stands in for kb_search.search_kb and records who it searched as."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, query, caller_groups, **kwargs):
        self.calls.append({"query": query, "caller_groups": list(caller_groups), **kwargs})
        chunk = {"kb_id": "kb:manual-airbeat-headphones#resetting", "rank": 1,
                 "score": 0.71, "title": "AirBeat headphones", "section": "Resetting",
                 "source_type": "markdown", "status": "current",
                 "allowed_groups": ["customer", "support-agent"],
                 "effective_from": "2026-01-01", "text": "Hold the power button..."}
        trace = {"query": query, "caller_groups": list(caller_groups), "hits": [chunk["kb_id"]]}
        return kb_search.KbResult([chunk], trace, llm.Usage(prompt_tokens=0, completion_tokens=0,
                                                           cost_usd=0.0))


@pytest.fixture()
def fake_search(monkeypatch) -> FakeSearch:
    fake = FakeSearch()
    monkeypatch.setattr(kb_search, "search_kb", fake)
    return fake


@pytest.fixture()
def no_group_env(monkeypatch) -> None:
    monkeypatch.delenv(mcp_server.CALLER_GROUP_ENV, raising=False)


# ---------------------------------------------------------------------------
# What the client's model is shown
# ---------------------------------------------------------------------------

def test_only_the_two_read_tools_are_registered() -> None:
    # No issue_refund: writes go through Deskhand's graph and approval gate.
    tools = _tools()
    assert set(tools) == {"lookup_order", "search_kb"}
    for tool in tools.values():
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.description.startswith("Read-only")


def test_search_kb_schema_has_no_way_to_choose_the_reader() -> None:
    schema = _tools()["search_kb"].input_schema
    assert set(schema["properties"]) == {"query", "k"}
    assert schema["required"] == ["query"]
    assert schema["properties"]["k"]["default"] == 5
    for name in schema["properties"]:
        assert not any(word in name.lower()
                       for word in ("group", "permission", "role", "caller", "superseded"))


def test_constants_copied_for_a_fast_start_still_match() -> None:
    # mcp_server copies these to answer Claude Code's handshake before the
    # heavy imports finish (see its import note).
    import kb_ingest

    assert mcp_server.KNOWN_GROUPS == kb_ingest.KNOWN_GROUPS
    assert mcp_server.DEFAULT_K == kb_search.TOP_K


def test_lookup_order_schema_matches_the_service_tool() -> None:
    schema = _tools()["lookup_order"].input_schema
    assert set(schema["properties"]) == {"order_id"}
    service = nodes.LookupOrderArgs.model_json_schema()["properties"]["order_id"]
    assert schema["properties"]["order_id"]["pattern"] == service["pattern"]


# ---------------------------------------------------------------------------
# The reader group comes from the environment
# ---------------------------------------------------------------------------

def test_unset_group_searches_as_a_customer(no_group_env, fake_search) -> None:
    out = _call("search_kb", {"query": "goodwill credit limit"})
    assert fake_search.calls[0]["caller_groups"] == ["customer"]
    assert out["searched_as"] == ["customer"]
    assert out["chunks"][0]["kb_id"].startswith("kb:")


def test_group_is_read_from_the_environment(monkeypatch, fake_search) -> None:
    monkeypatch.setenv(mcp_server.CALLER_GROUP_ENV, "support-agent")
    _call("search_kb", {"query": "goodwill credit limit", "k": 3})
    call = fake_search.calls[0]
    assert call["caller_groups"] == ["support-agent"]
    assert call["k"] == 3
    # The service's measured choice, not the client's.
    assert (call["mode"], call["rerank"]) == (nodes.KB_MODE, nodes.KB_RERANK)


def test_a_group_smuggled_into_the_arguments_is_not_used(no_group_env, fake_search) -> None:
    # A model (or an instruction in a document) adds the argument anyway. The
    # SDK may drop it or reject the call; it must never reach the search.
    try:
        asyncio.run(mcp_server.server.call_tool(
            "search_kb", {"query": "macros", "caller_groups": ["support-agent"]}))
    except ToolError:
        pass
    assert all(c["caller_groups"] == ["customer"] for c in fake_search.calls)


def test_an_unknown_group_fails_at_launch(monkeypatch) -> None:
    monkeypatch.setenv(mcp_server.CALLER_GROUP_ENV, "support-agnet")
    with pytest.raises(ValueError, match="unknown group"):
        mcp_server.configured_groups()


def test_bad_k_is_rejected_by_the_schema(no_group_env, fake_search) -> None:
    with pytest.raises(ToolError):
        asyncio.run(mcp_server.server.call_tool("search_kb", {"query": "x", "k": 50}))
    assert fake_search.calls == []


# ---------------------------------------------------------------------------
# lookup_order: the service's tool over a mock HTTP response
# ---------------------------------------------------------------------------

def test_lookup_order_maps_the_api_response(monkeypatch, order_api) -> None:
    monkeypatch.setenv(mcp_server.CALLER_GROUP_ENV, "support-agent")
    out = _call("lookup_order", {"order_id": "O-48291"})
    assert out["found"] is True
    assert out["total_usd"] == 79.99
    assert out["items"][0]["sku"] == "NB-HP-01"
    # A 404 is an answer, not a failure.
    assert _call("lookup_order", {"order_id": "O-00000"}) == {"order_id": "O-00000", "found": False}


def test_a_customer_server_cannot_read_orders(no_group_env, monkeypatch) -> None:
    def no_http(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the order API must not be called")

    monkeypatch.setattr(nodes, "transport", httpx.MockTransport(no_http))
    with pytest.raises(ToolError, match="support-agent"):
        asyncio.run(mcp_server.server.call_tool("lookup_order", {"order_id": "O-48291"}))


def test_bad_order_id_is_rejected_before_any_http(monkeypatch) -> None:
    monkeypatch.setenv(mcp_server.CALLER_GROUP_ENV, "support-agent")

    def no_http(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the order API must not be called")

    monkeypatch.setattr(nodes, "transport", httpx.MockTransport(no_http))
    with pytest.raises(ToolError):
        asyncio.run(mcp_server.server.call_tool("lookup_order", {"order_id": "48291"}))


def test_order_api_failure_reaches_the_client_as_a_code(monkeypatch) -> None:
    # The client's model reads the taxonomy code; the detail stays in the log.
    monkeypatch.setenv(mcp_server.CALLER_GROUP_ENV, "support-agent")

    def always_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("order API too slow", request=request)

    monkeypatch.setattr(nodes, "transport", httpx.MockTransport(always_timeout))
    monkeypatch.setattr(nodes, "ORDER_API_BACKOFF_S", (0, 0))
    with pytest.raises(ToolError) as info:
        asyncio.run(mcp_server.server.call_tool("lookup_order", {"order_id": "O-123"}))
    assert errors.TOOL_ORDER_API_TIMEOUT in str(info.value)
    assert "too slow" not in str(info.value)
