"""The clock the engine reads: simulated in class, the wall clock at work.

A collections case lives for weeks, and a class lives for two hours. So the
engine never calls datetime.now() for business time; it calls clock.now(),
which in `sim` mode returns one stored instant that moves only when someone
advances it (the console's buttons, `py cli.py --advance 3`). The API, the
worker and the CLI all read the same row, so they agree on what day it is.

DUNNING_CLOCK=wall makes now() the real UTC clock, which is what production
would run on. Nothing else in the engine changes.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import store

UTC = timezone.utc


def mode() -> str:
    return (os.getenv("DUNNING_CLOCK") or "sim").strip().lower()


def now(db_path: Path | None = None) -> datetime:
    if mode() == "wall":
        return datetime.now(UTC)
    stored = store.get_clock(db_path)
    if stored is None:
        # First use with nothing seeded: start the simulated clock at the
        # real time, on the hour, so the first advance lands on a round time.
        stored = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        store.set_clock(stored, db_path)
    return stored


def set_to(instant: datetime, db_path: Path | None = None) -> datetime:
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    store.set_clock(instant.astimezone(UTC), db_path)
    return instant


def advance(days: int = 0, hours: int = 0, minutes: int = 0, db_path: Path | None = None) -> datetime:
    """Move the simulated clock forward. Never backwards: the audit log is
    append-only and its times must not run in reverse."""
    if mode() == "wall":
        raise RuntimeError("DUNNING_CLOCK=wall: the real clock cannot be advanced")
    delta = timedelta(days=days, hours=hours, minutes=minutes)
    if delta < timedelta(0):
        raise ValueError("the clock only moves forward")
    return set_to(now(db_path) + delta, db_path)


def day_label(instant: datetime, invoice_due) -> str:
    """'D+3' for three days after the invoice due date, 'D-2' before it."""
    offset = (instant.date() - invoice_due).days
    return f"D{offset:+d}" if offset else "D"
