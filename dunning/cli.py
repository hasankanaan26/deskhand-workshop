"""The same engine from the terminal, for the handout and for fallbacks.

    py cli.py --cases                        the four cards
    py cli.py --timeline C-1002              one case's audit log and outbox
    py cli.py --evaluate C-1001              one pass, by hand
    py cli.py --advance 3 [--hours 7]        move the simulated clock
    py cli.py --tick                         one scheduler pass (no worker needed)
    py cli.py --pay C-1001 120               the ledger's webhook
    py cli.py --inbound C-1002 "text..."     a customer reply (one model call)
    py cli.py --approve C-1003 / --reject C-1003
    py cli.py --resolve C-1002 60 "item 3 credited"
    py cli.py --resume C-1002
    py cli.py --draw                         the graph, as Mermaid
"""
from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console
from rich.table import Table

import clock
import engine
import store

console = Console(width=max(100, Console().width))


def show_cases() -> None:
    now = clock.now()
    table = Table(title=f"Cases at {now.strftime('%a %d %b %Y %H:%M')} UTC")
    for col in ("case", "customer", "tenant", "balance", "status", "day", "next wake-up", "policy"):
        table.add_column(col)
    for c in store.list_cases():
        table.add_row(
            c.case_id, c.customer_name, c.tenant, f"${c.balance_usd:.2f}", c.status,
            clock.day_label(now, c.invoice_due),
            c.next_due_at.strftime("%a %d %b %H:%M") if c.next_due_at else "-",
            f"v{c.policy_version}" if c.policy_version else "-",
        )
    console.print(table)


def show_timeline(case_id: str) -> None:
    case = store.get_case(case_id)
    if case is None:
        sys.exit(f"unknown case {case_id}")
    console.print(f"[bold]{case.case_id}[/] {case.customer_name} | {case.tenant} | "
                  f"invoice {case.invoice_id} due {case.invoice_due} | balance ${case.balance_usd:.2f} | {case.status}")
    table = Table(show_lines=False)
    for col in ("#", "at", "day", "kind", "actor", "policy"):
        table.add_column(col)
    table.add_column("details", overflow="fold")
    for e in store.events_for(case_id):
        at = e["at"][:16].replace("T", " ")
        day = clock.day_label(store._dt(e["at"]), case.invoice_due)
        details = ", ".join(f"{k}={v}" for k, v in e["data"].items()
                            if k not in ("body", "text") and v not in (None, ""))
        table.add_row(str(e["seq"]), at, day, e["kind"], e["actor"],
                      f"v{e['policy_version']}" if e["policy_version"] else "-", details[:110])
    console.print(table)
    for m in store.messages_for(case_id):
        console.print(f"[dim]--- {m['stage']} | {m['channel']} | {m['tone']} | drafted by {m['drafted_by']} ---[/]")
        if m["subject"]:
            console.print(f"[bold]{m['subject']}[/]")
        console.print(m["body"])


def report(result: dict) -> None:
    for line in result.get("log", []):
        console.print(f"  {line}")
    if "intent" in result:
        console.print(f"  intent: {json.dumps(result['intent'])}")
    console.print(f"[bold]{result['case_id']}[/]: {result['decision']} ({result['events_added']} audit lines)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", action="store_true")
    ap.add_argument("--timeline", metavar="CASE")
    ap.add_argument("--evaluate", metavar="CASE")
    ap.add_argument("--advance", type=int, metavar="DAYS")
    ap.add_argument("--hours", type=int, default=0)
    ap.add_argument("--tick", action="store_true")
    ap.add_argument("--pay", nargs=2, metavar=("CASE", "AMOUNT"))
    ap.add_argument("--inbound", nargs=2, metavar=("CASE", "TEXT"))
    ap.add_argument("--approve", metavar="CASE")
    ap.add_argument("--reject", metavar="CASE")
    ap.add_argument("--resolve", nargs=3, metavar=("CASE", "CREDIT", "NOTE"))
    ap.add_argument("--resume", metavar="CASE")
    ap.add_argument("--draw", action="store_true")
    ap.add_argument("--by", default="the terminal")
    args = ap.parse_args(argv)

    store.init_db()
    try:
        if args.draw:
            draw = getattr(engine, "draw", None)
            print(draw() if draw else "this engine.py has no draw()")
        elif args.cases:
            show_cases()
        elif args.timeline:
            show_timeline(args.timeline)
        elif args.evaluate:
            report(engine.evaluate(args.evaluate, "manual"))
        elif args.advance is not None or args.hours:
            now = clock.advance(days=args.advance or 0, hours=args.hours)
            console.print(f"clock: {now.strftime('%a %d %b %Y %H:%M')} UTC")
            show_cases()
        elif args.tick:
            results = engine.tick()
            store.touch_worker()
            console.print(f"tick: {len(results)} case(s) woke")
            for r in results:
                report(r)
        elif args.pay:
            report(engine.record_payment(args.pay[0], float(args.pay[1])))
        elif args.inbound:
            report(engine.record_inbound(args.inbound[0], "email", args.inbound[1]))
        elif args.approve:
            report(engine.decide_approval(args.approve, True, args.by))
        elif args.reject:
            report(engine.decide_approval(args.reject, False, args.by))
        elif args.resolve:
            report(engine.resolve_dispute(args.resolve[0], float(args.resolve[1]), args.resolve[2], args.by))
        elif args.resume:
            report(engine.resume_case(args.resume, args.by))
        else:
            ap.print_help()
    except (KeyError, PermissionError, ValueError) as exc:
        sys.exit(f"error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
