"""The whiteboard version: the reminder cadence as steps.

    soft reminder -> wait -> first notice -> wait -> second notice -> wait -> final notice

Kept here on purpose. Session 6 builds this first, runs it, and breaks it,
before it builds the engine. One thread per open case, each walking the same
four steps and sleeping on the simulated clock in between. On the happy path
it works.

What it cannot do, and what the class watches it do instead:

- it sends inside quiet hours: no step asks the customer's local time
  (09:00 UTC is 02:00 in Los Angeles);
- it sends the business customer the retail cadence: one set of steps for
  every tenant;
- it reads the case once, when the thread starts, so a customer who pays in
  full on day 5 still gets a firm reminder on day 10, for the full amount;
- a change to the cadence is a code change and a restart, and a restart
  starts every thread from the top: everything already sent goes out again;
- no line says which rules were in force: the rules are whatever code ran.

    py steps.py         run until Ctrl+C; move the clock from the console
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import clock
import outbox
import store

UTC = timezone.utc


def wait_until(when: datetime) -> None:
    """Sleep on the simulated clock. The thread holds the case until then."""
    while clock.now() < when:
        time.sleep(0.5)


def send(case, stage: str, channel: str, tone: str) -> None:
    outbox.send_now(case, stage, channel, tone)
    print(f"[steps] {clock.now():%a %d %b %H:%M} UTC  {case.case_id} {case.customer_name}:"
          f" {stage} (${case.balance_usd:.2f})", flush=True)


def run(case_id: str) -> None:
    case = store.get_case(case_id)  # read once, when the workflow starts
    due = datetime.combine(case.invoice_due, datetime.min.time(), tzinfo=UTC)

    wait_until(due + timedelta(days=-2))
    send(case, "soft_reminder", "email", "friendly")
    wait_until(due + timedelta(days=3))
    send(case, "first_notice", "email", "formal")
    wait_until(due + timedelta(days=10))
    send(case, "second_notice", "sms+email", "firm")
    wait_until(due + timedelta(days=21))
    send(case, "final_notice", "email", "final")


def main() -> int:
    store.init_db()
    cases = [c for c in store.list_cases() if c.open]
    for c in cases:
        threading.Thread(target=run, args=(c.case_id,), daemon=True, name=c.case_id).start()
    print(f"[steps] {len(cases)} workflows started, one thread each. Ctrl+C stops them.", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[steps] stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
