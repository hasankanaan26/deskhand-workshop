"""End-to-end graph tests with the LLM and order API fully mocked.

The headline test kills the process (KeyboardInterrupt, uncatchable by the
graph's error handling — like a real crash) AFTER execute_actions persisted
its state but BEFORE finalize, then resumes the run and proves the refund was
not issued twice.

The runtime is LangGraph (graph_lg.py), the only one from checkpoint 03 on.
"""
from __future__ import annotations

import pytest

import errors
import graph_lg
import llm
import retrieve
import store
from models import Action, ResolutionPlan, TicketResolutionState, TriageResult

TICKET_TEXT = (
    "Ticket T-1001 from Dana Whitfield (order O-48291)\n"
    "[customer] My wireless headphones arrived with a cracked case. "
    "I want a refund."
)

TRIAGE = TriageResult(
    category="refund_request", priority="medium", sentiment="frustrated",
    confidence=0.92,
)

FAKE_CHUNKS = [
    {
        "doc_id": "refund-policy",
        "section_id": "refund-window",
        "version": "2.3",
        "effective": "2026-06-01",
        "text": "Refunds within 14 days of delivery.",
        "score": 0.91,
    }
]

GOOD_PLAN = ResolutionPlan(
    plan_version="v1",
    ticket_id="T-1001",
    category="refund_request",
    actions=[Action(type="issue_refund", params={"amount_usd": 30.0})],
    refund_amount_usd=30.0,  # under $50 => no approval gate in the happy path
    requires_approval=False,
    confidence=0.92,
    citations=["refund-policy#refund-window"],
    rationale="cracked case, inside refund window",
)

NO_USAGE = llm.Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.0005)


def fake_structured_completion(messages, response_model, **kwargs):
    if response_model is TriageResult:
        return TRIAGE.model_copy(deep=True), NO_USAGE
    return GOOD_PLAN.model_copy(deep=True), NO_USAGE


def fake_retrieve(query, k=6, client=None):
    return [dict(c) for c in FAKE_CHUNKS], NO_USAGE


def fake_stream_text(messages, **kwargs):
    yield "Hello, "
    yield "your refund is on its way."


def crashing_stream_text(messages, **kwargs):
    raise KeyboardInterrupt("process killed mid-run")
    yield  # pragma: no cover - makes this a generator


def make_state() -> TicketResolutionState:
    # customer_name as POST /tickets sets it for a dataset ticket. Without it
    # the sender is unknown and the refund would stop at the approval gate.
    return TicketResolutionState(
        request_id="req-recover01",
        ticket_id="T-1001",
        ticket_text=TICKET_TEXT,
        customer_name="Dana Whitfield",
    )


def test_happy_path_completes(monkeypatch, db_path, order_api) -> None:
    monkeypatch.setattr(llm, "structured_completion", fake_structured_completion)
    monkeypatch.setattr(retrieve, "retrieve", fake_retrieve)
    monkeypatch.setattr(llm, "stream_text", fake_stream_text)

    state = graph_lg.run(make_state(), db_path=db_path)

    assert state.status == "completed"
    assert state.customer_reply_draft == "Hello, your refund is on its way."
    assert state.validation_errors == []
    assert state.policy_chunks == FAKE_CHUNKS
    assert "refund-policy#refund-window (v2.3)" in (state.internal_note or "")
    assert len(order_api.refunds_by_key) == 1

    event_types = [e for _, e, _ in store.events_since("req-recover01", 0, db_path)]
    assert "reply.delta" in event_types
    # finalize emits run.completed; the runner's node.completed for finalize
    # is written after it, so it is the very last event.
    assert event_types[-1] == "node.completed"
    assert "run.completed" in event_types
    assert "run.failed" not in event_types


def test_crash_between_execute_and_finalize_recovers_without_double_refund(
    monkeypatch, db_path, order_api
) -> None:
    monkeypatch.setattr(llm, "structured_completion", fake_structured_completion)
    monkeypatch.setattr(retrieve, "retrieve", fake_retrieve)
    monkeypatch.setattr(llm, "stream_text", crashing_stream_text)

    with pytest.raises(KeyboardInterrupt):
        graph_lg.run(make_state(), db_path=db_path)

    # The refund happened, and the state AFTER execute_actions was persisted.
    assert len(order_api.refunds_by_key) == 1
    resumable = store.find_resumable(db_path=db_path)
    assert len(resumable) == 1
    stuck_state, next_node = resumable[0]
    assert next_node == "draft_customer_reply"  # the node that crashed runs again
    assert stuck_state.status == "executing"
    assert "refund R-0001 issued" in (stuck_state.internal_note or "")
    # LangGraph's own checkpoint agrees with the runs table.
    snapshot = graph_lg.checkpoint_state("req-recover01", db_path=db_path)
    assert snapshot.next == ("draft_customer_reply",)

    # Restart with a healthy stream: LangGraph continues from its checkpoint.
    monkeypatch.setattr(llm, "stream_text", fake_stream_text)
    resumed = graph_lg.resume(stuck_state, next_node, db_path=db_path)

    assert resumed.status == "completed"
    assert resumed.customer_reply_draft == "Hello, your refund is on its way."
    assert order_api.refund_post_count == 1  # execution was NOT repeated
    assert len(order_api.refunds_by_key) == 1


def test_resume_that_repeats_execute_actions_still_refunds_once(
    monkeypatch, db_path, order_api
) -> None:
    """Even if the crash lands INSIDE execute_actions (state not yet saved),
    re-running the node is safe: the idempotency key is stable."""
    monkeypatch.setattr(llm, "structured_completion", fake_structured_completion)
    monkeypatch.setattr(retrieve, "retrieve", fake_retrieve)
    monkeypatch.setattr(llm, "stream_text", fake_stream_text)

    state = graph_lg.run(make_state(), db_path=db_path)
    assert state.status == "completed"

    # Rebuild the state execute_actions saw and run that node again, as a
    # resume after a crash inside it would.
    replayed = make_state()
    replayed.order_data = {"order_id": "O-48291", "total_usd": 79.99}
    replayed.resolution_plan = GOOD_PLAN.model_copy(deep=True)
    graph_lg.run(replayed, start="execute_actions", db_path=db_path)

    assert order_api.refund_post_count == 2
    assert len(order_api.refunds_by_key) == 1  # same key -> same refund


def test_repair_exhausted_fails_safely(monkeypatch, db_path, order_api) -> None:
    # $500 on a $79.99 order breaks rule 1 on every attempt, so both repairs
    # fail and the run must end safely with no money moved.
    bad_plan = GOOD_PLAN.model_copy(
        update={"refund_amount_usd": 500.0, "requires_approval": True}
    )

    def always_bad(messages, response_model, **kwargs):
        if response_model is TriageResult:
            return TRIAGE.model_copy(deep=True), NO_USAGE
        return bad_plan.model_copy(deep=True), NO_USAGE

    monkeypatch.setattr(llm, "structured_completion", always_bad)
    monkeypatch.setattr(retrieve, "retrieve", fake_retrieve)

    state = graph_lg.run(make_state(), db_path=db_path)

    assert state.status == "failed"
    assert state.repair_attempts == 2
    assert state.internal_error_code == errors.REPAIR_EXHAUSTED
    assert state.user_visible_error == (
        "We couldn't generate a reliable resolution for this ticket. "
        "It has been routed to a human agent."
    )
    assert order_api.refund_post_count == 0
    event_types = [e for _, e, _ in store.events_since("req-recover01", 0, db_path)]
    assert "run.failed" in event_types
