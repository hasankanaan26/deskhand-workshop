"""Shared offline test fixtures. No live LLM calls, no live HTTP."""
import sys
from pathlib import Path

import pytest

# Make the checkpoint's modules importable when pytest runs from anywhere.
# (01 has no root conftest.py, so it is done here, before the import below.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import obslog  # noqa: E402

# Each test file replaces the provider call itself (monkeypatch), so no test
# needs a key or spends money. This file only redirects the log.


# autouse: every test gets this without asking for it, so none can forget.
@pytest.fixture(autouse=True)
def isolate_logs(monkeypatch, tmp_path):
    """Keep test runs from appending to the real logs/runs.jsonl."""
    monkeypatch.setattr(obslog, "LOG_PATH", tmp_path / "runs.jsonl")
