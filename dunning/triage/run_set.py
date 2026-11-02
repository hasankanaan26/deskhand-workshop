"""Run the real pipeline over the labelled set. Two model calls per reply: this costs money.

    py run_set.py                    all 30 replies
    py run_set.py --only R-05,R-12   just these
    py run_set.py --limit 5          the first five

One row per reply: the gold intent and the model's, the expected action and
the actual one, when it runs, what happens to the reply, and the failure
codes. Then plain counts. No score, no accuracy figure: 30 hand-labelled
replies are a set to read row by row, not a benchmark.

Every run is also written to runs/<timestamp>.jsonl (gitignored), with the
model's reading and proposal in full, so you can read the disagreements.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _dunning_folder() -> Path:
    """The folder with llm.py. Next to dunning/triage that's the parent; in the
    homework copy (resolvo-workshop/triage-live) it's ../dunning."""
    for candidate in (HERE.parent, HERE.parent / "dunning"):
        if (candidate / "llm.py").exists():
            return candidate
    raise SystemExit("can't find dunning/llm.py next to this folder")


# Appended, not inserted: this folder's models.py and policy.py must win over
# the dunning engine's files of the same name.
sys.path.insert(0, str(HERE))
sys.path.append(str(_dunning_folder()))

import data  # noqa: E402
import policy as policy_module  # noqa: E402
from pipeline import triage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run the triage pipeline on the labelled replies (real model calls)")
    ap.add_argument("--only", help="comma-separated reply ids, e.g. R-05,R-12")
    ap.add_argument("--limit", type=int, help="run only the first N replies")
    args = ap.parse_args(argv)

    accounts, now = data.load_accounts()
    replies = data.load_replies()
    policy = policy_module.load()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        replies = [r for r in replies if r.id in wanted]
    if args.limit:
        replies = replies[: args.limit]

    runs = HERE / "runs"
    runs.mkdir(exist_ok=True)
    out = runs / f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.jsonl"

    print(f"{'id':<5} {'gold intent':<24} {'model intent':<24} {'expected':<21} {'actual':<21} "
          f"{'runs':<15} {'reply':<20} failures")
    intents_agree = dispositions_agree = escalated = errors = 0
    cost = 0.0
    with out.open("w", encoding="utf-8") as log:
        for r in replies:
            exp = r.expected
            try:
                result = triage(r, accounts[r.account_id], now, policy=policy)
            except Exception as exc:  # a provider error or malformed output: note it, carry on
                errors += 1
                print(f"{r.id:<5} {r.gold_reading.intent:<24} ERROR {type(exc).__name__}: {exc}")
                log.write(json.dumps({"id": r.id, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
                continue
            d = result.disposition
            codes = [f.code for f in result.failures]
            intents_agree += result.reading.intent == r.gold_reading.intent
            same = (d.action, d.runs, d.reply, d.escalated, codes) == (
                exp.action, exp.runs, exp.reply, exp.escalated, exp.failure_codes)
            dispositions_agree += same
            escalated += d.escalated
            cost += sum(u.cost_usd for u in result.usage if u is not None)
            print(f"{r.id:<5} {r.gold_reading.intent:<24} {result.reading.intent:<24} {exp.action:<21} "
                  f"{d.action:<21} {d.runs:<15} {d.reply:<20} {','.join(codes) or '-'}")
            log.write(json.dumps({
                "id": r.id,
                "gold_intent": r.gold_reading.intent,
                "expected": exp.model_dump(),
                "reading": result.reading.model_dump(),
                "match": result.match.model_dump(),
                "proposal": result.proposal.model_dump(),
                "disposition": d.model_dump(),
                "usage": [u.model_dump() if u is not None else None for u in result.usage],
            }, ensure_ascii=False) + "\n")

    done = len(replies) - errors
    print()
    print(f"replies run:            {done} of {len(replies)}" + (f" ({errors} errors)" if errors else ""))
    print(f"intent agrees with gold: {intents_agree} of {done}")
    print(f"disposition agrees:      {dispositions_agree} of {done}  (action, runs, reply and failure codes)")
    print(f"escalated:               {escalated} of {done}")
    print(f"cost:                    ${cost:.4f}  (from llm.Usage; local models report $0)")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
