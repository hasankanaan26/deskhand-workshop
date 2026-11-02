"""SQLite persistence: the case record, the audit log, the outbox, the clock.

The case row is the durable thing. A pass of the engine is a short process
that reads the row, decides, writes the row and exits; nothing waits in
memory (the brief's Option B). Connections are opened per call, as in
checkpoints/02-service/store.py, so the API and the worker can share one
file: WAL lets readers and the writer get on with it.

Every function takes `db_path=None`, which means "the module's DB_PATH as it
is right now": tests point DB_PATH at a temp file.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from models import Case, Draft

DB_PATH = Path(os.getenv("DUNNING_DB") or Path(__file__).resolve().parent / "dunning.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id         TEXT PRIMARY KEY,
    tenant          TEXT NOT NULL,
    customer_name   TEXT NOT NULL,
    customer_email  TEXT NOT NULL,
    customer_tz     TEXT NOT NULL,
    invoice_id      TEXT NOT NULL,
    invoice_due     TEXT NOT NULL,
    amount_usd      REAL NOT NULL,
    balance_usd     REAL NOT NULL,
    status          TEXT NOT NULL,
    stage_history   TEXT NOT NULL DEFAULT '{}',
    next_due_at     TEXT,
    claimed_at      TEXT,
    paused_until    TEXT,
    pending_stage   TEXT,
    pending_draft   TEXT,
    approved_stage  TEXT,
    policy_version  INTEGER,
    owner           TEXT,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (          -- the audit log, append-only
    case_id         TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    at              TEXT NOT NULL,           -- simulated time
    kind            TEXT NOT NULL,
    actor           TEXT NOT NULL,           -- system | model | person | customer | timer
    policy_version  INTEGER,
    data_json       TEXT NOT NULL,
    PRIMARY KEY (case_id, seq)
);
CREATE TABLE IF NOT EXISTS messages (        -- the outbox; nothing is really sent
    message_key     TEXT PRIMARY KEY,        -- case_id:stage:policy_version
    case_id         TEXT NOT NULL,
    stage           TEXT NOT NULL,
    channel         TEXT NOT NULL,
    tone            TEXT NOT NULL,
    subject         TEXT,
    body            TEXT NOT NULL,
    drafted_by      TEXT NOT NULL,           -- model | template
    sent_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clock (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    now_iso         TEXT,                    -- the simulated instant; NULL = wall clock
    worker_seen_at  TEXT                     -- wall time of the worker's last tick
);
CREATE TABLE IF NOT EXISTS policy_versions (
    tenant          TEXT NOT NULL,
    version         INTEGER NOT NULL,
    policy_json     TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    PRIMARY KEY (tenant, version)
);
"""


def _path(db_path: Path | None) -> Path:
    return Path(db_path) if db_path else DB_PATH


def _connect(db_path: Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(_path(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO clock (id, now_iso, worker_seen_at) VALUES (1, NULL, NULL)")


def reset_db(db_path: Path | None = None) -> None:
    """Drop everything. seed.py --reset only. Tables are dropped rather than
    the file deleted, so it works while the API and the worker are running."""
    with _connect(db_path) as conn:
        for table in ("cases", "events", "messages", "clock", "policy_versions"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    init_db(db_path)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def now_wall() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

_CASE_COLUMNS = (
    "case_id, tenant, customer_name, customer_email, customer_tz, invoice_id, invoice_due,"
    " amount_usd, balance_usd, status, stage_history, next_due_at, claimed_at, paused_until,"
    " pending_stage, pending_draft, approved_stage, policy_version, owner, updated_at"
)


def _row_to_case(row: tuple) -> Case:
    (case_id, tenant, name, email, tz, invoice_id, invoice_due, amount, balance, status,
     history, next_due_at, claimed_at, paused_until, pending_stage, pending_draft,
     approved_stage, policy_version, owner, updated_at) = row
    return Case(
        case_id=case_id, tenant=tenant, customer_name=name, customer_email=email,
        customer_tz=tz, invoice_id=invoice_id, invoice_due=invoice_due,
        amount_usd=amount, balance_usd=balance, status=status,
        stage_history=json.loads(history or "{}"),
        next_due_at=_dt(next_due_at), claimed_at=_dt(claimed_at), paused_until=_dt(paused_until),
        pending_stage=pending_stage,
        pending_draft=Draft.model_validate_json(pending_draft) if pending_draft else None,
        approved_stage=approved_stage, policy_version=policy_version, owner=owner,
        updated_at=_dt(updated_at),
    )


def save_case(case: Case, db_path: Path | None = None) -> None:
    """Upsert the whole row. The engine calls this after every change, so a
    crash between two passes loses nothing: the next pass reads the row."""
    case.updated_at = now_wall()
    with _connect(db_path) as conn:
        conn.execute(
            f"INSERT INTO cases ({_CASE_COLUMNS}) VALUES ({', '.join('?' * 20)})"
            " ON CONFLICT(case_id) DO UPDATE SET"
            " tenant=excluded.tenant, customer_name=excluded.customer_name,"
            " customer_email=excluded.customer_email, customer_tz=excluded.customer_tz,"
            " invoice_id=excluded.invoice_id, invoice_due=excluded.invoice_due,"
            " amount_usd=excluded.amount_usd, balance_usd=excluded.balance_usd,"
            " status=excluded.status, stage_history=excluded.stage_history,"
            " next_due_at=excluded.next_due_at, claimed_at=excluded.claimed_at,"
            " paused_until=excluded.paused_until, pending_stage=excluded.pending_stage,"
            " pending_draft=excluded.pending_draft, approved_stage=excluded.approved_stage,"
            " policy_version=excluded.policy_version, owner=excluded.owner,"
            " updated_at=excluded.updated_at",
            (
                case.case_id, case.tenant, case.customer_name, case.customer_email,
                case.customer_tz, case.invoice_id, case.invoice_due.isoformat(),
                case.amount_usd, case.balance_usd, case.status,
                json.dumps(case.stage_history), _iso(case.next_due_at), _iso(case.claimed_at),
                _iso(case.paused_until), case.pending_stage,
                case.pending_draft.model_dump_json() if case.pending_draft else None,
                case.approved_stage, case.policy_version, case.owner, _iso(case.updated_at),
            ),
        )


def get_case(case_id: str, db_path: Path | None = None) -> Case | None:
    with _connect(db_path) as conn:
        row = conn.execute(f"SELECT {_CASE_COLUMNS} FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    return _row_to_case(row) if row else None


def list_cases(db_path: Path | None = None) -> list[Case]:
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT {_CASE_COLUMNS} FROM cases ORDER BY case_id").fetchall()
    return [_row_to_case(r) for r in rows]


def find_open_case_by_email(email: str, db_path: Path | None = None) -> Case | None:
    """The inbound path's lookup: a reply is matched to the customer's open
    case by the address it came from."""
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {_CASE_COLUMNS} FROM cases WHERE lower(customer_email) = lower(?)"
            " AND status NOT IN ('settled', 'escalated') ORDER BY case_id LIMIT 1",
            (email.strip(),),
        ).fetchone()
    return _row_to_case(row) if row else None


def scheduled_cases(db_path: Path | None = None) -> list[tuple[str, datetime]]:
    """Every case with a wake-up on the books: (case_id, next_due_at)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT case_id, next_due_at FROM cases WHERE next_due_at IS NOT NULL"
        ).fetchall()
    return [(case_id, _dt(due)) for case_id, due in rows]


# The lease: a worker that claimed a case and died must not block it forever.
# A claim older than this (wall time) is up for grabs again.
LEASE_SECONDS = 60


def claim_due_cases(now: datetime, db_path: Path | None = None, limit: int = 20) -> list[str]:
    """The scheduler's one query. Cases whose wake-up time has come, claimed
    in the same statement so two workers never evaluate one case together."""
    wall = now_wall()
    # ISO strings in one format sort as text, so the lease check is a string
    # comparison: no date parsing inside SQLite.
    lease_before = (wall - timedelta(seconds=LEASE_SECONDS)).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            "UPDATE cases SET claimed_at = ? WHERE case_id IN ("
            "  SELECT case_id FROM cases WHERE next_due_at IS NOT NULL AND next_due_at <= ?"
            "   AND status IN ('active', 'paused')"
            "   AND (claimed_at IS NULL OR claimed_at < ?)"
            "   ORDER BY next_due_at LIMIT ?)"
            " RETURNING case_id",
            (wall.isoformat(), now.isoformat(), lease_before, limit),
        ).fetchall()
    return [r[0] for r in rows]


def last_event(case_id: str, kind: str, db_path: Path | None = None) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT rowid, case_id, seq, at, kind, actor, policy_version, data_json FROM events"
            " WHERE case_id = ? AND kind = ? ORDER BY seq DESC LIMIT 1", (case_id, kind)
        ).fetchone()
    return _row_to_event(row) if row else None


# ---------------------------------------------------------------------------
# The audit log
# ---------------------------------------------------------------------------

def append_event(
    case_id: str, at: datetime, kind: str, actor: str, policy_version: int | None,
    data: dict[str, Any], db_path: Path | None = None,
) -> int:
    with _connect(db_path) as conn:
        (seq,) = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE case_id = ?", (case_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO events (case_id, seq, at, kind, actor, policy_version, data_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_id, seq, at.isoformat(), kind, actor, policy_version,
             json.dumps(data, ensure_ascii=False, default=str)),
        )
    return seq


def _row_to_event(row: tuple) -> dict:
    rowid, case_id, seq, at, kind, actor, policy_version, data_json = row
    return {"id": rowid, "case_id": case_id, "seq": seq, "at": at, "kind": kind,
            "actor": actor, "policy_version": policy_version, "data": json.loads(data_json)}


def events_for(case_id: str, db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT rowid, case_id, seq, at, kind, actor, policy_version, data_json"
            " FROM events WHERE case_id = ? ORDER BY seq", (case_id,)
        ).fetchall()
    return [_row_to_event(r) for r in rows]


def feed(after: int = 0, limit: int = 200, db_path: Path | None = None) -> list[dict]:
    """Events across every case, in the order they were written (the console's poll)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT rowid, case_id, seq, at, kind, actor, policy_version, data_json"
            " FROM events WHERE rowid > ? ORDER BY rowid LIMIT ?", (after, limit)
        ).fetchall()
    return [_row_to_event(r) for r in rows]


# ---------------------------------------------------------------------------
# The outbox
# ---------------------------------------------------------------------------

def insert_message(
    message_key: str, case_id: str, stage: str, channel: str, tone: str, subject: str | None,
    body: str, drafted_by: str, sent_at: datetime, db_path: Path | None = None,
) -> bool:
    """True if the message was new. A known key inserts nothing: that is the
    idempotency key doing for reminders what it did for refunds."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO messages"
            " (message_key, case_id, stage, channel, tone, subject, body, drafted_by, sent_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message_key, case_id, stage, channel, tone, subject, body, drafted_by, sent_at.isoformat()),
        )
    return cur.rowcount == 1


def messages_for(case_id: str, db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT message_key, stage, channel, tone, subject, body, drafted_by, sent_at"
            " FROM messages WHERE case_id = ? ORDER BY sent_at, rowid", (case_id,)
        ).fetchall()
    keys = ("message_key", "stage", "channel", "tone", "subject", "body", "drafted_by", "sent_at")
    return [dict(zip(keys, r)) for r in rows]


def sent_times(case_id: str, db_path: Path | None = None) -> list[datetime]:
    """When each message to this case went out (the weekly cap counts these)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT sent_at FROM messages WHERE case_id = ? ORDER BY sent_at", (case_id,)
        ).fetchall()
    return [datetime.fromisoformat(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# The clock and the worker's heartbeat
# ---------------------------------------------------------------------------

def get_clock(db_path: Path | None = None) -> datetime | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT now_iso FROM clock WHERE id = 1").fetchone()
    return _dt(row[0]) if row and row[0] else None


def set_clock(now: datetime | None, db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO clock (id, now_iso) VALUES (1, ?)"
            " ON CONFLICT(id) DO UPDATE SET now_iso = excluded.now_iso", (_iso(now),)
        )


def touch_worker(db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO clock (id, worker_seen_at) VALUES (1, ?)"
            " ON CONFLICT(id) DO UPDATE SET worker_seen_at = excluded.worker_seen_at",
            (now_wall().isoformat(),),
        )


def worker_seen_at(db_path: Path | None = None) -> datetime | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT worker_seen_at FROM clock WHERE id = 1").fetchone()
    return _dt(row[0]) if row and row[0] else None


# ---------------------------------------------------------------------------
# Policy versions: the audit copy of every version the engine has applied
# ---------------------------------------------------------------------------

def record_policy_version(
    tenant: str, version: int, policy_json: str, first_seen: datetime, db_path: Path | None = None
) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO policy_versions (tenant, version, policy_json, first_seen)"
            " VALUES (?, ?, ?, ?)", (tenant, version, policy_json, first_seen.isoformat())
        )
    return cur.rowcount == 1


def policy_versions(db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT tenant, version, first_seen FROM policy_versions ORDER BY tenant, version"
        ).fetchall()
    return [{"tenant": t, "version": v, "first_seen": f} for t, v, f in rows]
