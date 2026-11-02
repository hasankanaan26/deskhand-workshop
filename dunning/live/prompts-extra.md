# Extra prompts, not used in class

The class uses only the three prompts in `prompts.md`. These are for home: the worker,
which class restores instead of building, and `steps.py`, a toy for comparison.

## The worker

Instead of `restore.py worker`, after build 2:

```
Write worker.py from its docstring. engine.tick() and store.touch_worker() are
all it needs. tests/test_worker.py is the acceptance test. Run it before you
say done.
```

The suite goes to 42. Start it, `py worker.py` in terminal D: Globex gets the
SMS (its soft reminder is skipped as stale), Initech is deferred (01:00 in Los
Angeles), Umbrella waits for its own day +5. Then +4 days (D+3: two first
notices, Initech deferred until 16:00 UTC), +7 hours (Initech's goes out),
Ctrl+C the worker, +2 days, start it again: Umbrella's first notice, once, late.

## The toy: the cadence as steps in code

Two prompts that write the schedule as steps in code, the way a whiteboard draws it, so
you can compare it with the engine. Run them in a fresh `dunning-live\` before build 1.
`py ..\dunning\live\restore.py steps` copies the finished toy in.

### Toy 1: the cadence as steps

```
Write steps.py: the reminder cadence as steps, the way the
whiteboard draws it. For every open case in store.list_cases(),
start a thread that runs four steps in order, sleeping on the
simulated clock (clock.now(), checked twice a second) until
each step's day, counted from the invoice due date:
  soft_reminder   day -2   email      friendly
  first_notice    day +3   email      formal
  second_notice   day +10  sms+email  firm
  final_notice    day +21  email      final
Send each step with outbox.send_now(case, stage, channel, tone)
and print a line. Only what the whiteboard says: four steps and
three waits. `py steps.py` runs it until Ctrl+C.
```

Run it with `py steps.py` in terminal D. At D-2 09:00 UTC four soft reminders
go out at once: Initech's at 01:00 in Los Angeles, and Umbrella's although the
business policy has no soft reminder. Then, from the console: +5 days (D+3,
four first notices), +2 days (D+5), Acme pays $240 in full, +5 days (D+10):
Acme gets a firm second notice anyway, for $240.00 if the thread read the
case once (the usual result), for $0.00 if it re-reads it.

### Toy 2: a change request

```
Retail customers (tenant nimbus-retail) want one more touch: an SMS heads-up
the day before the due date (sms_heads_up, day -1, sms, friendly). Business
customers don't. Add it to steps.py.
```

The diff is a new step and an `if` on the tenant. To ship it, restart
steps.py (Ctrl+C, `py steps.py`): every thread starts again from the top, and
everything already sent goes out again in one burst (fifteen messages),
including the new "due tomorrow" SMS on day 10. Then stop steps.py and reset
for the engine: `py seed.py --reset` (the API can stay up).
