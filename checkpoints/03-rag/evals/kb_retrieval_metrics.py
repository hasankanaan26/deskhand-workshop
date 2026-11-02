"""Measure knowledge-base retrieval without an LLM judge: Hit@k, MRR, Recall@5.

Session 9, "diagnose before you fix". shared/kb/eval_questions.json holds
about 30 questions written the way customers and agents type, each with the
documents that answer it (`expected_doc_ids`) and the reader asking
(`caller_group`). Every question is searched in each mode, as that reader,
and the ranked chunks are compared with the expected documents in code:

- Hit@1 / Hit@5: is a chunk of an expected document in the first 1 / 5?
  Five is what research_kb shows the planner (nodes.KB_TOP_K).
- MRR (mean reciprocal rank): 1 / the rank of the first expected chunk,
  averaged. 1.0 = always first, 0.5 = always second, and a hit at rank 9
  still earns 0.11 where Hit@5 gives it nothing. Searched to depth 20.
- Recall@5: the share of the expected documents found in the first 5 (a
  multi-hop question needs two documents, and one of two is 0.5).

A chunk counts for its document: any row of past-tickets.csv is a hit for
"past-tickets". Two kinds of question have no expected document and are
scored apart: `not-in-kb` (the answer is not there; the top score is
printed, since a ranker always returns something), and `acl-trap` (a
customer fishing for a staff-only macro; it passes when no returned chunk
lacks the customer group). Every mode also counts leaks (a chunk the caller
may not read) and stale hits (a superseded chunk): both must be 0, because
the filters run inside the search.

Retrieval only: one embedding call for all questions; the LLM reranker (used
when no cross-encoder is cached, see kb_search.py) adds one chat call per
question in that mode, about $0.002 each on gpt-4.1-mini.

Run:  py evals\\kb_retrieval_metrics.py                 (all three modes)
      py evals\\kb_retrieval_metrics.py --modes dense   (just one)
Writes evals/runs/kb-<timestamp>.json; py evals\\kb_regression.py compares it
with evals/kb_baseline.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kb_search  # noqa: E402
import llm  # noqa: E402
from kb_ingest import KB_DIR  # noqa: E402

QUESTIONS_PATH = KB_DIR / "eval_questions.json"
RUNS_DIR = Path(__file__).resolve().parent / "runs"
# The three modes Session 9 compares, one lever at a time.
MODES = {
    "dense": {"mode": "dense", "rerank": False},
    "hybrid": {"mode": "hybrid", "rerank": False},
    "hybrid+rerank": {"mode": "hybrid", "rerank": True},
}
DEPTH = 20  # chunks fetched per question: MRR looks this deep
UNSCORED_KINDS = {"not-in-kb", "acl-trap"}  # no expected document to find
METRICS = ("hit@1", "hit@5", "mrr", "recall@5")


# ---------------------------------------------------------------------------
# The metric maths (tests/test_kb_metrics.py)
# ---------------------------------------------------------------------------

def hit_at(ranked: list[str], expected: set[str], k: int) -> float:
    """1.0 if any of the first k ranked doc ids is expected, else 0.0."""
    return 1.0 if any(doc in expected for doc in ranked[:k]) else 0.0


def reciprocal_rank(ranked: list[str], expected: set[str]) -> float:
    """1 / (rank of the first expected doc id), 0.0 if none is ranked."""
    for rank, doc in enumerate(ranked, start=1):
        if doc in expected:
            return 1.0 / rank
    return 0.0


def recall_at(ranked: list[str], expected: set[str], k: int) -> float:
    """The share of expected doc ids among the first k ranked (each once)."""
    if not expected:
        raise ValueError("recall is undefined without expected documents")
    return len(set(ranked[:k]) & expected) / len(expected)


def score_question(ranked: list[str], expected: set[str]) -> dict[str, float]:
    return {
        "hit@1": hit_at(ranked, expected, 1),
        "hit@5": hit_at(ranked, expected, 5),
        "mrr": reciprocal_rank(ranked, expected),
        "recall@5": recall_at(ranked, expected, 5),
    }


def mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarize(rows: list[dict]) -> dict:
    """Averages over the scored questions of one mode, plus the safety counts
    over all of them and Hit@5 per question kind."""
    scored = [r for r in rows if r["scores"] is not None]
    summary = {m: mean([r["scores"][m] for r in scored]) for m in METRICS}
    summary["questions_scored"] = len(scored)
    summary["leaks"] = sum(r["leaks"] for r in rows)
    summary["stale_hits"] = sum(r["stale_hits"] for r in rows)
    acl = [r for r in rows if r["kind"] == "acl-trap"]
    summary["acl_trap_pass"] = f"{sum(r['leaks'] == 0 for r in acl)}/{len(acl)}"
    summary["hit@5_by_kind"] = {
        kind: mean([r["scores"]["hit@5"] for r in scored if r["kind"] == kind])
        for kind in sorted({r["kind"] for r in scored})
    }
    summary["ms_per_question"] = mean([r["latency_ms"] for r in rows])
    return summary


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

def load_questions(path: Path = QUESTIONS_PATH) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw["questions"] if isinstance(raw, dict) else raw


def run_mode(name: str, questions: list[dict], vectors: list[list[float]]) -> tuple[list[dict], float]:
    settings = MODES[name]
    rows, cost = [], 0.0
    for question, vector in zip(questions, vectors):
        group = question["caller_group"]
        result = kb_search.search_kb(
            question["question"], [group], k=DEPTH, query_vector=vector, **settings
        )
        cost += result.usage.cost_usd
        ranked = [c["doc_id"] for c in result.chunks]
        expected = set(question["expected_doc_ids"])
        unscored = question["kind"] in UNSCORED_KINDS or not expected
        rows.append({
            "id": question["id"],
            "kind": question["kind"],
            "caller_group": group,
            "expected": sorted(expected),
            "ranked": ranked[:10],
            "first_expected_rank": next(
                (n for n, d in enumerate(ranked, start=1) if d in expected), None),
            "top_score": result.chunks[0]["score"] if result.chunks else None,
            "scores": None if unscored else score_question(ranked, expected),
            "leaks": sum(group not in (c["allowed_groups"] or []) for c in result.chunks),
            "stale_hits": sum(c["status"] != "current" for c in result.chunks),
            "reranker": result.trace.get("reranker"),
            "latency_ms": result.trace["latency_ms"],
        })
    return rows, cost


def print_tables(summaries: dict[str, dict], rows_by_mode: dict[str, list[dict]]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Knowledge-base retrieval (doc-level match, chunk ranks)")
    for col in ("mode", "Hit@1", "Hit@5", "MRR", "Recall@5", "leaks", "stale", "acl-trap", "ms/q"):
        table.add_column(col, justify="left" if col == "mode" else "right")
    for name, s in summaries.items():
        table.add_row(name, *(f"{s[m]:.2f}" for m in METRICS), str(s["leaks"]),
                      str(s["stale_hits"]), s["acl_trap_pass"], f"{s['ms_per_question']:.0f}")
    console.print(table)

    kinds = sorted({k for s in summaries.values() for k in s["hit@5_by_kind"]})
    by_kind = Table(title="Hit@5 by question kind")
    by_kind.add_column("kind")
    for name in summaries:
        by_kind.add_column(name, justify="right")
    for kind in kinds:
        by_kind.add_row(kind, *(f"{summaries[n]['hit@5_by_kind'].get(kind, 0):.2f}" for n in summaries))
    console.print(by_kind)

    # The misses, with where the first right chunk actually was.
    misses = Table(title="Scored questions not in the top 5 (first expected rank, depth 20)")
    misses.add_column("id")
    misses.add_column("kind")
    for name in summaries:
        misses.add_column(name, justify="right")
    ids = [r["id"] for r in next(iter(rows_by_mode.values()))]
    for qid in ids:
        per_mode = {n: next(r for r in rows if r["id"] == qid) for n, rows in rows_by_mode.items()}
        first = next(iter(per_mode.values()))
        if first["scores"] is None or all(r["scores"]["hit@5"] for r in per_mode.values()):
            continue
        misses.add_row(qid, first["kind"], *(str(r["first_expected_rank"] or "-") for r in per_mode.values()))
    console.print(misses)

    unscored = Table(title="not-in-kb: top score per mode (a ranker always returns something)")
    unscored.add_column("id")
    for name in summaries:
        unscored.add_column(name, justify="right")
    for qid in ids:
        per_mode = [next(r for r in rows if r["id"] == qid) for rows in rows_by_mode.values()]
        if per_mode[0]["kind"] == "not-in-kb":
            unscored.add_row(qid, *(f"{r['top_score']}" for r in per_mode))
    console.print(unscored)


def main(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description="knowledge-base retrieval metrics")
    parser.add_argument("--modes", default=",".join(MODES),
                        help=f"comma-separated, from {list(MODES)}")
    args = parser.parse_args(argv)
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = set(modes) - set(MODES)
    if unknown:
        raise SystemExit(f"unknown mode(s) {sorted(unknown)}; choose from {list(MODES)}")

    questions = load_questions()
    # Every question embedded once, in one call, and reused by every mode:
    # the modes differ in ranking, not in the query vector.
    vectors, usage = llm.embed_texts([q["question"] for q in questions])
    cost = usage.cost_usd
    summaries, rows_by_mode = {}, {}
    for name in modes:
        rows, mode_cost = run_mode(name, questions, vectors)
        cost += mode_cost
        rows_by_mode[name], summaries[name] = rows, summarize(rows)
        rerankers = {r["reranker"] for r in rows if r["reranker"]}
        if rerankers:
            summaries[name]["reranker"] = ", ".join(sorted(rerankers))

    print_tables(summaries, rows_by_mode)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out = RUNS_DIR / f"kb-{stamp}.json"
    out.write_text(json.dumps({
        "created_at": stamp,
        "embedding_model": llm.EMBED_MODEL,
        "questions": len(questions),
        "cost_usd": round(cost, 6),
        "summary": summaries,
        "rows": rows_by_mode,
    }, indent=2), encoding="utf-8")
    for name, s in summaries.items():
        if "reranker" in s:
            print(f"{name}: reranker = {s['reranker']}")
    print(f"{len(questions)} questions, {sum(s['questions_scored'] for s in summaries.values()) // max(len(summaries), 1)} "
          f"scored per mode; cost ${cost:.4f}; wrote {out}")
    return out


if __name__ == "__main__":
    main()
