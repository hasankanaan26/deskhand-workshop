# Reply triage: the model proposes, the harness disposes

Customers answer payment reminders. Some say they paid, some promise a date,
some dispute an item, some are auto-replies, and one tries to talk the system
into marking an invoice paid. This folder triages those replies as a workflow
with two model calls and three steps of plain code. The model reads the reply
and proposes an action. Code matches the reply to an invoice, holds the
proposal to a written policy, and decides what runs now and what waits for a
person. A person approves only what can't be taken back.

## The five steps

| Step | Who | File | In, out |
|---|---|---|---|
| 1. read | model | `read.py` | reply text -> `ReplyReading` (intent, other intents, churn flag, refs, amounts, dates, quotes) |
| 2. match | code | `match.py` | reading + account -> `Match` (which open invoices, by which rule, ambiguous or not) |
| 3. propose | model | `propose.py` | reading + matched invoices + allowed actions -> `Proposal` (one action, maybe a draft reply) |
| 4. check | code | `check.py` | -> a list of `CheckFailure`, each with a fixed code |
| 5. gate | code | `gate.py` | -> `Disposition`: what runs, when, and what happens to the reply |

`pipeline.py` runs them in that order, every time. Code never asks the model
what to do next. The two model steps are arguments to `triage()`, so the tests
pass fakes and never call a model. The types are in `models.py`: every model
output is fully typed with no `dict` fields, because strict structured output
rejects anything looser.

What the gate does:

- any check failure: escalate now, send nothing;
- `offer_payment_plan` and `update_contact` run only after a person's yes (a
  commitment to the customer; account details going to a new address);
- everything else runs now, is logged, and a person can undo it;
- every draft reply waits for a person. A sent email can't be recalled, and
  free text can promise things code can't see.

## The policy file

`triage_policy.json` holds the limits (a confidence floor of 0.6, promises up
to 30 days out, snoozes up to 21 days, payment plans of at most 3 instalments
within 60 days), the actions allowed for each intent, and the actions that
need a person. These numbers are this course's choices for the exercise, not
an industry standard. Change one and run the tests to see what moves.

## Run the tests

```
cd dunning\triage
py -m pytest tests -q
```

All offline. 105 tests: the match rules, every check code passing and failing,
each gate branch, the schemas, a pipeline with fake model steps, and the data
test, which runs every gold label in `data/replies.json` through match, check
and gate and compares the result with that row's `expected`.

## The data

`data/accounts.json`: eight business customers, their open and closed
invoices with line items, a few unmatched incoming payments, and one fixed
"now" for the whole set (`2026-11-16T09:00:00Z`).

`data/replies.json`: 30 replies, each with `hard_because` (one line on what
makes it tricky), a `gold_reading` and `gold_proposal` (what a careful person
would read and propose from what each step is given), and `expected`. Some gold
proposals break the policy on purpose, a promise 50 days out or a plan that
doesn't add up, and `expected` shows the escalation and the failure codes.

## One reply, step by step

```
py try_reply.py --list                 # the thirty, with why each is hard
py try_reply.py R-13                   # with its labels: no model, free
py try_reply.py R-13 --live            # the model, two calls
py try_reply.py R-05 --override '{"reading": {"intent": "already_paid"}}'
py try_reply.py --account A-01 --text "We paid last week, ref ACME-OCT-240." --live
```

It prints each step: the reading, the match, the proposal, the failed checks and the
gate's decision, then what the label expected. `--override` is a what-if: it edits the
labels before match, check and gate run, so you can see which check catches a mistake
without paying for a model call. The dunning console's Triage tab (`#triage`) is the same
script in the browser.

## Run the live set

```
py run_set.py                    # all 30, two model calls each
py run_set.py --only R-05,R-12
py run_set.py --limit 5
```

This calls the model set in the repo `.env` through `../llm.py`, so it costs a
little money. It prints one row per reply (gold intent against the model's,
expected action against the actual one, when it runs, the reply, the failure
codes), then plain counts, and writes everything to `runs/` (gitignored).

Results: [TODO: run before class]

## The homework

```
py dunning\triage\live\start.py --force     # from resolvo-workshop: makes triage-live\
```

`triage-live` is this folder with `match.py`, `check.py` and `gate.py` replaced
by stubs whose docstrings are the spec. Give your coding agent the three
prompts in `live/prompts.md`, one per stub; each ends with "run the tests".
Before you start, 34 tests pass; after all three, 105. If a step goes
sideways, `py ..\dunning\triage\live\restore.py match` (or `check`, `gate`,
`all`) copies the reference file in.

## Exercises

1. Label five replies of your own (real ones from your inbox, anonymised, are
   best) and add them to `data/replies.json`. Write `expected` by hand first,
   then run the tests to see if the harness agrees with you.
2. Run the live set. Pick two rows where the model and the gold label
   disagree, and decide for each whether the label or the model is wrong.
3. Add a tenth intent of your choice (`asks_for_invoice_copy`, say). List
   every file that had to change.
4. Argue in three sentences whether `update_contact` really needs a person.
