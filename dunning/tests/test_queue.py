"""Two backends, one rule: a wake-up is a question."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import clock
import engine
import queues
import store

UTC = timezone.utc


class StubRedis:
    """The four calls RedisQueue makes, over a dict."""

    def __init__(self) -> None:
        self.sets: dict[str, dict[str, float]] = {}

    def zadd(self, key, mapping):
        self.sets.setdefault(key, {}).update(mapping)

    def zrem(self, key, *ids):
        s = self.sets.get(key, {})
        for i in ids:
            s.pop(i, None)

    def eval(self, script, numkeys, key, now, limit):
        s = self.sets.get(key, {})
        due = sorted((i for i, score in s.items() if score <= float(now)), key=lambda i: s[i])[: int(limit)]
        for i in due:
            s.pop(i)
        return due


def test_sqlite_queue_is_the_case_row(seeded):
    q = queues.SqliteQueue()
    now = clock.now()
    assert sorted(q.due(now)) == ["C-1001", "C-1002", "C-1003", "C-1004"]
    assert q.due(now) == []


def test_redis_queue_pops_what_is_due(seeded):
    stub = StubRedis()
    q = queues.RedisQueue(stub)
    now = clock.now()
    q.schedule("C-1", now - timedelta(hours=1))
    q.schedule("C-2", now + timedelta(days=1))
    assert q.due(now) == ["C-1"]
    assert q.due(now) == []
    q.cancel("C-2")
    assert stub.sets[queues.DUE_KEY] == {}


def test_redis_resync_reloads_every_wake_up(seeded):
    stub = StubRedis()
    q = queues.RedisQueue(stub)
    assert q.resync() == 4
    assert set(stub.sets[queues.DUE_KEY]) == {"C-1001", "C-1002", "C-1003", "C-1004"}


def test_a_stale_wake_up_is_harmless(seeded, fake_model, monkeypatch):
    """The customer paid in full before the reminder came due: the wake-up
    still arrives, evaluate re-reads the truth, nothing is sent."""
    stub = StubRedis()
    monkeypatch.setattr(queues, "_instance", queues.RedisQueue(stub))
    engine.evaluate("C-1001", "manual")  # schedules the first notice in Redis
    assert "C-1001" in stub.sets[queues.DUE_KEY]
    engine.record_payment("C-1001", 240.0)  # settled: the entry is removed
    assert "C-1001" not in stub.sets[queues.DUE_KEY]
    stub.zadd(queues.DUE_KEY, {"C-1001": clock.now().timestamp()})  # a stale duplicate arrives anyway
    clock.advance(days=5)
    woke = engine.tick()
    assert [r["case_id"] for r in woke] == ["C-1001"]
    assert woke[0]["decision"].startswith("nothing: case is settled")
    assert len(store.messages_for("C-1001")) == 1


def test_from_env_builds_the_right_backend(monkeypatch):
    queues.reset()
    monkeypatch.delenv("DUNNING_QUEUE", raising=False)
    assert queues.from_env().name == "sqlite"
    queues.reset()
    monkeypatch.setenv("DUNNING_QUEUE", "carrier-pigeon")
    with pytest.raises(RuntimeError):
        queues.from_env()
    queues.reset()
