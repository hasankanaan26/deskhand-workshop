"""The worker holds nothing: a due case waits in the database until a worker
asks, and two workers never take the same one."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import clock
import engine
import store
import worker

UTC = timezone.utc


def test_a_due_case_waits_for_a_worker(seeded, fake_model):
    clock.advance(days=5)  # plenty due, nobody asking
    assert all(c.stage_history == {} for c in store.list_cases())
    woke = [r["case_id"] for r in engine.tick()]  # "the worker starts"
    assert len(woke) == 4
    assert store.get_case("C-1001").stage_history["first_notice"] == "sent"


def test_two_workers_claim_disjoint_cases(seeded):
    now = clock.now()
    results: list[list[str]] = [[], []]

    def claim(i: int) -> None:
        results[i] = store.claim_due_cases(now)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results[0] + results[1]) == ["C-1001", "C-1002", "C-1003", "C-1004"]
    assert not set(results[0]) & set(results[1])


def test_a_claim_expires_after_the_lease(seeded):
    now = clock.now()
    assert len(store.claim_due_cases(now)) == 4
    assert store.claim_due_cases(now) == []  # claimed, not yet evaluated
    case = store.get_case("C-1001")
    case.claimed_at = store.now_wall() - timedelta(seconds=store.LEASE_SECONDS + 5)
    store.save_case(case)
    assert store.claim_due_cases(now) == ["C-1001"]  # the dead worker's claim is up for grabs


def test_tick_once_writes_the_heartbeat(seeded, fake_model):
    assert store.worker_seen_at() is None
    woke = worker.tick_once()
    assert woke == 4
    assert store.worker_seen_at() is not None
