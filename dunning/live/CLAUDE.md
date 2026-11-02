# The dunning engine, built live

This folder is a live build in front of a class. A collections engine: a case
per unpaid invoice, a reminder policy per tenant in `policies.json`, a
simulated clock, a worker that wakes due cases, and an inbound path for
customer replies.

- Work only inside this folder. Read and write nothing outside it.
- Each file still to build is a stub whose module docstring is the spec.
  Follow the docstring; the named test file is the acceptance test.
- Never change a test, `models.py`, `store.py`, `clock.py` or the doors in
  `engine.py`. If a test looks wrong, say so and stop.
- Run the tests with `py -m pytest tests -q` (or one file) before you say done.
  They are offline: a fake model, a temporary database.
- Keep the code short and plain. No new dependencies, no new files beyond the
  one the prompt asks for.
- Windows, PowerShell. Python is `py`.
