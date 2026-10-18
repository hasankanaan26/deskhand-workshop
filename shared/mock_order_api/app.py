"""Mock Nimbus Electronics order API.

Stands in for the real order system in every checkpoint. In-memory only —
restart it to reset refunds. Chaos modes simulate the real API misbehaving.
GET /refunds is the till: every refund issued since the process started.
"""
from __future__ import annotations

import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

SEED_PATH = Path(__file__).parent / "orders_seed.json"
CHAOS_MODES = {"none", "timeout", "error", "flaky", "inconsistent"}


def _load_orders() -> dict[str, dict]:
    """Load the seed, turning relative `delivered_days_ago` into absolute ISO
    timestamps at startup — so "delivered 12 days ago" stays true whenever the
    class runs, not just the day the seed was written."""
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    orders: dict[str, dict] = {}
    for order in seed["orders"]:
        days_ago = order.pop("delivered_days_ago")
        order["delivered_at"] = (
            (now - timedelta(days=days_ago)).isoformat() if days_ago is not None else None
        )
        orders[order["order_id"]] = order
    return orders


app = FastAPI(title="Nimbus Mock Order API")
ORDERS = _load_orders()
REFUNDS: dict[str, dict] = {}  # Idempotency-Key -> refund record
# Idempotency-Key -> when the refund was issued and how many POSTs asked for it
# (2+ means a retry got the original back). Kept apart so POST's reply is unchanged.
REFUND_LEDGER: dict[str, dict] = {}


def _chaos_mode(request: Request) -> str:
    """Per-request X-Chaos header wins over the MOCK_CHAOS env var."""
    mode = request.headers.get("X-Chaos") or os.getenv("MOCK_CHAOS", "none")
    if mode not in CHAOS_MODES:
        raise HTTPException(400, f"unknown chaos mode {mode!r}, expected one of {sorted(CHAOS_MODES)}")
    return mode


def _apply_chaos(mode: str) -> None:
    if mode == "timeout":
        time.sleep(30)
    elif mode == "error":
        raise HTTPException(500, "chaos: simulated internal error")
    elif mode == "flaky" and random.random() < 0.5:
        raise HTTPException(500, "chaos: simulated flaky failure")


@app.get("/orders/{order_id}")
def get_order(order_id: str, request: Request) -> dict:
    mode = _chaos_mode(request)
    _apply_chaos(mode)
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    payload = dict(order)
    if mode == "inconsistent":
        payload.pop("total_usd")  # the "real API returns garbage sometimes" scenario
    return payload


class RefundRequest(BaseModel):
    order_id: str
    amount_usd: float


@app.post("/refunds")
def issue_refund(
    body: RefundRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    _apply_chaos(_chaos_mode(request))
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header is required")
    if idempotency_key in REFUNDS:
        REFUND_LEDGER[idempotency_key]["post_count"] += 1
        return REFUNDS[idempotency_key]  # same key => same refund, never a double refund
    refund = {
        "refund_id": f"R-{uuid.uuid4().hex[:8]}",
        "order_id": body.order_id,
        "amount_usd": body.amount_usd,
        "status": "issued",
    }
    REFUNDS[idempotency_key] = refund
    REFUND_LEDGER[idempotency_key] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "post_count": 1,
    }
    return refund


@app.get("/refunds")
def list_refunds() -> dict:
    """Read-only: every refund issued since this process started, oldest first,
    so a class can count the money directly instead of searching logs.

    Chaos modes never apply here (no X-Chaos, no MOCK_CHAOS): this is the
    instructor's view of the till, not part of the API the services call, and
    a chaos error or a 30s hang must never hide money that moved. A replayed
    Idempotency-Key is still one refund (post_count says how often it was asked)."""
    refunds = [
        {**refund, "idempotency_key": key, **REFUND_LEDGER[key]}
        for key, refund in REFUNDS.items()
    ]
    return {
        "count": len(refunds),
        "total_usd": round(sum(r["amount_usd"] for r in refunds), 2),
        "refunds": refunds,
    }
