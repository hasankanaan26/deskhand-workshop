"""The ticker: the one process that is allowed to be boring.

RESTORED IN CLASS, NOT BUILT (the prompt is in live/prompts-extra.md). This docstring is the spec;
tests/test_worker.py is the acceptance test.

    while True:
        store.touch_worker()      # the heartbeat: the console shows "worker: alive"
        engine.tick()             # wake every case whose time has come
        sleep(TICK_SECONDS)

engine.tick() is already written: it asks the queue which cases are due and
calls engine.evaluate(case_id, "timer") for each one. A wake-up is a
question, not an order: the worker never sends anything itself. It holds no
case in memory, so killing it loses nothing; the next worker finds the due
rows, late.

What to write here:
- TICK_SECONDS: the DUNNING_TICK_SECONDS environment variable, default 1.
- tick_once() -> int: store.touch_worker(), then engine.tick(); print one
  line per case it woke, "[worker] C-1002: <its decision>", and return how
  many woke.
- run_forever(stop=None): q = queues.from_env(), q.resync() (Redis reloads
  every wake-up from the case table; SQLite does nothing), an
  obslog "worker.started" line, a printed start line, then tick_once() and
  time.sleep(TICK_SECONDS) until Ctrl+C or until `stop` (a threading.Event)
  is set. Print "[worker] stopped" on the way out.
- main(argv=None): store.init_db(); --once runs one tick, prints how many
  woke and exits; otherwise run_forever().

    py worker.py            run until Ctrl+C
    py worker.py --once     one tick, then exit
"""
from __future__ import annotations

import sys

_TODO = "worker.py is restored in class: restore.py worker"


def tick_once() -> int:
    raise NotImplementedError(_TODO)


def run_forever(stop=None) -> None:
    raise NotImplementedError(_TODO)


def main(argv: list[str] | None = None) -> int:
    print(_TODO)
    return 1


if __name__ == "__main__":
    sys.exit(main())
