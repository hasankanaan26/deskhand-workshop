"""The ticker: the one process that is allowed to be boring.

    while True:
        for case_id in queue.due(clock.now()):   # whose time has come?
            engine.evaluate(case_id, "timer")     # ask the case what is true
        sleep(1)

That is the whole scheduler. It holds no case in memory, so killing it loses
nothing: the wake-ups are in the database (or in Redis), and the next worker
picks them up, late. It writes a heartbeat every tick so the console can say
"worker: last seen 3 s ago", which is how you watch the kill in class.

    py worker.py            run until Ctrl+C
    py worker.py --once     one tick, then exit (the console's and CLI's "Tick")
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import engine
import obslog
import queues as queue_module
import store

TICK_SECONDS = float(os.getenv("DUNNING_TICK_SECONDS") or 1.0)


def tick_once() -> int:
    store.touch_worker()
    results = engine.tick()
    for r in results:
        print(f"[worker] {r['case_id']}: {r['decision']}", flush=True)
    return len(results)


def run_forever(stop=None) -> None:
    q = queue_module.from_env()
    synced = q.resync()
    obslog.log_event("startup", "worker.started", queue=q.name, resynced=synced, tick_seconds=TICK_SECONDS)
    print(f"[worker] started: queue={q.name}, tick every {TICK_SECONDS:g}s, "
          f"{synced} wake-up(s) resynced. Ctrl+C stops it.", flush=True)
    try:
        while stop is None or not stop.is_set():
            tick_once()
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        obslog.log_event("startup", "worker.stopped")
        print("[worker] stopped", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the dunning engine's scheduler")
    ap.add_argument("--once", action="store_true", help="one tick, then exit")
    args = ap.parse_args(argv)
    store.init_db()
    if args.once:
        woke = tick_once()
        print(f"[worker] tick: {woke} case(s) woke")
        return 0
    run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
