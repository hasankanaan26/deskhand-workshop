# Session 6, try this tonight: build the triage harness, measure it, write §1

In class you watched the collections engine built live, with a first cut of the reply
step: one model call reads the reply, then code applies a fixed action. Production triage
needs more. A customer's reply goes through five steps: read (the model), match (code),
propose (the model), check (code) and gate (code). The model proposes; the harness
disposes. Tonight you build the three code steps, measure the model against a labelled set, and write §1 of your
design doc. About an hour. The live run calls the
model, so it costs a little; it prints what it spent.

The kit is `dunning/triage/`. Its `README.md` explains every file.

## 1. Build match, check and gate with your coding agent (30 min)

From the repo root, venv active:

```
py dunning\triage\live\start.py --force    # triage-live\: the kit with three stubs
cd triage-live
py -m pytest tests -q                            # 34 passed, 71 failed
```

`match.py`, `check.py` and `gate.py` are stubs. Each one's docstring is its spec, and the
tests are the acceptance check. The two model steps (`read.py`, `propose.py`) are finished:
read their prompts first. Open your coding agent in `triage-live` and give it the three
prompts in `dunning\triage\live\prompts.md`, word for word, clearing the session before
each one. Run the tests after every prompt. The count climbs to 53 after match, 67 after check and 105 after gate.

Then read each file your agent wrote against its stub's docstring. What did it decide that
the spec didn't say? Places to look: how it normalises "inv 2041", what it does when an
amount matches two invoices, and whether a plan's sum is compared with a tolerance.

If a prompt goes sideways, copy the reference file in and carry on:
`py ..\dunning\triage\live\restore.py match` (or `check`, `gate`, `all`). No coding agent?
`restore.py all`, then read each file next to its stub.

## 2. Your own replies, and the live set (20 min)

Open `data/replies.json` and read three of the hard ones: each has a `hard_because` line.
Then write five replies of your own, in the same shape, for customers in
`data/accounts.json`: a reply with two requests, a promise with a vague date, a reply that
names an invoice the customer doesn't have, an angry one, and one you think will fool the
model. Fill in `gold_reading`, `gold_proposal` and `expected` the way a careful person
would. Run `py -m pytest tests -q`: the data test tells you if your `expected` doesn't match what
the harness does with your labels. Either your label is wrong or the harness disagrees with
you; decide which.

To see one reply step by step: `py try_reply.py R-13` (with its labels, free),
`py try_reply.py R-13 --live` (the model), or a what-if:
`py try_reply.py R-05 --override '{"reading": {"intent": "already_paid"}}'`. Which check
catches it? The dunning console's Triage tab does the same in the browser
(`py -m uvicorn app:app --port 8006` in `dunning\`, then
http://127.0.0.1:8006/console/#triage).

Then the real model, over the whole set:

```
py run_set.py                              # two model calls per reply
py run_set.py --only R-31,R-32             # just yours
```

It prints one row per reply and plain counts: how often the model's intent agreed with the
label, how often the final outcome did, and how many were escalated. Pick two
disagreements. For each, decide in one sentence whether the label or the model is wrong,
and what you'd change: the prompt, the label, or a check.

You're done when your five replies pass the data test, you have the counts from one live
run, and you can explain two disagreements.

## 3. Design doc §1: who decides each step (10 min)

§1's steps table already has a "Who" column (code, model, a person). Fill it in, with
one line on why for each step. Then
mark every step that can't be taken back: those are where a person approves. In the
"Check" column, name the check code runs on the model's output for each LLM step. If
your system has a schedule, add one sentence each: where it lives (code, a workflow tool, a
record per client), and where the rules that apply to every message live. If it receives
messages from outside: what does the listener do before it answers, what is the queue's
dedupe key, where does a job go after it has failed a few times, and which messages reach
your workflow at all (what goes to code, to a person, to another team)? For every step whose
"Waits for" column isn't "nothing": what holds the wake-up (a date on a row, a scheduled
message, a durable engine), what runs the loop (a cron job, a background worker), and what
the step re-reads when it wakes.

## Stretch (15 min)

- Add a tenth intent of your choice (a request for a copy of the invoice, say) and list
  every file that had to change. Was any of them a model file?
- `update_contact` waits for a person. Argue in three sentences whether it should. What
  would have to be true for it to run on its own?
- The amount check's gap (reference slide 53): the lab's amount check only checks amounts. Write a check for wording a
  stage doesn't allow (a discount, a waiver, legal referral before the final notice) in `dunning-live\drafting.py` (make the copy with
  `py dunning\live\start.py --force`), and list one reminder it would wrongly block.

## Going further

**Structured output and checks.** The reading and the proposal are Session 2's structured
output on an email. The MCP links are in Session 5's handout.

**The outbound core, in the lab.** `dunning/README.md` explains the engine behind the
reminders: the versioned policy per client, the rules function, the worker and the outbox.
To rebuild it yourself, `dunning/live/prompts.md` has the three prompts from class and
`py dunning\live\start.py --force` makes the folder.

**Collections in the wild.** [Stripe's revenue recovery](https://docs.stripe.com/billing/revenue-recovery)
is a dunning engine as a product: the cadence is settings, not code. Chargebee's dunning is
configured too:
[smart and manual dunning](https://www.chargebee.com/docs/payments/2.0/kb/payments/smart-and-manual-dunning-management).

**Where a long wait lives.** If you already run a durable engine, Temporal's
[Managing very long-running Workflows](https://temporal.io/blog/very-long-running-workflows)
and AWS Step Functions'
[UpdateStateMachine](https://docs.aws.amazon.com/cli/latest/reference/stepfunctions/update-state-machine.html)
(running executions keep the previous definition) are the two pages to read before putting
a weeks-long schedule inside one.

**Explore: the worker as scheduled messages, not a loop.** `dunning/worker.py` polls: every
second it asks the database which cases are due, and a cron job would do the same every
few minutes. Sketch the other design (reference slide 47's third column): whenever the
engine writes `next_due_at`, it also books one message on a queue, delivered at that time,
saying only "look at this case". Nothing polls. Answer three questions before you'd trust
it. When Acme pays or the schedule changes, do you cancel and rebook the message, or let
the stale one fire and re-read the case? How does an eleven-day wait fit a service with a
delay limit? What do you watch, now that there is no heartbeat? The docs:
[Azure Service Bus scheduled messages](https://learn.microsoft.com/en-us/azure/service-bus-messaging/message-sequencing)
(cancel by sequence number),
[EventBridge Scheduler's one-time schedules](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html)
(`at(...)`),
[Google Cloud Tasks](https://docs.cloud.google.com/tasks/docs/create-tasks) (`schedule_time`, at
most [30 days ahead](https://docs.cloud.google.com/tasks/docs/quotas)), and for contrast
[SQS delay queues](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-delay-queues.html),
which stop at 15 minutes. In code you already run:
[BullMQ's delayed jobs](https://docs.bullmq.io/guide/jobs/delayed) and
[Sidekiq's scheduled jobs](https://github.com/sidekiq/sidekiq/wiki/Scheduled-Jobs).

## Before next session

Bring §1; two people will read theirs out. Next session is Session 7.
