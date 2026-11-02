"""Rule 6 (Session 7): refund eligibility is decided in code, from the order
record. The return window from delivered_at, and final-sale items.

Same style as test_rules.py: a valid plan by default, one field changed per
test, and a fixed clock so the dates never drift.
"""
from __future__ import annotations

from datetime import datetime, timezone

import rules
from models import Action, ResolutionPlan

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)  # Session 7, Monday morning

CHUNKS = [{"doc_id": "refund-policy", "section_id": "window", "version": "2.3",
           "effective": "2026-06-01", "text": "Refunds within 14 days.", "score": 0.9}]


def order(delivered_at: str | None, *items: dict) -> dict:
    items = items or ({"sku": "NB-HL-050", "name": "Halo Smart Desk Lamp",
                       "price_usd": 48.5, "qty": 1},)
    return {
        "order_id": "O-77201",
        "customer_name": "Marcus Bell",
        "items": list(items),
        "total_usd": round(sum(i["price_usd"] * i["qty"] for i in items), 2),
        "status": "delivered" if delivered_at else "processing",
        "delivered_at": delivered_at,
    }


def refund(amount: float = 30.0, **overrides) -> ResolutionPlan:
    defaults = dict(
        plan_version="v1", ticket_id="T-1010", category="refund_request",
        actions=[Action(type="issue_refund")], refund_amount_usd=amount,
        requires_approval=False, confidence=0.9,
        citations=["refund-policy#window"], rationale="test plan",
    )
    defaults.update(overrides)
    return ResolutionPlan(**defaults)


def window_violations(plan: ResolutionPlan, order_data: dict) -> list[str]:
    return [v for v in rules.check_plan(plan, order_data, CHUNKS, now=NOW)
            if "refund window" in v]


def test_the_business_chose_fourteen_days() -> None:
    # The 14 vs 30 contradiction is resolved once, in code (see rules.py).
    assert rules.RETURN_WINDOW_DAYS == 14


def test_refund_inside_the_window_passes() -> None:
    assert rules.check_plan(refund(), order("2026-10-22T15:00:00+00:00"), CHUNKS, now=NOW) == []


def test_day_fourteen_is_inside_day_fifteen_is_outside() -> None:
    # Calendar days: delivered late on 19 Oct is still 14 days at 9am on 2 Nov.
    assert window_violations(refund(), order("2026-10-19T23:30:00Z")) == []
    outside = window_violations(refund(), order("2026-10-18T08:00:00Z"))
    assert len(outside) == 1
    assert "delivered 15 days ago" in outside[0]


def test_t1010_twenty_one_days_is_refused_whatever_the_model_read() -> None:
    # T-1010 / T-1026: the planted contradiction. The plan cites the 30-day
    # section and refunds $48.50 at 21 days. The citation is real (rule 5
    # passes); the refund is still not allowed.
    plan = refund(48.5, citations=["returns-and-exchanges#returns-window"])
    chunks = CHUNKS + [{"doc_id": "returns-and-exchanges", "section_id": "returns-window",
                        "version": "1.1", "effective": "2025-11-15",
                        "text": "30 days", "score": 0.88}]
    violations = rules.check_plan(plan, order("2026-10-12T10:00:00Z"), chunks, now=NOW)
    assert violations == [v for v in violations if "refund window" in v]
    assert "21 days" in violations[0]
    assert "reply_only" in violations[0]  # the repair prompt says what to do instead


def test_no_delivery_date_means_the_window_does_not_apply() -> None:
    # A processing order or a lost parcel is shipping-sla's business.
    assert window_violations(refund(), order(None)) == []


def test_the_window_only_constrains_refunds() -> None:
    reply = refund(None, actions=[Action(type="reply_only")], citations=[])
    assert rules.check_plan(reply, order("2026-09-01T10:00:00Z"), CHUNKS, now=NOW) == []


def test_final_sale_item_cannot_be_refunded() -> None:
    # T-1015: the Pulse Fitness Band on O-46633, final sale in its item name.
    band = {"sku": "NB-PF-CLR", "name": "Pulse Fitness Band (Clearance — Final Sale)",
            "price_usd": 24.99, "qty": 1}
    violations = rules.check_plan(refund(24.99), order("2026-10-27T10:00:00Z", band),
                                  CHUNKS, now=NOW)
    assert len(violations) == 1
    assert "final sale" in violations[0]
    assert "$0.00" in violations[0]


def test_final_sale_flag_counts_too() -> None:
    item = {"sku": "X", "name": "Plain name", "price_usd": 10.0, "qty": 1, "final_sale": True}
    assert any("final sale" in v for v in
               rules.check_plan(refund(10.0), order("2026-10-27T10:00:00Z", item), CHUNKS, now=NOW))


def test_other_items_on_a_final_sale_order_stay_refundable() -> None:
    band = {"sku": "NB-PF-CLR", "name": "Pulse Fitness Band (Final Sale)", "price_usd": 24.99, "qty": 1}
    lamp = {"sku": "NB-HL-050", "name": "Halo Smart Desk Lamp", "price_usd": 20.0, "qty": 1}
    mixed = order("2026-10-27T10:00:00Z", band, lamp)
    assert rules.check_plan(refund(20.0), mixed, CHUNKS, now=NOW) == []
    assert any("at most $20.00" in v for v in rules.check_plan(refund(30.0), mixed, CHUNKS, now=NOW))


def test_rule_six_without_a_clock_uses_today() -> None:
    # The service passes no `now`: rule 6 reads the real clock.
    today = datetime.now(timezone.utc).isoformat()
    long_ago = "2020-01-01T00:00:00Z"
    assert rules.check_plan(refund(), order(today), CHUNKS) == []
    assert any("refund window" in v for v in rules.check_plan(refund(), order(long_ago), CHUNKS))


# Rule 5, extended: kb: citations are grounded like policy ones, and never
# stand in for a policy citation on a refund.
KB_CHUNKS = [{"kb_id": "kb:carrier-holiday-2026#delays", "doc_id": "carrier-holiday-2026",
              "text": "Holiday delays of up to 5 days.", "score": 0.8}]


def test_retrieved_kb_citation_passes() -> None:
    plan = refund(citations=["refund-policy#window", "kb:carrier-holiday-2026#delays"])
    assert rules.check_plan(plan, order("2026-10-27T10:00:00Z"), CHUNKS,
                            kb_chunks=KB_CHUNKS, now=NOW) == []


def test_invented_kb_citation_fails() -> None:
    plan = refund(citations=["refund-policy#window", "kb:goodwill-macro#credit"])
    violations = rules.check_plan(plan, order("2026-10-27T10:00:00Z"), CHUNKS,
                                  kb_chunks=KB_CHUNKS, now=NOW)
    assert any("knowledge-base section" in v for v in violations)


def test_kb_citation_alone_does_not_authorise_a_refund() -> None:
    plan = refund(citations=["kb:carrier-holiday-2026#delays"])
    violations = rules.check_plan(plan, order("2026-10-27T10:00:00Z"), CHUNKS,
                                  kb_chunks=KB_CHUNKS, now=NOW)
    assert any("kb: sources do not count" in v for v in violations)


def test_kb_citation_on_a_reply_needs_no_policy() -> None:
    reply = refund(None, category="shipping_delay", actions=[Action(type="reply_only")],
                   citations=["kb:carrier-holiday-2026#delays"])
    assert rules.check_plan(reply, order("2026-10-27T10:00:00Z"), CHUNKS,
                            kb_chunks=KB_CHUNKS, now=NOW) == []
