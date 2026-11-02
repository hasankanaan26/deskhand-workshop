"""Load the two data files: accounts (with the fixed "now") and the labelled replies."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from models import Account, LabelledReply

DATA = Path(__file__).resolve().parent / "data"


def load_accounts(path: Path | None = None) -> tuple[dict[str, Account], datetime]:
    """(accounts by id, now). The whole set is judged at one fixed instant,
    so "before today" means the same thing on every run."""
    raw = json.loads(Path(path or DATA / "accounts.json").read_text(encoding="utf-8"))
    now = datetime.fromisoformat(raw["now"].replace("Z", "+00:00"))
    accounts = [Account.model_validate(a) for a in raw["accounts"]]
    return {a.account_id: a for a in accounts}, now


def load_replies(path: Path | None = None) -> list[LabelledReply]:
    raw = json.loads(Path(path or DATA / "replies.json").read_text(encoding="utf-8"))
    return [LabelledReply.model_validate(r) for r in raw]
