"""The retrieval metric maths and the regression delta, offline."""
from __future__ import annotations

import pytest

from evals import kb_regression
from evals import kb_retrieval_metrics as m

EXPECTED = {"manual-aerofit-lite"}


def test_hit_at_k() -> None:
    ranked = ["manual-aerofit-earbuds", "manual-aerofit-lite", "past-tickets"]
    assert m.hit_at(ranked, EXPECTED, 1) == 0.0
    assert m.hit_at(ranked, EXPECTED, 2) == 1.0
    assert m.hit_at([], EXPECTED, 5) == 0.0


def test_reciprocal_rank() -> None:
    assert m.reciprocal_rank(["manual-aerofit-lite"], EXPECTED) == 1.0
    assert m.reciprocal_rank(["x", "y", "manual-aerofit-lite"], EXPECTED) == pytest.approx(1 / 3)
    # Rank 9: Hit@5 says nothing, MRR still gives partial credit.
    ranked = ["x"] * 8 + ["manual-aerofit-lite"]
    assert m.hit_at(ranked, EXPECTED, 5) == 0.0
    assert m.reciprocal_rank(ranked, EXPECTED) == pytest.approx(1 / 9)
    assert m.reciprocal_rank(["x"], EXPECTED) == 0.0


def test_recall_counts_each_expected_document_once() -> None:
    expected = {"carrier-eastvale-depot", "macro-shipping-delays"}
    # Two chunks of one document are still one of two documents.
    ranked = ["carrier-eastvale-depot", "carrier-eastvale-depot", "x", "y", "z",
              "macro-shipping-delays"]
    assert m.recall_at(ranked, expected, 5) == 0.5
    assert m.recall_at(ranked, expected, 6) == 1.0
    with pytest.raises(ValueError):
        m.recall_at(ranked, set(), 5)


def _row(qid, kind, scores, leaks=0, stale=0):
    return {"id": qid, "kind": kind, "scores": scores, "leaks": leaks,
            "stale_hits": stale, "latency_ms": 10}


def test_summarize_scores_only_questions_with_an_answer() -> None:
    rows = [
        _row("q1", "single", m.score_question(["a"], {"a"})),
        _row("q2", "multi-hop", m.score_question(["x", "a", "b"], {"a", "b"})),
        _row("q3", "not-in-kb", None),
        _row("q4", "acl-trap", None, leaks=0),
        _row("q5", "acl-trap", None, leaks=2),
    ]
    s = m.summarize(rows)
    assert s["questions_scored"] == 2
    assert s["hit@1"] == 0.5
    assert s["hit@5"] == 1.0
    assert s["mrr"] == pytest.approx((1 + 0.5) / 2)
    assert s["recall@5"] == 1.0
    assert s["leaks"] == 2
    assert s["acl_trap_pass"] == "1/2"
    assert s["hit@5_by_kind"] == {"multi-hop": 1.0, "single": 1.0}


def test_regression_flags_drops_beyond_tolerance_and_any_leak() -> None:
    baseline = {"dense": {"hit@1": 0.60, "hit@5": 0.90, "mrr": 0.70, "recall@5": 0.85,
                          "leaks": 0, "stale_hits": 0}}
    current = {"dense": {"hit@1": 0.59, "hit@5": 0.80, "mrr": 0.75, "recall@5": 0.85,
                         "leaks": 1, "stale_hits": 0},
               "hybrid": {"hit@1": 0.1}}  # not in the baseline: not compared
    rows = {(r["mode"], r["metric"]): r for r in kb_regression.deltas(baseline, current)}
    assert not rows[("dense", "hit@1")]["regressed"]  # -0.01 is noise
    assert rows[("dense", "hit@5")]["regressed"]      # -0.10 is not
    assert rows[("dense", "hit@5")]["delta"] == pytest.approx(-0.10)
    assert not rows[("dense", "mrr")]["regressed"]
    assert rows[("dense", "leaks")]["regressed"]      # any leak is a regression
    assert ("hybrid", "hit@1") not in rows


def test_lost_questions_names_what_fell_out() -> None:
    hit, miss = {"hit@5": 1.0}, {"hit@5": 0.0}
    before = {"dense": [{"id": "kb-13", "scores": hit}, {"id": "kb-14", "scores": miss}]}
    after = {"dense": [{"id": "kb-13", "scores": miss}, {"id": "kb-14", "scores": hit}]}
    assert kb_regression.lost_questions(before, after) == [("dense", "kb-13")]
