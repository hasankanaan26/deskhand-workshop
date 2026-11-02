# Reply triage, the homework copy

This folder triages customer replies to payment reminders. Two steps are
model calls (`read.py`, `propose.py`); three are plain code that you build:
`match.py`, `check.py`, `gate.py`. `pipeline.py` runs them in a fixed order.

- Work only inside this folder.
- Each file to build is a stub whose module docstring is the spec. Follow
  the docstring; the test file it names is the acceptance test.
- Never change a test, `models.py`, `policy.py`, `triage_policy.json`,
  `pipeline.py` or the data files. If a test looks wrong, say so and stop.
- Run the tests with `py -m pytest tests -q` (or one file) before you say
  done. They are offline: no model is ever called.
- Never run `run_set.py`: it calls a real model and costs money.
- Keep the code short and plain. No new dependencies, no new files.
- Windows, PowerShell. Python is `py`.
