"""Compare a knowledge-base retrieval run with the saved baseline.

A retrieval change (a new chunk size, hybrid on, a reranker) helps some
questions and hurts others. The average can rise while the questions you
cared about drop, so this prints the delta per mode and metric, flags any
drop larger than TOLERANCE, and lists the questions that fell out of the
top 5. Exit code 1 on a regression, so a script or CI step can stop on it.

Run:  py evals\\kb_regression.py                    (the newest evals/runs/kb-*.json)
      py evals\\kb_regression.py --run evals\\runs\\kb-20261106T090000Z.json
      py evals\\kb_regression.py --save-baseline    (make the newest run the baseline)

The baseline is a file in the repo, evals/kb_baseline.json: changing it is a
reviewed commit, not a side effect of running the eval.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
RUNS_DIR = EVALS_DIR / "runs"
BASELINE_PATH = EVALS_DIR / "kb_baseline.json"
METRICS = ("hit@1", "hit@5", "mrr", "recall@5")
# A drop smaller than this is noise, not a regression. With ~27 scored
# questions one question moves Hit@5 by 0.037, so 0.03 flags a single loss.
TOLERANCE = 0.03


def deltas(baseline: dict, current: dict, tolerance: float = TOLERANCE) -> list[dict]:
    """One row per (mode, metric) present in both summaries: before, after,
    delta, and whether it regressed. Safety counts (leaks, stale hits) regress
    on ANY increase: there is no acceptable number of leaks."""
    rows = []
    for mode in current:
        if mode not in baseline:
            continue
        for metric in (*METRICS, "leaks", "stale_hits"):
            before, after = baseline[mode].get(metric), current[mode].get(metric)
            if before is None or after is None:
                continue
            delta = round(after - before, 4)
            if metric in METRICS:
                regressed = delta < -tolerance
            else:
                regressed = delta > 0
            rows.append({"mode": mode, "metric": metric, "before": before,
                         "after": after, "delta": delta, "regressed": regressed})
    return rows


def lost_questions(baseline_rows: dict, current_rows: dict) -> list[tuple[str, str]]:
    """(mode, question id) for questions in the baseline's top 5 that are
    not in the current run's top 5."""
    lost = []
    for mode, rows in current_rows.items():
        before = {r["id"]: r for r in baseline_rows.get(mode, [])}
        for row in rows:
            old = before.get(row["id"])
            if old and old["scores"] and row["scores"] and old["scores"]["hit@5"] and not row["scores"]["hit@5"]:
                lost.append((mode, row["id"]))
    return lost


def newest_run() -> Path:
    runs = sorted(RUNS_DIR.glob("kb-*.json"))
    if not runs:
        raise SystemExit("no runs yet — py evals\\kb_retrieval_metrics.py first")
    return runs[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="knowledge-base retrieval regression check")
    parser.add_argument("--run", type=Path, default=None, help="a kb-*.json run (default: newest)")
    parser.add_argument("--save-baseline", action="store_true",
                        help="copy the run to evals/kb_baseline.json and stop")
    args = parser.parse_args(argv)
    run_path = args.run or newest_run()

    if args.save_baseline:
        shutil.copyfile(run_path, BASELINE_PATH)
        print(f"baseline is now {run_path.name} -> {BASELINE_PATH}")
        return 0
    if not BASELINE_PATH.exists():
        raise SystemExit(f"no baseline at {BASELINE_PATH} — run with --save-baseline once")

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    current = json.loads(run_path.read_text(encoding="utf-8"))
    rows = deltas(baseline["summary"], current["summary"])
    print(f"{run_path.name} vs baseline {baseline.get('created_at', '?')} "
          f"(tolerance {TOLERANCE})\n")
    print(f"{'mode':<15}{'metric':<12}{'before':>8}{'after':>8}{'delta':>8}")
    for r in rows:
        flag = "  REGRESSED" if r["regressed"] else ""
        print(f"{r['mode']:<15}{r['metric']:<12}{r['before']:>8}{r['after']:>8}{r['delta']:>+8.3f}{flag}")
    lost = lost_questions(baseline.get("rows", {}), current.get("rows", {}))
    if lost:
        print("\nfell out of the top 5: " + ", ".join(f"{q} ({m})" for m, q in lost))
    regressed = any(r["regressed"] for r in rows)
    print("\nREGRESSION" if regressed else "\nno regression")
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
