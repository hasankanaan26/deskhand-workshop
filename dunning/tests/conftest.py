"""Offline fixtures: no model, no Redis, no real clock, no real log."""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import llm  # noqa: E402
import obslog  # noqa: E402
import policy  # noqa: E402
import queues as queue_module  # noqa: E402
import store  # noqa: E402
from models import Draft, InboundIntent  # noqa: E402

UTC = timezone.utc


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Every test gets its own database, log file, policy file and queue."""
    db = tmp_path / "dunning.db"
    monkeypatch.setattr(store, "DB_PATH", db)
    monkeypatch.setattr(obslog, "LOG_PATH", tmp_path / "runs.jsonl")
    policies = tmp_path / "policies.json"
    shutil.copy(HERE / "policies.json", policies)
    monkeypatch.setattr(policy, "POLICIES_PATH", policies)
    monkeypatch.delenv("DUNNING_QUEUE", raising=False)
    monkeypatch.delenv("DUNNING_CLOCK", raising=False)
    queue_module.reset()
    store.init_db(db)
    yield {"db": db, "policies": policies, "log": tmp_path / "runs.jsonl"}
    queue_module.reset()


_BALANCE = re.compile(r"balance \$([0-9][0-9,]*\.[0-9]{2})")


class FakeModel:
    """A scripted stand-in for llm.structured_completion.

    Drafts echo the balance it finds in the prompt (so the amount check
    passes) unless `wrong_amount` is set. Inbound replies are read with a few
    keywords, or from `intents`, a queue a test can fill with exact answers.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.wrong_amount = False
        self.fail_with: str | None = None
        self.intents: list[InboundIntent] = []
        self.usage = llm.Usage(prompt_tokens=120, completion_tokens=60, cost_usd=0.0001)

    def __call__(self, messages, response_model, **kwargs):
        self.calls.append(response_model.__name__)
        if self.fail_with:
            import errors
            raise errors.DeskhandError(self.fail_with, "scripted failure")
        text = "\n".join(m["content"] for m in messages)
        if response_model is Draft:
            match = _BALANCE.search(text)
            balance = match.group(1) if match else "0.00"
            amount = "$999.00" if self.wrong_amount else f"${balance}"
            return Draft(subject="About your invoice", body=f"Hello, your balance of {amount} is outstanding."), self.usage
        if response_model is InboundIntent:
            if self.intents:
                return self.intents.pop(0), self.usage
            return self._read(text), self.usage
        raise AssertionError(f"unexpected model: {response_model}")

    @staticmethod
    def _read(text: str) -> InboundIntent:
        reply = text.split("Reply:", 1)[-1].lower()
        if "not paying" in reply or "dispute" in reply or "never received" in reply:
            return InboundIntent(intent="dispute", promised_date=None, disputed_amount_usd=None,
                                 summary="disputes an item", confidence=0.92)
        if "already paid" in reply or "we paid" in reply:
            return InboundIntent(intent="payment_claim", promised_date=None, disputed_amount_usd=None,
                                 summary="says they paid", confidence=0.85)
        if "stop" in reply:
            return InboundIntent(intent="stop_contact", promised_date=None, disputed_amount_usd=None,
                                 summary="asks us to stop", confidence=0.9)
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", reply)
        if "pay" in reply and date_match:
            return InboundIntent(intent="promise_to_pay", promised_date=date_match.group(1),
                                 disputed_amount_usd=None, summary="promises to pay", confidence=0.88)
        return InboundIntent(intent="question", promised_date=None, disputed_amount_usd=None,
                             summary="asks something", confidence=0.7)


@pytest.fixture()
def fake_model(monkeypatch: pytest.MonkeyPatch) -> FakeModel:
    fake = FakeModel()
    monkeypatch.setattr(llm, "structured_completion", fake)
    return fake


@pytest.fixture()
def seeded(fake_model):
    """The four classroom cases, clock at D-2 09:00 UTC, worker not yet run."""
    import seed

    seed.seed(reset=False)
    return seed


def at(y: int, m: int, d: int, hh: int = 9, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)
