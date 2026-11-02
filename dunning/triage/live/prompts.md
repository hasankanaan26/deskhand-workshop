# Reply triage homework, prompt by prompt

You build the three code steps of the triage workflow with your own coding
agent, one prompt per step. The tests are the acceptance check after each one.
The model steps (`read.py`, `propose.py`) and the pipeline are given: tonight
is about the part that decides.

Setup, once, from `resolvo-workshop`:

```
py dunning\triage\live\start.py --force     # makes triage-live\ with three stubs
cd triage-live
py -m pytest tests -q                       # 34 passed, 71 failed before you start
```

Start your agent inside `triage-live`. Clear its context before every prompt:
if it needs the previous conversation to do the next step, the spec is missing
something, and that is worth noticing.

If a step isn't green after a reasonable try, restore the reference file, run
the tests, and move on to the next prompt:

```
py ..\dunning\triage\live\restore.py <match|check|gate|all>
```

## Prompt 1: match

```
Implement match.py in this folder. Its module docstring is the spec and
models.py has the types. tests/test_match.py is the acceptance test. Don't
change the tests, models.py or the data files. Keep it plain Python, no new
dependencies. Run the tests.
```

`tests/test_match.py` goes green; the suite goes from 34 to 53 passed.

## Prompt 2: check

```
Implement check.py in this folder. Its module docstring is the spec: eleven
failure codes, in that order, every limit read from the policy object, never
hard-coded. models.py and policy.py have the types. tests/test_check.py is
the acceptance test. Don't change the tests, the policy file or models.py.
A date that doesn't parse is a failure, never an exception. Run the tests.
```

`tests/test_check.py` goes green; the suite goes to 67.

## Prompt 3: gate

```
Implement gate.py in this folder. Its module docstring is the spec;
models.py and policy.py have the types. tests/test_gate.py is the acceptance
test, and once it passes, tests/test_data.py and tests/test_pipeline.py run
the whole workflow on all 30 labelled replies. Don't change any test. Run
the tests.
```

All 105 pass. Now read one row of `data/replies.json` whose `expected` is an
escalation (R-07, R-10 or R-21) and find the line in your `check.py` that
caused it.
