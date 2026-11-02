"""eval_stub.py — run the golden ticket set and print a pass/fail table.

This is the Session 9 -> 10 bridge: a first, deliberately simple eval. It runs
the PLANNING portion of the pipeline (through validate) for each golden ticket
and compares against expectations. No actions are executed and no replies are
drafted — we are grading decisions, not prose.

Needs: provider credentials in the repo .env, the mock order API on port 9000, and a
built index (`py ingest.py`). Checkpoint 04 grows this stub into a real eval
suite with thresholds and a release gate.

Run:  py eval_stub.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from rich.console import Console
from rich.table import Table

import nodes
import rules
from models import TicketResolutionState

# The golden set: real dataset tickets with the right answer written down by
# a person (category, action types, approval, documents to cite). The eval is
# only as good as these labels.
GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "golden_tickets.json"
TICKETS_PATH = (
    Path(__file__).resolve().parents[2] / "shared" / "data" / "tickets.json"
)


def load_customer_name(ticket_id: str, tickets: list[dict]) -> str | None:
    """Who the dataset says the ticket is from. The rules trust this field,
    not the name inside the text, which the model reads and an attacker writes."""
    for ticket in tickets:
        if ticket["ticket_id"] == ticket_id:
            return ticket["customer_name"]
    return None


def load_ticket_text(ticket_id: str, tickets: list[dict]) -> str | None:
    """The ticket as one text, built the way app.py builds it for a dataset
    ticket, so the eval grades the input the service would see."""
    for ticket in tickets:
        if ticket["ticket_id"] == ticket_id:
            header = f"Ticket {ticket_id} from {ticket['customer_name']}"
            if ticket.get("order_id"):
                header += f" (order {ticket['order_id']})"
            body = "\n\n".join(f"[{m['from']}] {m['text']}" for m in ticket["messages"])
            return f"{header}\n{body}"
    return None


def plan_ticket(
    ticket_id: str, ticket_text: str, customer_name: str | None = None
) -> TicketResolutionState:
    """Run the pipeline up to (and including) validation — no execution.

    customer_name defaults to the dataset record's, so the ownership rule runs
    in evals exactly as it does in the service (free-text callers pass None)."""
    if customer_name is None and ticket_id.startswith("T-") and TICKETS_PATH.exists():
        tickets = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))["tickets"]
        customer_name = load_customer_name(ticket_id, tickets)
    state = TicketResolutionState(
        request_id=f"eval-{uuid.uuid4().hex[:8]}",
        ticket_id=ticket_id,
        ticket_text=ticket_text,
        customer_name=customer_name,
    )
    no_emit = lambda event, data: 0  # noqa: E731 - no SSE audience here
    # The graph's own node functions, called in order by hand. Nothing after
    # validation runs, so no refund is issued and no reply is drafted. There
    # is no repair loop either: check_plan is called directly, not through
    # validate_plan, so "valid" grades the planner's first attempt.
    nodes.normalize_ticket(state, no_emit)
    nodes.triage(state, no_emit)
    nodes.fetch_order_data(state, no_emit)
    nodes.retrieve_policy(state, no_emit)
    nodes.generate_resolution_plan(state, no_emit)
    state.validation_errors = rules.check_plan(
        state.resolution_plan,
        state.order_data,
        state.policy_chunks,
        customer_name=state.customer_name,
    )
    return state


def grade(entry: dict, state: TicketResolutionState) -> dict[str, bool]:
    """Five pass/fail checks, all compared in code, no LLM judge.

    Actions compare as a set of types: order, count and amounts don't count.
    Citations are checked per document, not per section: citing any
    refund-policy section satisfies must_cite ["refund-policy"].
    """
    plan = state.resolution_plan
    assert plan is not None and state.triage is not None
    cited_docs = {c.split("#")[0] for c in plan.citations}
    return {
        "valid": not state.validation_errors,
        "category": state.triage.category == entry["expected_category"],
        "actions": {a.type for a in plan.actions}
        == set(entry["expected_action_types"]),
        "approval": plan.requires_approval == entry["expected_requires_approval"],
        "citations": set(entry["must_cite"]) <= cited_docs,
    }


def main() -> int:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["golden_tickets"]
    tickets = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))["tickets"]
    console = Console()

    table = Table(title=f"Golden set — {len(golden)} tickets")
    for column in ("ticket", "valid", "category", "actions", "approval",
                   "citations", "verdict"):
        table.add_column(column)

    passed = 0
    for entry in golden:
        ticket_text = load_ticket_text(entry["ticket_id"], tickets)
        if ticket_text is None:
            table.add_row(entry["ticket_id"], *["-"] * 5, "[yellow]missing[/]")
            continue
        try:
            state = plan_ticket(entry["ticket_id"], ticket_text)
            checks = grade(entry, state)
        except Exception as exc:  # a crash is a failed eval, not a crashed eval
            table.add_row(entry["ticket_id"], *["-"] * 5, f"[red]error[/] {exc}")
            continue
        ok = all(checks.values())
        passed += ok
        mark = lambda flag: "[green]pass[/]" if flag else "[red]FAIL[/]"  # noqa: E731
        table.add_row(
            entry["ticket_id"],
            *(mark(checks[k]) for k in ("valid", "category", "actions",
                                        "approval", "citations")),
            "[green]PASS[/]" if ok else "[red]FAIL[/]",
        )

    console.print(table)
    console.print(f"\n{passed}/{len(golden)} tickets fully passing")
    # A non-zero exit lets a script or CI notice a failure. All-or-nothing is
    # the stub's simplification; checkpoint 04 gates on per-metric thresholds.
    return 0 if passed == len(golden) else 1


if __name__ == "__main__":
    sys.exit(main())
