"""Offline tests for evals/rag_evals.py.

The ID-based context metrics need no LLM, so RAGAS really runs here.
Retrieval and the judge's chat model are replaced at our boundary.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import llm
import retrieve
from evals import rag_evals


def _case(ticket_id: str, retrieved: list[str], reference: list[str], rationale=None) -> dict:
    return {
        "ticket_id": ticket_id,
        "query": "q",
        "retrieved": retrieved,
        "retrieved_texts": [f"text of {r}" for r in retrieved],
        "reference": reference,
        "rationale": rationale,
        "judge_contexts": [],
        "cost_usd": 0.0001,
    }


def test_context_metrics_compare_section_ids() -> None:
    scores = rag_evals.score_context([
        _case("T-1", ["a#1", "b#1", "c#1"], ["a#1", "d#1"]),
    ])
    # precision: 1 of the 3 retrieved is labelled. recall: 1 of the 2 labelled was retrieved.
    assert scores["T-1"][rag_evals.PRECISION] == pytest.approx(1 / 3)
    assert scores["T-1"][rag_evals.RECALL] == pytest.approx(1 / 2)


def test_starving_the_retriever_trades_recall_for_precision() -> None:
    """The README's TOP_K = 1 exercise, in miniature."""
    reference = ["refund-policy#window", "returns-and-exchanges#returns-window"]
    wide = rag_evals.score_context([_case("T", [
        "refund-policy#window", "returns-and-exchanges#returns-window",
        "refund-policy#scope", "warranty#coverage",
    ], reference)])["T"]
    narrow = rag_evals.score_context([_case("T", ["refund-policy#window"], reference)])["T"]

    assert narrow[rag_evals.RECALL] < wide[rag_evals.RECALL]
    assert narrow[rag_evals.PRECISION] > wide[rag_evals.PRECISION]


def test_tickets_without_reference_sections_are_not_scored() -> None:
    cases = [_case("T-1", ["a#1"], ["a#1"]), _case("T-2", ["a#1"], [])]
    scores = rag_evals.score_context(cases)
    assert set(scores) == {"T-1"}

    rows = rag_evals.merge(cases, scores)
    assert rows[1]["scores"] == {}
    assert rag_evals.summarize(rows)[rag_evals.RECALL] == {"mean": 1.0, "n": 1}


def test_merge_lists_missed_sections_and_turns_nan_into_none() -> None:
    rows = rag_evals.merge(
        [_case("T-1", ["a#1"], ["a#1", "b#1"])],
        {"T-1": {rag_evals.RECALL: 0.5, rag_evals.FAITHFULNESS: float("nan")}},
    )
    assert rows[0]["missed"] == ["b#1"]
    assert rows[0]["scores"] == {rag_evals.RECALL: 0.5, rag_evals.FAITHFULNESS: None}
    assert rag_evals.FAITHFULNESS not in rag_evals.summarize(rows)


def test_retrieval_case_uses_the_service_query(monkeypatch) -> None:
    # If the eval built its own query, it would measure a search the service never runs.
    seen: list[tuple] = []

    def fake_retrieve(query, k=None, client=None):
        seen.append((query, k))
        chunk = {"doc_id": "refund-policy", "section_id": "window", "text": "14 days",
                 "score": 0.9, "version": "2.3", "effective": "2026-06-01"}
        return [chunk], llm.Usage(prompt_tokens=5, completion_tokens=0, cost_usd=0.0001)

    monkeypatch.setattr(retrieve, "retrieve", fake_retrieve)
    entry = {"ticket_id": "T-1001", "expected_category": "refund_request",
             "reference_contexts": ["refund-policy#window"]}

    case = rag_evals.retrieval_case(entry, "cracked case")

    assert seen == [(retrieve.build_query("refund_request", "cracked case"), None)]
    assert case["retrieved"] == ["refund-policy#window"]
    assert case["cost_usd"] == 0.0001


def _fake_chunk_retrieve(seen: list):
    def fake_retrieve(query, k=None, client=None):
        seen.append(query)
        chunk = {"doc_id": "escalation-matrix", "section_id": "when-to-escalate",
                 "text": "escalate when…", "score": 0.8, "version": "1.0",
                 "effective": "2026-01-01"}
        return [chunk], llm.Usage(prompt_tokens=5, completion_tokens=0, cost_usd=0.0001)
    return fake_retrieve


def test_query_styles_restyle_the_ticket_text_not_the_category(monkeypatch) -> None:
    """rewrite and hyde each make one model call per ticket; the result
    replaces the ticket text inside build_query, so the category prefix stays
    and the generated text is what gets logged as the query."""
    prompts: list[list[dict]] = []

    def fake_completion(messages, **kwargs):
        prompts.append(messages)
        return "  When must an angry customer with a chargeback threat be escalated?  ", llm.Usage(
            prompt_tokens=80, completion_tokens=14, cost_usd=0.00002
        )

    seen: list[str] = []
    monkeypatch.setattr(llm, "text_completion", fake_completion)
    monkeypatch.setattr(retrieve, "retrieve", _fake_chunk_retrieve(seen))
    entry = {"ticket_id": "T-1005", "expected_category": "refund_request",
             "reference_contexts": ["escalation-matrix#when-to-escalate"]}

    case = rag_evals.retrieval_case(entry, "dead pixels, I will do a chargeback", "rewrite")

    assert seen == [retrieve.build_query(
        "refund_request", "When must an angry customer with a chargeback threat be escalated?"
    )]
    assert case["query"] == seen[0]
    assert case["query_style"] == "rewrite"
    assert case["cost_usd"] == pytest.approx(0.0001 + 0.00002)
    assert case["query_cost_usd"] == pytest.approx(0.00002)
    assert len(prompts) == 1
    assert prompts[0][0]["content"] == rag_evals.REWRITE_PROMPT
    assert prompts[0][-1]["content"] == "dead pixels, I will do a chargeback"

    rag_evals.retrieval_case(entry, "dead pixels", "hyde")
    assert prompts[1][0]["content"] == rag_evals.HYDE_PROMPT

    # plain is the service's query: no model call, no extra cost
    plain = rag_evals.retrieval_case(entry, "dead pixels", "plain")
    assert len(prompts) == 2
    assert plain["query"] == retrieve.build_query("refund_request", "dead pixels")
    assert plain["query_cost_usd"] == 0

    with pytest.raises(ValueError, match="unknown query style"):
        rag_evals.restyle_query("bm25", "dead pixels")


def test_generated_queries_print_on_a_cp1252_console() -> None:
    """A rewritten query with a character cp1252 can't encode must not crash
    the report (it did: rich writes the table through the console encoding)."""
    text = "refund_request: what is the policy on → returns 中" + "x" * 100
    cell = rag_evals._printable(text, encoding="cp1252")
    assert cell.endswith("...") and len(cell) == 93
    assert "→" not in cell and "?" in cell
    cell.encode("cp1252")  # really printable now
    assert rag_evals._printable("short", encoding="utf-8") == "short"


def test_query_style_needs_retrieval_only() -> None:
    with pytest.raises(SystemExit):
        rag_evals.main(["--query-style", "rewrite"])


def test_main_records_the_query_style_and_the_generated_queries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rag_evals, "RESULTS_PATH", tmp_path / "rag_results.json")
    monkeypatch.setattr(llm, "text_completion", lambda messages, **kw: (
        "a search question", llm.Usage(prompt_tokens=10, completion_tokens=4, cost_usd=0.00001)))
    monkeypatch.setattr(retrieve, "retrieve", _fake_chunk_retrieve([]))

    assert rag_evals.main(["--retrieval-only", "--query-style", "hyde"]) == 0

    results = json.loads((tmp_path / "rag_results.json").read_text(encoding="utf-8"))
    assert results["query_style"] == "hyde"
    assert results["per_ticket"][0]["query"].endswith(": a search question")
    # 46 golden tickets x ($0.0001 search + $0.00001 rewrite)
    assert results["cost_usd"]["pipeline"] == pytest.approx(46 * 0.00011, abs=1e-6)


def test_faithfulness_skips_cases_without_a_rationale(monkeypatch) -> None:
    monkeypatch.setattr(rag_evals, "ragas_llm", lambda: pytest.fail("no judge needed"))
    scores, usage = rag_evals.score_faithfulness([_case("T-1", ["a#1"], ["a#1"])])
    assert scores == {}
    assert usage.cost_usd == 0


def test_judge_wrapper_never_sends_temperature_to_claude(monkeypatch) -> None:
    monkeypatch.setattr(llm, "fresh_chat_model", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(llm, "PROVIDER", "anthropic")
    assert rag_evals.ragas_llm().bypass_temperature is True
    monkeypatch.setattr(llm, "PROVIDER", "azure")
    wrapper = rag_evals.ragas_llm()
    assert wrapper.bypass_temperature is False
    assert wrapper.bypass_n is True


def test_judge_usage_is_priced_with_our_table() -> None:
    # The provider reports a dated model name; the price table matches it by
    # family. 1M input tokens at gpt-4.1-mini's $0.40 per million.
    meter = SimpleNamespace(usage_metadata={
        "gpt-4.1-mini-2025-04-14": {"input_tokens": 1_000_000, "output_tokens": 0},
    })
    usage = rag_evals.judge_usage(meter)
    assert usage.prompt_tokens == 1_000_000
    assert usage.cost_usd == pytest.approx(0.40)


def test_main_retrieval_only_writes_results(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(retrieve, "TOP_K", retrieve.TOP_K)  # restored afterwards
    monkeypatch.setattr(rag_evals, "RESULTS_PATH", tmp_path / "rag_results.json")

    def fake_retrieve(query, k=None, client=None):
        chunk = {"doc_id": "refund-policy", "section_id": "window", "text": "14 days",
                 "score": 0.9, "version": "2.3", "effective": "2026-06-01"}
        return [chunk][: retrieve.TOP_K], llm.Usage(
            prompt_tokens=5, completion_tokens=0, cost_usd=0.0001
        )

    monkeypatch.setattr(retrieve, "retrieve", fake_retrieve)

    assert rag_evals.main(["--retrieval-only", "--k", "1"]) == 0

    results = json.loads((tmp_path / "rag_results.json").read_text(encoding="utf-8"))
    assert results["k"] == 1
    assert results["mode"] == "retrieval_only"
    assert results["cost_usd"]["judge"] == 0
    assert len(results["per_ticket"]) == 46
    assert results["summary"][rag_evals.RECALL]["n"] == 38  # 8 tickets need no policy


def test_pipeline_case_gives_the_judge_everything_the_planner_saw(monkeypatch) -> None:
    """Against the policy text alone, "delivered 5 days ago, $45" looks
    invented: it comes from the order record."""
    chunk = {"doc_id": "refund-policy", "section_id": "window", "text": "14 days",
             "score": 0.9, "version": "2.3", "effective": "2026-06-01"}
    state = SimpleNamespace(
        policy_chunks=[chunk], normalized_ticket="refund my $45 keyboard",
        order_data={"order_id": "O-51877", "total_usd": 45.0},
        resolution_plan=SimpleNamespace(rationale="Inside the window."), cost_usd=0.002,
    )
    monkeypatch.setattr(rag_evals, "plan_ticket", lambda ticket_id, text: state)
    entry = {"ticket_id": "T-1004", "reference_contexts": ["refund-policy#window"]}

    case = rag_evals.pipeline_case(entry, "raw ticket")

    assert case["retrieved"] == ["refund-policy#window"]
    assert case["judge_contexts"][0] == "14 days"
    assert "refund my $45 keyboard" in case["judge_contexts"][1]
    assert '"total_usd": 45.0' in case["judge_contexts"][2]
