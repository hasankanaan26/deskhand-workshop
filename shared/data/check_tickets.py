"""Dataset checks for shared/data/tickets.json (stdlib only).

Run from the repo root:  py shared\\data\\check_tickets.py

The dataset is hand-written, so this is the only thing that keeps it honest:
every ticket has exactly the six documented keys, every tag is in the
vocabulary, every order id either exists in the mock API seed or is listed
here as deliberately unknown, and every ticket that is an attack or an
identity mismatch has a golden entry that forbids a refund. Checkpoint 04's
tests/test_dataset.py runs the same checks offline.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
TICKETS_PATH = DATA_DIR / "tickets.json"
ORDERS_PATH = DATA_DIR.parent / "mock_order_api" / "orders_seed.json"
GOLDEN_PATH = (
    DATA_DIR.parent.parent / "checkpoints" / "04-production" / "golden" / "golden_tickets.json"
)

TICKET_KEYS = {"ticket_id", "customer_name", "order_id", "channel", "messages", "tags"}
CHANNELS = {"email", "chat", "web"}
SENDERS = {"customer", "agent"}

# The tag vocabulary. v1 tags describe the ticket's difficulty; v2 tags
# describe its SHAPE, so one ticket can carry several.
TAGS_V1 = {
    "happy_path", "ambiguous", "angry", "out_of_policy", "injection",
    "no_order_ref", "long_thread", "contradiction_trigger",
}
TAGS_V2 = {
    "injection_no_trigger",   # attack with no textbook trigger phrase
    "injection_indirect",     # attack arrives through the order record, not the ticket
    "injection_role_spoof",   # customer pastes a fake [agent] turn
    "injection_obfuscated",   # zero-width chars, homoglyphs, base64
    "social_engineering",     # no attack phrasing at all; identity / payment games
    "customer_mismatch",      # ticket customer is not the order's customer
    "benign_lookalike",       # genuine ticket that trips naive heuristics
    "partial_refund",
    "boundary",               # sits on a policy threshold ($50, day 14/15)
    "multi_item",
    "thread",                 # 2-3 messages, agent turns inside
    "quoted_reply",           # email with signature and quoted history
    "non_english",
    "over_length",            # longer than normalize_ticket keeps
    "duplicate",              # second ticket for an already-handled order
    "cancel",                 # order not yet delivered
}
TAGS = TAGS_V1 | TAGS_V2

# Order ids that are referenced on purpose but do not exist in the seed
# (the mock API answers 404). Anything else unknown is a typo.
UNKNOWN_ORDERS = {"O-90112", "O-88104", "O-29003", "O-66720", "O-73391"}

# Tickets with any of these tags must have a golden entry whose
# must_not_action_types contains issue_refund.
MUST_FORBID_REFUND = {
    "injection", "injection_no_trigger", "injection_indirect",
    "injection_role_spoof", "injection_obfuscated", "customer_mismatch",
    "social_engineering",
}


def load(path: Path, key: str) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))[key]


def check(tickets: list[dict], orders: list[dict], golden: list[dict]) -> list[str]:
    """Return a list of problems (empty = dataset is consistent)."""
    problems: list[str] = []
    order_ids = {o["order_id"] for o in orders}
    golden_by_id = {g["ticket_id"]: g for g in golden}

    ids = [t.get("ticket_id") for t in tickets]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    if dupes:
        problems.append(f"duplicate ticket ids: {dupes}")

    for t in tickets:
        tid = t.get("ticket_id", "<missing id>")
        keys = set(t.keys())
        if keys != TICKET_KEYS:
            problems.append(f"{tid}: keys {sorted(keys ^ TICKET_KEYS)} differ from the schema")
            continue
        if not t["customer_name"].strip():
            problems.append(f"{tid}: empty customer_name")
        if t["channel"] not in CHANNELS:
            problems.append(f"{tid}: channel {t['channel']!r} not in {sorted(CHANNELS)}")
        if not t["messages"]:
            problems.append(f"{tid}: no messages")
        for i, m in enumerate(t["messages"]):
            if set(m.keys()) != {"from", "text"} or m["from"] not in SENDERS or not m["text"].strip():
                problems.append(f"{tid}: message {i} malformed")
        if not t["tags"]:
            problems.append(f"{tid}: no tags")
        for tag in t["tags"]:
            if tag not in TAGS:
                problems.append(f"{tid}: unknown tag {tag!r}")
        oid = t["order_id"]
        if oid is not None and oid not in order_ids and oid not in UNKNOWN_ORDERS:
            problems.append(f"{tid}: order {oid} is neither in the seed nor in UNKNOWN_ORDERS")
        if oid is None and "no_order_ref" not in t["tags"]:
            problems.append(f"{tid}: order_id is null but the ticket is not tagged no_order_ref")
        if MUST_FORBID_REFUND & set(t["tags"]):
            entry = golden_by_id.get(tid)
            if entry is None:
                problems.append(f"{tid}: attack/mismatch ticket has no golden entry")
            elif "issue_refund" not in entry.get("must_not_action_types", []):
                problems.append(f"{tid}: golden entry does not forbid issue_refund")

    for g in golden:
        if g["ticket_id"] not in set(ids):
            problems.append(f"golden {g['ticket_id']}: not in the dataset")

    return problems


def tag_table(tickets: list[dict], golden: list[dict]) -> str:
    counts = Counter(tag for t in tickets for tag in t["tags"])
    golden_ids = {g["ticket_id"] for g in golden}
    covered = Counter(tag for t in tickets if t["ticket_id"] in golden_ids for tag in t["tags"])
    width = max(len(tag) for tag in counts)
    lines = [f"{'tag':<{width}}  tickets  in golden set"]
    for tag in sorted(counts, key=lambda k: (k in TAGS_V2, k)):
        lines.append(f"{tag:<{width}}  {counts[tag]:>7}  {covered[tag]:>13}")
    return "\n".join(lines)


def main() -> int:
    tickets = load(TICKETS_PATH, "tickets")
    orders = load(ORDERS_PATH, "orders")
    golden = load(GOLDEN_PATH, "golden_tickets")
    problems = check(tickets, orders, golden)
    print(f"{len(tickets)} tickets, {len(orders)} orders, {len(golden)} golden entries\n")
    print(tag_table(tickets, golden))
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
