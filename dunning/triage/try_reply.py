"""Run one reply through the five steps and show what each step did.

    py try_reply.py --list                     the 30 labelled replies
    py try_reply.py R-13                       with the labels: no model, free, same every time
    py try_reply.py R-13 --live                the real model (two calls, costs a little)
    py try_reply.py --account A-01 --text "We paid last week, ref ACME-OCT-240." --live
                                               your own reply, to one of the accounts
    py try_reply.py R-05 --override '{"proposal": {"pause_until": "2027-01-10"}}'
                                               what if the model had proposed this? (labels only)

"With the labels" plays a careful person's reading and proposal through the
real match, check and gate: it shows what the harness does, with no model in
the loop. "--live" replaces the person with the model, so you can compare.
A reply you write yourself has no labels, so it needs --live.

--override changes fields of the labelled reading or proposal before match,
check and gate run: "what if the model had read it as X, or proposed Y?".
It is free and shows which check catches each mistake.

--json prints one JSON object instead of the text, for the dunning console's
Triage view (app.py calls this script in a separate process, because this
folder's models.py and the engine's models.py share a name).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _dunning_folder() -> Path:
    """The folder with llm.py: the parent here, ../dunning in triage-live."""
    for candidate in (HERE.parent, HERE.parent / "dunning"):
        if (candidate / "llm.py").exists():
            return candidate
    raise SystemExit("can't find dunning/llm.py next to this folder")


# Appended, not inserted: this folder's models.py and policy.py must win.
sys.path.insert(0, str(HERE))
sys.path.append(str(_dunning_folder()))

import data  # noqa: E402
import pipeline  # noqa: E402
from models import Reply  # noqa: E402


def _usage(u) -> dict | None:
    if u is None:
        return None
    return {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens, "cost_usd": u.cost_usd}


def list_replies() -> dict:
    accounts, now = data.load_accounts()
    replies = data.load_replies()
    return {
        "now": now.isoformat(),
        "accounts": [{"account_id": a.account_id, "name": a.name} for a in accounts.values()],
        "replies": [{
            "id": r.id, "account_id": r.account_id, "account_name": accounts[r.account_id].name,
            "subject": r.subject, "intent": r.gold_reading.intent, "hard_because": r.hard_because,
            "expected_action": r.expected.action,
        } for r in replies],
    }


def run(reply_id: str | None, account_id: str | None, text: str | None, live: bool,
        override: dict | None = None) -> dict:
    accounts, now = data.load_accounts()
    labelled = None
    if reply_id:
        by_id = {r.id: r for r in data.load_replies()}
        if reply_id not in by_id:
            raise SystemExit(f"no reply {reply_id}; try --list")
        labelled = by_id[reply_id]
        reply: Reply = labelled
    else:
        if account_id not in accounts:
            raise SystemExit(f"no account {account_id}; accounts: {', '.join(accounts)}")
        if not (text or "").strip():
            raise SystemExit("--text is empty")
        if not live:
            raise SystemExit("a reply you wrote has no labels: add --live to use the model")
        account = accounts[account_id]
        reply = Reply.model_validate({
            "id": "your-reply", "account_id": account_id, "from": account.contact_email,
            "subject": "Re: your invoice", "body": text.strip(),
            "received_at": now.isoformat()})
    account = accounts[reply.account_id]

    if override and (live or labelled is None):
        raise SystemExit("--override works on a labelled reply, without --live")
    if live:
        result = pipeline.triage(reply, account, now)
    else:
        # The labels stand in for the model; an override edits them first.
        # model_validate re-checks every field, so an override can't smuggle
        # in a value the model's schema would reject.
        o = override or {}
        reading = type(labelled.gold_reading).model_validate({**labelled.gold_reading.model_dump(), **o.get("reading", {})})
        proposal = type(labelled.gold_proposal).model_validate({**labelled.gold_proposal.model_dump(), **o.get("proposal", {})})
        result = pipeline.triage(
            reply, account, now,
            read_fn=lambda _reply, _now: (reading, None),
            propose_fn=lambda *_args: (proposal, None))

    out = {
        "mode": "model" if live else ("override" if override else "labels"),
        "now": now.isoformat(),
        "reply": {"id": reply.id, "from": reply.from_, "subject": reply.subject,
                  "body": reply.body, "received_at": reply.received_at},
        "account": {
            "account_id": account.account_id, "name": account.name, "contact_email": account.contact_email,
            "open_invoices": [{"invoice_id": i.invoice_id, "balance_usd": i.balance_usd, "due": i.due,
                               "items": [it.model_dump() for it in i.items]} for i in account.open_invoices()],
            "unmatched_payments": [p.model_dump() for p in account.unmatched_payments],
        },
        "reading": result.reading.model_dump(),
        "match": result.match.model_dump(),
        "proposal": result.proposal.model_dump(),
        "failures": [f.model_dump() for f in result.failures],
        "disposition": result.disposition.model_dump(),
        "usage": [_usage(u) for u in result.usage],
        "labelled": None,
    }
    if labelled is not None:
        exp = labelled.expected
        got = result.disposition
        out["labelled"] = {
            "hard_because": labelled.hard_because,
            "gold_intent": labelled.gold_reading.intent,
            "gold_action": labelled.gold_proposal.action,
            "expected": exp.model_dump(),
            "intent_agrees": result.reading.intent == labelled.gold_reading.intent,
            "outcome_agrees": (got.action, got.runs, got.reply, got.escalated,
                               sorted(f.code for f in got.failures))
                              == (exp.action, exp.runs, exp.reply, exp.escalated, sorted(exp.failure_codes)),
        }
    return out


def _print(o: dict) -> None:
    r, rd, m, p, d = o["reply"], o["reading"], o["match"], o["proposal"], o["disposition"]
    who = {"labels": "the labels, no model", "override": "the labels, edited", "model": "the model"}[o["mode"]]
    print(f"{r['id']} from {r['from']} to {o['account']['name']}: {r['subject']}")
    print("  " + r["body"].replace("\n", "\n  "))
    print(f"\n1 read     ({who})")
    print(f"  intent {rd['intent']}" + (f" + {', '.join(rd['other_intents'])}" if rd["other_intents"] else "")
          + f"   confidence {rd['confidence']:.2f}" + ("   churn risk" if rd["churn_risk"] else ""))
    for k in ("invoice_refs", "payment_reference", "amounts_usd", "promised_date", "return_date", "forward_to_email"):
        if rd[k] not in (None, []):
            print(f"  {k}: {rd[k]}")
    for item in rd["disputed_items"]:
        print(f"  disputed: {item['item']} ({item['reason']})")
    for q in rd["evidence"]:
        print(f"  quote: \"{q}\"")
    print("\n2 match    (code)")
    print(f"  {m['method']}: {', '.join(m['invoice_ids']) or 'no invoice'}"
          + ("   ambiguous" if m["ambiguous"] else "")
          + (f"   not theirs or not open: {', '.join(m['unknown_refs'])}" if m["unknown_refs"] else ""))
    print(f"\n3 propose  ({who})")
    print(f"  {p['action']} {', '.join(p['invoice_ids'])}".rstrip())
    if p["pause_until"]:
        print(f"  until {p['pause_until']}")
    for i in p["instalments"]:
        print(f"  instalment ${i['amount_usd']:,.2f} on {i['due']}")
    if p["new_contact_email"]:
        print(f"  new contact {p['new_contact_email']}")
    if p["draft_reply"]:
        print("  draft: " + p["draft_reply"].replace("\n", "\n         "))
    print("\n4 check    (code)")
    print("  all passed" if not o["failures"] else "\n".join(f"  FAIL {f['code']}: {f['detail']}" for f in o["failures"]))
    print("\n5 gate     (code)")
    print(f"  {d['action']}, runs {d['runs']}; reply {d['reply']}" + ("; a person takes the case" if d["escalated"] else ""))
    lab = o["labelled"]
    if lab:
        e = lab["expected"]
        print(f"\nlabel: {lab['gold_intent']} -> {e['action']}, {e['runs']}, reply {e['reply']}"
              + (f", fails {', '.join(e['failure_codes'])}" if e["failure_codes"] else ""))
        print(f"  hard because: {lab['hard_because']}")
        if o["mode"] == "model":
            print(f"  intent {'agrees' if lab['intent_agrees'] else 'DIFFERS'}; outcome {'agrees' if lab['outcome_agrees'] else 'DIFFERS'}")
    cost = sum(u["cost_usd"] for u in o["usage"] if u)
    if o["mode"] == "model":
        print(f"\ncost ${cost:.4f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run one reply through read, match, propose, check, gate")
    ap.add_argument("reply_id", nargs="?", help="a labelled reply, e.g. R-13")
    ap.add_argument("--list", action="store_true", help="list the labelled replies")
    ap.add_argument("--live", action="store_true", help="use the real model (two calls, costs a little)")
    ap.add_argument("--account", help="for your own reply: the account, e.g. A-01")
    ap.add_argument("--text", help="for your own reply: the email body")
    ap.add_argument("--override", help='JSON: {"reading": {...}, "proposal": {...}} fields to change in the labels')
    ap.add_argument("--json", action="store_true", help="print JSON (for the console)")
    args = ap.parse_args(argv)
    if args.list:
        out = list_replies()
        if args.json:
            print(json.dumps(out))
        else:
            for r in out["replies"]:
                print(f"{r['id']}  {r['account_name']:<18} {r['intent']:<24} {r['hard_because']}")
        return 0
    if not args.reply_id and not args.account:
        ap.error("give a reply id (see --list), or --account and --text with --live")
    override = json.loads(args.override) if args.override else None
    out = run(args.reply_id, args.account, args.text, args.live, override)
    if args.json:
        print(json.dumps(out))
    else:
        _print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
