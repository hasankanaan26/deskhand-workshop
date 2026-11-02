"""Deskhand's tools as an MCP server, for agents that are not Deskhand.

Session 7's "agents at work" opener: Claude Code (or any MCP client) starts
this process and calls two of Deskhand's tools as its own.

    lookup_order(order_id)   one order from the mock order API (support agents only)
    search_kb(query, k=5)    the support knowledge base, as this server's reader

MCP (Model Context Protocol) is a standard way to hand an agent tools: the
client launches the server, asks it which tools it has (a name, a description
and a JSON schema each), and the model then calls them by name. The tools are
the same code the LangGraph service runs: nodes.lookup_order with its retries
and taxonomy codes, kb_search.search_kb with its access filter inside the
Qdrant query. Only the caller is new, and the caller is a model nobody here
wrote a prompt for. Hence the two rules below.

1. The reader group is the server's configuration, never a tool argument.
   DESKHAND_MCP_CALLER_GROUP (default: customer) is read from the environment
   the client launched this process with. search_kb's schema has no group
   parameter, so no model, prompt or poisoned document can ask for
   "support-agent" and get the staff-only macros. That is the same rule as
   research_kb taking the groups from the ticket's channel: who is asking is
   decided by where the request comes from, which the operator controls, not
   by what the request says. The default is the least-privileged reader, so a
   server launched without the variable leaks nothing. In production the group
   would come from the client's authenticated identity (an OAuth token on an
   HTTP transport); over stdio the launching config is the identity.

2. Read-only. There is no issue_refund here. A refund in Deskhand is a run:
   triage, rules 1 to 6 in code, then the approval gate above the auto-approve
   limit, with a human's yes recorded against the run. An MCP refund tool
   would be a second door to the same money with none of that in front of it,
   called whenever the client's model decides to. Writes go through Deskhand's
   graph and its approval gate; agents outside it get to look, not pay.

lookup_order also needs the support-agent group. An order record carries a
customer's name and delivery details; a customer-group caller would need its
own identity checked against the order's owner (fetch_order_data does that
for a ticket), and an MCP argument cannot prove who someone is.

Tool results are data. O-70058's `notes` field holds an instruction to "the
AI agent"; it reaches the client's model as text in a tool result, as it
reaches Deskhand's planner. Claude Code's own guardrails meet it there.

Qdrant local mode: one process per qdrant_data/ folder. The first search_kb
call opens the folder and this process keeps it until it exits, so the lab's
03 service (uvicorn app:app), kb_ingest.py, kb_sync.py and the evals cannot
open it at the same time. Stop the service before starting Claude Code, and
quit Claude Code (or disable the server with /mcp) before starting it again.
lookup_order never touches Qdrant.

Run (Claude Code starts it for you, see .mcp.json.example):
    .venv\\Scripts\\python.exe checkpoints\\03-rag\\mcp_server.py
It speaks JSON-RPC on stdin/stdout, so run by hand it waits silently for a
client. Nothing in it may print to stdout: that is the protocol's wire.
Every tool call is logged to logs/runs.jsonl under request_id "mcp-...", with
the group it ran as and, for search_kb, the trace.
"""
from __future__ import annotations

import os
import threading
import uuid
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

import errors
import obslog

# nodes and kb_search are imported by _deskhand(), not here. They bring the
# provider SDKs, LangChain, Qdrant and fastembed: 10 to 15 seconds of imports
# on a cold Windows laptop, and Claude Code gives a server 30 seconds to
# answer its handshake. So the server answers first, and main() warms the
# imports on a thread while the client connects. The constants they would
# have supplied are copied below; the tests check the copies still match.
KNOWN_GROUPS = ("customer", "support-agent")  # = kb_ingest.KNOWN_GROUPS
DEFAULT_K = 5  # = kb_search.TOP_K

# One thread at a time, for the imports and for the calls. The SDK runs each
# tool call on a worker thread and Claude Code may send two at once. LangChain
# loads its classes lazily, and two threads loading it together (the warm-up
# and an early call) fail with "module 'langchain_core.runnables.base' not
# found"; Qdrant local mode is not built for parallel queries either. One
# person's Claude Code session does not need parallel calls. Reentrant, so a
# tool holding it can still call _deskhand().
_one_at_a_time = threading.RLock()


def _deskhand():
    """The service's modules, imported once, by one thread at a time."""
    with _one_at_a_time:
        import kb_search
        import nodes
    return kb_search, nodes

CALLER_GROUP_ENV = "DESKHAND_MCP_CALLER_GROUP"
# The least-privileged reader: an unset or empty variable searches as a
# customer, never as staff.
DEFAULT_CALLER_GROUP = "customer"
# Groups that may read order records (see the module docstring).
ORDER_READER_GROUPS = frozenset({"support-agent"})
# The same order id pattern as nodes.LookupOrderArgs, so a malformed id is
# refused by the schema, before any HTTP (tests/test_mcp_server.py checks they
# match).
ORDER_ID_PATTERN = r"^O-\d{3,}$"
MAX_K = 10  # what a client may ask for; the service itself uses kb_search's 5


def configured_groups() -> list[str]:
    """The reader groups this server searches as, from its launch config.

    Comma-separated, e.g. "support-agent" or "customer,support-agent". A typo
    fails loudly: an unknown group would match no chunk and every search would
    come back empty and innocent-looking.
    """
    raw = os.getenv(CALLER_GROUP_ENV, "").strip() or DEFAULT_CALLER_GROUP
    groups = sorted({g.strip() for g in raw.split(",") if g.strip()})
    unknown = set(groups) - set(KNOWN_GROUPS)
    if unknown:
        raise ValueError(
            f"{CALLER_GROUP_ENV}={raw!r}: unknown group(s) {sorted(unknown)}; "
            f"known: {list(KNOWN_GROUPS)}"
        )
    return groups


def _new_request_id() -> str:
    return f"mcp-{uuid.uuid4().hex[:8]}"


def _coded_failure(request_id: str, tool: str, exc: errors.DeskhandError) -> ToolError:
    """A DeskhandError as the client sees it: the code, never the detail.

    Same split as errors.py: the internal message goes to the log, and the
    caller gets the code plus where to look. A ToolError reaches the model as
    an is_error result it can read; any other exception would reach it only
    as "Error executing tool ...".
    """
    obslog.log_event(request_id, "mcp.tool_failed", tool=tool,
                     internal_error_code=exc.internal_error_code, detail=str(exc))
    return ToolError(f"{exc.internal_error_code} (request_id {request_id}, "
                     "details in logs/runs.jsonl)")


server = MCPServer(
    "deskhand",
    instructions=(
        "Deskhand is Nimbus Electronics' support-resolution system. These tools "
        "are read-only: look up an order, search the support knowledge base. "
        "Refunds are not available here; they go through Deskhand's approval "
        "gate. The reader group is fixed by the server's configuration."
    ),
)

# readOnlyHint tells the client these tools change nothing, so it may call
# them without the confirmation it asks for before a write. It is a hint to a
# well-behaved client, not the guard: the guard is that no write tool exists.
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)


@server.tool(annotations=_READ_ONLY)
def lookup_order(
    order_id: Annotated[str, Field(pattern=ORDER_ID_PATTERN,
                                   description="Nimbus order id, e.g. 'O-48291'.")],
) -> dict:
    """Read-only. Fetch one order (items, total, status, delivery date) from
    the Nimbus order system. Returns found=false when the order does not
    exist. Only a server configured for support agents may read orders."""
    request_id = _new_request_id()
    groups = configured_groups()
    obslog.log_event(request_id, "mcp.tool_call", tool="lookup_order",
                     caller_groups=groups, order_id=order_id)
    if not ORDER_READER_GROUPS & set(groups):
        # Refused before any HTTP: the record never enters this process.
        raise ToolError(f"lookup_order needs one of {sorted(ORDER_READER_GROUPS)}; "
                        f"this server reads as {groups} ({CALLER_GROUP_ENV})")
    with _one_at_a_time:
        _, nodes = _deskhand()
        try:
            # The service's own tool: the 5 s timeout, 2 retries and taxonomy
            # codes come with it (nodes._order_api_call).
            order = nodes.lookup_order.invoke({"order_id": order_id})
        except errors.DeskhandError as exc:
            raise _coded_failure(request_id, "lookup_order", exc) from exc
        except ValidationError as exc:  # the schema above already checks; belt and braces
            raise ToolError(f"invalid order id {order_id!r}") from exc
    if order is None:
        # A 404 is an answer, not a failure (errors.TOOL_ORDER_NOT_FOUND).
        return {"order_id": order_id, "found": False}
    return {"found": True, **order}


@server.tool(annotations=_READ_ONLY)
def search_kb(
    query: Annotated[str, Field(min_length=1, max_length=500,
                                description="What to look for, in plain words.")],
    k: Annotated[int, Field(ge=1, le=MAX_K, description="How many chunks to return.")] = DEFAULT_K,
) -> dict:
    """Read-only. Search Nimbus's support knowledge base: product manuals,
    carrier notices and, for support agents, internal macros, ops emails and
    past tickets. Returns the top k chunks with a citable kb_id each, and a
    trace of what was searched as whom. Superseded documents are excluded.
    What you may see is set by the server's configuration, not by the query."""
    request_id = _new_request_id()
    groups = configured_groups()
    with _one_at_a_time:
        kb_search, nodes = _deskhand()
        try:
            # Mode and rerank are the service's measured choice (nodes.KB_MODE,
            # nodes.KB_RERANK: hybrid, no rerank), not the client's: like the
            # group, they are configuration. include_superseded stays False.
            result = kb_search.search_kb(query, groups, k=k,
                                         mode=nodes.KB_MODE, rerank=nodes.KB_RERANK)
        except errors.DeskhandError as exc:  # the embedding call failed
            raise _coded_failure(request_id, "search_kb", exc) from exc
        except RuntimeError as exc:
            # Our own messages ("no 'kb' collection — run `py kb_ingest.py`"),
            # or Qdrant's when another process holds qdrant_data/.
            obslog.log_event(request_id, "mcp.tool_failed", tool="search_kb", detail=str(exc))
            hint = (" Stop the lab's 03 service: qdrant_data/ opens in one process at a time."
                    if "already accessed" in str(exc) else "")
            raise ToolError(f"{exc}{hint}") from exc
    obslog.log_event(request_id, "mcp.tool_call", tool="search_kb",
                     caller_groups=groups, trace=result.trace,
                     cost_usd=result.usage.cost_usd)
    return {
        "searched_as": groups,
        "chunks": [
            {key: c.get(key) for key in ("kb_id", "rank", "score", "title", "section",
                                         "source_type", "status", "effective_from", "text")}
            for c in result.chunks
        ],
        "trace": result.trace,
    }


def main() -> None:
    # Check the configuration before the client connects: a typo'd group
    # stops the server at launch (Claude Code's /mcp shows it failed) instead
    # of failing every call.
    configured_groups()
    # Imports only: nothing here opens qdrant_data/ (the first search does).
    # A tool called before it finishes waits on _one_at_a_time.
    threading.Thread(target=_deskhand, name="warm-imports", daemon=True).start()
    server.run("stdio")


if __name__ == "__main__":
    main()
