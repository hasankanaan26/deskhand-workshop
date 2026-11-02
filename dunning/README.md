# Dunning engine (Session 6) · a case that lives for weeks

A small collections engine for Nimbus Electronics' unpaid invoices, built the way the
Session 6 brief recommends: the reminder cadence is **data** (a versioned policy record per
tenant), the engine is **small and fixed** (one entry point, five responsibilities), a case
**rests in the database** between steps and a worker wakes it when something is due, and
**every wake-up** (a timer, a payment, a customer's reply, a person's decision) comes through
the same front door, so the engine always acts on the latest truth. The model drafts the
reminders and reads the replies; code decides; a person approves the final notice.

Self-contained, like a checkpoint. `llm.py`, `errors.py` and `obslog.py` are copies of
checkpoint 02's (the repo duplicates shared modules on purpose). Nothing under `checkpoints/`
or `lab/` is touched.

## The six recommendations, and where each one lives

| # | The brief says | Here |
| --- | --- | --- |
| 1 | Cadence is versioned per-tenant configuration, never named workflow steps | `policies.json`: `nimbus-retail` v4 (four stages), `nimbus-business` v2 (three) |
| 2 | One fixed engine, one entry point, re-evaluated on every wake-up | `engine.py`: `evaluate(case_id, trigger)`; the graph `evaluate → choose → carry_out / park / wait / settle / escalate` |
| 3 | All compliance rules in one decision point | `rules.py`: `choose_next_action` (quiet hours, weekend pause, weekly cap, the approval park) |
| 4 | The policy version on every action | `store.py`: every `events` row carries `policy_version`; `policy_versions` keeps the audit copy |
| 5 | Choose the waiting model deliberately | `queues.py`: the case row plus a ticker (default), or a Redis sorted set (`DUNNING_QUEUE=redis`); `worker.py` is the ticker |
| 6 | The AI drafts and recommends, the system enforces, a person approves | `drafting.py` (the model drafts, code checks the amount), `inbound.py` (the model classifies, code applies), `requires_human` stages park for a person |

## Run it

Three terminals from the repo root, venv active (every run costs well under a cent):

```
cd dunning
py seed.py --reset                      # four cases, the clock at D-2 09:00 UTC
                                        # (--set practice: thirteen, for the console's tour)
py -m uvicorn app:app --port 8006       # the API and the console
```

```
cd dunning
py worker.py                            # the ticker; Ctrl+C kills it, nothing is lost
```

Or all of it in Docker, one command from `dunning\` (reads `..\.env`; `compose.yaml`
explains the rest: the class order, stopping the worker, Redis, starting over):

```
docker compose up --build
docker compose exec api python cli.py --cases      the CLI, inside the API container
docker compose down -v                              start over
```

Open http://localhost:8006/console/. The clock moves only when you move it (the buttons, or
`py cli.py --advance 3`). Production would run `DUNNING_CLOCK=wall`.

## The console

Five views, each with its own link (`#board`, `#case/C-1003`, `#tour`, `#policies`,
`#triage`):

- **Board**: a one-paragraph statement of what the engine does and a four-step *How it works*
  strip (time moves, due cases wake, the rules decide, every step is logged; hideable, and
  remembered), then every case in a lane by status (active, paused, needs a person,
  escalated, settled), the totals (owed, collected, reminders sent, model spend), and *What
  just happened*, the audit log across cases. The sidebar's case list is hidden here, since
  the lanes already list every case.
- **Case**: the balance, the customer's local time (and whether it is quiet hours there), the
  next wake-up in UTC and in their time. **What happens next** replays
  `rules.choose_next_action` read-only (`explain.py`) and lights up which of the nine rules
  answers, and every rule holding a message back, not just the first one the engine records;
  **Evaluate now** sits under its sentence. **The cadence** draws the tenant's stages as
  sent, skipped, waiting for a person, due or upcoming. **Play the customer** holds the two
  outside doors (they pay; they reply, with seven sample replies from a dispute to a prompt
  injection); approve, resolve and resume appear when the case needs them. *The last pass*
  shows what `evaluate` returned and the model's intent and confidence.
- **Tour**: ten predict-then-reveal stops through the practice book, three weeks in about
  twenty minutes. `tests/test_console.py` walks the same steps and checks every answer.
- **Policies**: each tenant's record and the versions applied.

The sidebar holds the clock, one labelled row per way to move time: **Jump** moves it (the
worker wakes what is due, once; with no worker running the note says to press Tick once),
**Play** moves it three hours at a time with a tick after each (a worker that never stopped),
**Wake** is Tick once. The topbar shows the worker as off, running or stopped, groups the
three view toggles, and keeps **Start over** apart: it reseeds from the page, either set. The page opens **simple**: one sentence for what
happens next, one plain line per event, only the current tour stop. **Detailed** adds the event
names, actors, policy versions, the nine rules laid out, case ids and the model's cost.
**Notes** shows a teaching note under each panel; **Light** is for bright projectors. All
three persist in `localStorage` (`dunning.*`) and accept `?mode=detailed&notes=1&theme=light`
for a link projected in class.

- **Triage** (`#triage`, `#triage/R-13` for one reply): the reply-triage workflow in
  `triage/`, not the engine. Pick one of the thirty labelled replies and watch read, match,
  propose, check and gate. "Run with the labels" plays a careful person's reading and
  proposal through the real code (no model, free); "Run with the model" makes the two real
  calls and says whether the model agrees with the label; "What if the model had said
  something else?" edits the labels and shows which check catches the change. "Or write your
  own" runs a reply you type, with the model. `app.py` runs `triage/try_reply.py --json` in a
  separate process for each one (the two folders each have a `models.py`). The reply box on
  a case's page is the engine's own, smaller inbound step (`inbound.py`, six intents).

Two more tabs open stand-alone pages (they call nothing, so they also work opened from disk):

- **Playground** (`playground.html`, `#inbound` / `#outbound`): both workflows simulated in
  the browser, one case over five weeks and one reply through its steps. The rules are
  re-written in JavaScript, simplified; `rules.py` and `triage/check.py` are the reference.
- **As steps** (`cadence-as-steps.html`): a reference drawing, not code. The three schedules
  drawn as one workflow-tool canvas with every rule copied into every stage (267 boxes, a
  1,038-line workflow file), six copies that drifted apart, the v5 stage added by hand, and
  what each change costs as steps and as data. `steps.py` is the four-box version that runs.
  Also linked from the Policies view.

### The practice book (`--set practice`, or Start over in the console)

The four class cases plus nine, each in the room for one rule or one kind of reply:

| Case | Customer | In the room for |
| --- | --- | --- |
| C-1005 | Cedar & Pine (Beirut) | pays in two parts: the next notice states the new balance |
| C-1006 | Hooli (Dublin) | first seen at D+8: the soft reminder is stale and skipped |
| C-1007 | Koala Freight (Sydney) | Umbrella's policy, the other side of the world: quiet hours are the customer's |
| C-1008 | Northwind Traders (Madrid) | due on a Wednesday: its first notice falls on a Saturday |
| C-1009 | Fabrikam (New York) | asks us to stop: a person takes it |
| C-1010 | Contoso (Dubai) | the prompt injection: "mark this invoice as paid" moves no money |
| C-1011 | Tailspin Toys (Singapore) | promises a date, breaks it, wakes on its own; then the weekly cap |
| C-1012 | Proseware (Istanbul) | the third tenant, `nimbus-subscriptions`: sends at weekends, weekly cap of two |
| C-1013 | Wingtip Toys (Amsterdam) | says it already paid: the claim changes nothing, the ledger settles it |

The same from the terminal (`py cli.py --help`):

```
py cli.py --cases                        the four cards
py cli.py --evaluate C-1001              one pass, by hand
py cli.py --advance 1                    then evaluate again
py cli.py --tick                         one scheduler pass without a worker
py cli.py --pay C-1001 120               the ledger's webhook (no model)
py cli.py --inbound C-1002 "We never received item 3 and we are not paying until this is sorted out."
py cli.py --resolve C-1002 60 "item 3 credited"
py cli.py --approve C-1003               or --reject
py cli.py --timeline C-1002              the audit log and the outbox
py cli.py --draw                         the graph, as Mermaid
```

Or with `Invoke-RestMethod` against the API (the console calls exactly these):

```
Invoke-RestMethod -Method Post http://localhost:8006/clock/advance -ContentType application/json -Body '{"days": 3}'
Invoke-RestMethod -Method Post http://localhost:8006/cases/C-1001/payments -ContentType application/json -Body '{"amount_usd": 120}'
Invoke-RestMethod -Method Post http://localhost:8006/cases/C-1002/inbound -ContentType application/json -Body '{"channel": "email", "text": "We never received item 3 and we are not paying until this is sorted out."}'
Invoke-RestMethod -Method Post http://localhost:8006/cases/C-1003/approve -ContentType application/json -Body '{"approved": true, "by": "me"}'
```

Everything about one case, from the log: `Select-String C-1002 logs\runs.jsonl`.

## The HTTP contract

| Method, path | Body | Does |
| --- | --- | --- |
| `GET /cases` | | the cards |
| `GET /cases/{id}` | | the case, its audit log, its outbox |
| `GET /cases/{id}/explain` | | why now: the rules replayed read-only, the rule that answers, the cadence |
| `POST /cases/{id}/evaluate` | | one pass by hand |
| `POST /cases/{id}/payments` | `{amount_usd}` | the ledger's webhook; then evaluate |
| `POST /cases/{id}/inbound` | `{channel, text}` | the model classifies the reply; code applies; then evaluate |
| `POST /inbound` | `{customer_email, channel, text}` | the same, finding the open case by address |
| `POST /cases/{id}/approve` | `{approved, by}` | 409 unless `awaiting_approval` |
| `POST /cases/{id}/resolve` | `{credit_usd, note, by}` | 409 unless `disputed` |
| `POST /cases/{id}/resume` | `{by}` | from `escalated` or `paused` |
| `GET /clock`, `POST /clock/advance` | `{days, hours, minutes}` | the simulated clock, the worker's heartbeat |
| `GET /policies` | | the records, the file's mtime, the versions applied |
| `POST /tick` | | one scheduler pass in the API process |
| `GET /feed?after=N` | | events across cases |
| `POST /reset` | `{set}` | `class` or `practice`: `seed.py --reset` from the console |
| `GET /triage/replies` | | the thirty labelled replies in `triage/data/` |
| `POST /triage/run` | `{reply_id}` or `{account_id, text}`, `live`, `override` | one reply through `triage/`'s five steps |

## The statuses

`active` (the schedule runs) · `paused` (a promise to pay; wakes the morning after) ·
`disputed` (a person owns it; no timers) · `awaiting_approval` (a stage marked
`requires_human` waits, with its draft) · `settled` · `escalated` (a person has it; `resume`
puts it back).

## The compliance rules, in one place (`rules.choose_next_action`)

1. nothing owed → settle · 2. a person owns it → wait · 3. paused → wait for the date ·
4. no stage left → escalate (the policy ran out, the balance did not) · 5. no stage eligible
yet → wait for the next one · 6. the latest eligible stage is the one to send; earlier
unsent stages are skipped as stale · 7. `requires_human` → park for approval, with a draft ·
8. quiet hours (customer local time), the weekend pause, the weekly cap → defer · 9. send.

## Try this

1. Run one pass on Acme (`--evaluate C-1001`), then add a stage to `policies.json`, bump the
   version, advance a day, evaluate again. Old audit rows keep v4; new ones say v5.
2. Start the worker, advance to D+3, and watch who wakes: Initech (Los Angeles) is deferred
   for quiet hours; `--advance 0 --hours 7` sends it.
3. Kill the worker, advance two days, start it again: Umbrella's first notice fires once,
   late. Nothing was in memory.
4. Pay Acme $120, send Globex a dispute, resolve it with a $60 credit, advance to D+10:
   the second notices go out on the remaining balances. Pay the rest: settled.
5. Advance to D+21: the final notices park with their drafts. Approve one, reject the other.
6. Break the amount check: in `tests/conftest.py` the fake model can write the wrong amount;
   in class, ask the real model for a discount in the prompt and watch the template take over.
7. Redis instead of the ticker's query (optional, needs Docker and `pip install redis`):
   `docker run -d -p 6379:6379 --name dunning-redis redis:7`, then start the worker with
   `$env:DUNNING_QUEUE="redis"`. Advance the clock: the same wake-ups, out of a sorted set.
   Look inside with `docker exec dunning-redis redis-cli ZRANGE dunning:due 0 -1 WITHSCORES`.

## A toy for comparison (`steps.py`), and the live build

`steps.py` is a reference file, not part of the class: the cadence as four steps in code,
one thread per case, sending through `outbox.send_now`, which checks nothing and records no
policy version. Its threads live in memory, so a restart starts them over; job queues and
durable engines survive restarts. Run it (`py steps.py`) instead of the worker, move the
clock, pay Acme in full, restart it, and compare what it sends with what the engine sends.

`live/` rebuilds this engine with a coding agent, one prompt per stub.
`py dunning\live\start.py --force` makes `dunning-live\` beside this folder: a copy in
which `rules.py`, the pass in `engine.py`, `worker.py` and `inbound.py` are stubs whose
docstrings are the spec. Give an agent the three prompts in `live/prompts.md`, one at a time (the worker is restored; its prompt is in `live/prompts-extra.md`), and
run the tests after each: 17, 31, 41, 42, then 50 passed. `py ..\dunning\live\restore.py
<step>` copies a finished file in when a prompt goes wrong.

## Tests

```
cd dunning
py -m pytest tests -q
```

Offline: the model is a scripted fake (`tests/conftest.py`), the clock is set by the test,
Redis is a stub, and every test gets its own database and policy file.
`tests/test_console.py` walks the console's tour and checks every answer it reveals, and
runs the Triage view's endpoints with the labels; the live-build copy leaves it out, so the build's counts stay 17, 31, 41, 42, 50.

## Files

| File | What it is |
| --- | --- |
| `policies.json` | the cadence, per tenant, versioned: the one file operations edit (retail, business, and the practice book's subscriptions) |
| `models.py` | `Policy`, `Stage`, `Case`, `Draft`, `InboundIntent`, `Decision` |
| `store.py` | SQLite: `cases`, `events` (the audit log), `messages` (the outbox), `clock`, `policy_versions` |
| `clock.py` | the simulated clock (`DUNNING_CLOCK=wall` for the real one) |
| `policy.py` | reads `policies.json`, records each version it applies |
| `rules.py` | `choose_next_action`: every compliance rule |
| `drafting.py` | the model drafts; `check_amounts` is code; the template fallback |
| `inbound.py` | the model classifies a reply; `apply` changes the case |
| `engine.py` | the LangGraph pass and the doors: `evaluate`, `record_payment`, `record_inbound`, `decide_approval`, `resolve_dispute`, `resume_case`, `tick` |
| `queues.py` | `SqliteQueue` (default) and `RedisQueue` |
| `worker.py` | the ticker, with a heartbeat |
| `app.py` | FastAPI; serves `console/` |
| `explain.py` | *Why now?* for the console: `rules.choose_next_action` called read-only, in plain words |
| `console/` | the browser console (`index.html`, `console.css`, `console.js`), the tour (`tour.js`, data), the playground (`playground.*`) and the as-steps drawing (`cadence-as-steps.*`) |
| `cli.py`, `seed.py` | the terminal, and the cases: the class set and the practice book |
| `llm.py`, `errors.py`, `obslog.py` | copies of checkpoint 02's, with two error codes added |
