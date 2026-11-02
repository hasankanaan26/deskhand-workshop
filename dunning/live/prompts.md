# The live build: three prompts

Paste each prompt into terminal B, the `claude` session. Type `/clear` before each one.
When the agent says it's done, run the tests in terminal C:

```
py -m pytest tests -q
```

Red tests? Don't debug. Copy the finished file in and carry on:

```
py ..\dunning\live\restore.py rules      # or: engine, worker, inbound
```

Setup, once: `py dunning\live\start.py --force`, then `cd dunning-live`. Before the
build, the tests say 17 passed, 33 failed. Run `claude` in accept-edits mode (Shift+Tab),
and allow `py -m pytest` the first time it asks.

## Build 1: the rules (slide 10)

```
Implement rules.py. Its docstring is the spec, models.py has the types and
tests/test_rules.py is the acceptance test. Don't change the tests. Run that
file before you say done.
```

Expect 31 passed.

## Build 2: the engine (slide 12)

```
Implement the pass in engine.py: evaluate() and whatever it needs. The module
docstring is the spec; models.py, store.py, rules.py and drafting.py have what it
calls. tests/test_histories.py, test_idempotency.py and test_policy.py are the
acceptance tests. The whole-class history also needs inbound.py, which comes later,
so that one test may still fail. Don't touch the tests or the doors. Run those
three files before you say done.
```

Expect 41 passed. The rest wait for the worker and build 3.

## The worker: no prompt, restore it (slide 16)

```
py ..\dunning\live\restore.py worker
```

Expect 42 passed. To build it yourself instead, the prompt is in `prompts-extra.md`.

## Build 3: a reply (slide 17)

```
Implement inbound.py from its docstring, classifier prompt included;
llm.structured_completion makes the call. tests/test_inbound.py is the
acceptance test. Then run the whole suite: all 50 should pass.
```

Expect 50 passed.
