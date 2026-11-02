"""How a case waits: a wake-up on the books, and a worker that asks.

Two backends behind one small protocol:

- SqliteQueue (the default): the wake-up time is the case row's own
  next_due_at column. The worker's query claims what is due (store.py). No
  extra moving part, which is why it is the default and what every student
  runs at home.
- RedisQueue: the same wake-ups as a sorted set, score = due time, which is
  the delayed-job pattern behind Sidekiq, BullMQ and friends. The worker
  pops what is due with one atomic script. Azure Service Bus's scheduled
  messages and SQS's delay queues are the managed versions of the same idea.
  `pip install redis`, a Redis in Docker, DUNNING_QUEUE=redis.

The rule both keep: a wake-up is a question, not an order. The worker only
ever calls engine.evaluate, which re-reads the case, so a stale wake-up
(the invoice was paid in the meantime) is harmless.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import store

DUE_KEY = "dunning:due"


class ScheduledQueue(Protocol):
    name: str

    def schedule(self, case_id: str, due_at: datetime) -> None: ...
    def cancel(self, case_id: str) -> None: ...
    def due(self, now: datetime, db_path: Path | None = None) -> list[str]: ...
    def resync(self, db_path: Path | None = None) -> int: ...


class SqliteQueue:
    """The case row is the queue. next_due_at is written by the engine's
    wait node, so schedule() and cancel() have nothing left to do."""

    name = "sqlite"

    def schedule(self, case_id: str, due_at: datetime) -> None:
        return None

    def cancel(self, case_id: str) -> None:
        return None

    def due(self, now: datetime, db_path: Path | None = None) -> list[str]:
        return store.claim_due_cases(now, db_path)

    def resync(self, db_path: Path | None = None) -> int:
        return 0


# Pops every member whose score (due time) is at or before now, atomically,
# so two workers never wake the same case.
_POP_DUE = """
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, ARGV[2])
for _, id in ipairs(ids) do redis.call('ZREM', KEYS[1], id) end
return ids
"""


class RedisQueue:
    name = "redis"

    def __init__(self, client, key: str = DUE_KEY) -> None:
        self.client = client  # redis.Redis, or a stub with zadd, zrem, eval
        self.key = key

    def schedule(self, case_id: str, due_at: datetime) -> None:
        self.client.zadd(self.key, {case_id: due_at.astimezone(timezone.utc).timestamp()})

    def cancel(self, case_id: str) -> None:
        self.client.zrem(self.key, case_id)

    def due(self, now: datetime, db_path: Path | None = None) -> list[str]:
        ids = self.client.eval(_POP_DUE, 1, self.key, now.astimezone(timezone.utc).timestamp(), 20)
        return [i.decode() if isinstance(i, bytes) else str(i) for i in ids]

    def resync(self, db_path: Path | None = None) -> int:
        """Put every wake-up the case table knows about into the set. Run at
        worker start, so switching backends mid-way loses nothing."""
        count = 0
        for case_id, due_at in store.scheduled_cases(db_path):
            self.schedule(case_id, due_at)
            count += 1
        return count


_instance: ScheduledQueue | None = None


def from_env() -> ScheduledQueue:
    """DUNNING_QUEUE=sqlite (default) or redis; built once per process."""
    global _instance
    if _instance is None:
        _instance = _build()
    return _instance


def _build() -> ScheduledQueue:
    choice = (os.getenv("DUNNING_QUEUE") or "sqlite").strip().lower()
    if choice == "sqlite":
        return SqliteQueue()
    if choice == "redis":
        import redis  # optional dependency: pip install redis

        url = os.getenv("DUNNING_REDIS_URL") or "redis://localhost:6379/0"
        return RedisQueue(redis.Redis.from_url(url, decode_responses=True))
    raise RuntimeError(f"DUNNING_QUEUE={choice!r}: use 'sqlite' or 'redis'")


def reset() -> None:
    """Forget the built instance (tests switch backends)."""
    global _instance
    _instance = None
