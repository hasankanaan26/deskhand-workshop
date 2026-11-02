"""send_now: how the whiteboard version sends. No questions asked.

steps.py (the version everyone writes first) calls this after each wait. It
fills in the template, writes the message, and records `reminder.sent` with
actor "steps" and no policy version: in that design there is no policy
record to point at, because the cadence is the code.

Every call is a new message (the key is unique per call), the way a plain
email API behaves when nobody gives it an idempotency key. The engine never
uses this: it writes the outbox itself, keyed case:stage:version, and only
after rules.py says the message may go.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import clock
import drafting
import store
from models import Case, Stage


def send_now(case: Case, stage: str, channel: str, tone: str, db_path: Path | None = None) -> str:
    """Send one reminder now, for the balance on `case`. Returns the message key."""
    now = clock.now(db_path)
    text = drafting.template_draft(case, Stage(name=stage, day=0, channel=channel, tone=tone), now)
    key = f"{case.case_id}:{stage}:{uuid.uuid4().hex[:8]}"
    store.insert_message(key, case.case_id, stage, channel, tone, text.subject, text.body,
                         "template", now, db_path)
    store.append_event(case.case_id, now, "reminder.sent", "steps", None,
                       {"stage": stage, "channel": channel, "tone": tone,
                        "balance_usd": case.balance_usd, "message_key": key,
                        "subject": text.subject}, db_path)
    return key
