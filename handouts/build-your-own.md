# Build your own support agent

This guide is optional. It isn't graded, and nobody reviews your build: the design doc is
still the capstone. What you get instead is a way to check yourself. Every milestone below
ends with a command whose output says pass or fail, and the last section has a checklist,
a prompt for your AI assistant, and a way to swap repos with another builder.

You'll turn checkpoint 04 into a support agent for a domain you pick: your own documents,
your own categories, one read tool and one write tool against your own mock API, approval
before the write, cited answers, and a release gate. It's the same system you watched being
built, with your data in it.

Released with Session 9 (6 November). Milestones 1 to 6 use what you've seen by then;
milestone 7 needs Session 10's eval suite. If you finish by Friday 13 November, you can show
it in your demo-day slot instead of the doc on screen. Most people won't finish by then, and
that's fine: the guide works the same after the course.

## Scope rules

Set these before you start, because the build gets out of hand without them.

- One domain. One write tool. At most 20 documents.
- Keep it a workflow. The Session 6 pre-read's rule: if you can draw the graph, build the
  workflow. Use an agent only if your tickets really can't be drawn as a graph, and say why
  in your notes.
- Keep the id shapes: `T-2001` for tickets, `O-301` for the records your read tool looks up.
  The code finds record ids with `_ORDER_ID_RE` in `nodes.py` (`O-` and three or more
  digits). Change that regex if you must, and its test with it.
- The easiest domains keep a write with an amount in it: a credit, a fee waiver, a refund
  of a subscription. The amount rules in `rules.py` then carry over almost unchanged. A
  write with no amount (reset a password, extend a loan) works too, but you'll rewrite more
  of `rules.py`.

Keep a `NOTES.md` in your folder. Every time this guide says "write down", it goes there.

## Setup

From the repo root, venv active:

```
cp -r checkpoints\04-production work\my-agent
cd work\my-agent
Remove-Item -Recurse -Force qdrant_data, logs, runs*.db*, evals\results.json, evals\rag_results.json -ErrorAction SilentlyContinue
py -m pytest -q
```

The `work\` depth matters for the same reason it did in Session 3: the code finds the repo
`.env` and `shared\` by walking up two folders, and `work\my-agent` is as deep as
`checkpoints\04-production`. The tests should pass before you change anything. That's your
baseline.

## Milestone 1: twenty tickets with answers

Write the tickets first, before any code. They decide everything else.

1. Create `data\tickets.json` in the same format as `shared\data\tickets.json`: one entry per
   ticket with `ticket_id`, `customer_name`, `order_id` (your record id, or null),
   `channel`, `messages` and `tags`.
2. Write the expected answer for each in `golden\golden_tickets.json`, replacing the Nimbus
   entries. Same keys: `expected_category`, `expected_action_types`,
   `expected_requires_approval`, `notes`, and `must_not_action_types` on any ticket where
   one action is an automatic fail. Leave `must_cite` and `reference_contexts` as `[]`
   until milestone 2.
3. Point the code at your tickets. Four files read `shared\data\tickets.json`:
   `Select-String -Path *.py, evals\*.py -Pattern 'tickets.json'` finds them. Change each to
   your `data\tickets.json`.

At least 3 tickets must expect `escalate_to_human`, and at least 2 must be injection
attempts (text that tries to get your write tool called), each with your write action in
`must_not_action_types`. Save this as `check_golden.py` and run it with `py check_golden.py`:

```python
import json

golden = json.load(open("golden/golden_tickets.json", encoding="utf-8"))["golden_tickets"]
tickets = {t["ticket_id"] for t in json.load(open("data/tickets.json", encoding="utf-8"))["tickets"]}
escalate = sum("escalate_to_human" in g["expected_action_types"] for g in golden)
forbidden = sum(bool(g.get("must_not_action_types")) for g in golden)
missing = [g["ticket_id"] for g in golden if g["ticket_id"] not in tickets]
print(f"{len(golden)} golden, {escalate} escalate, {forbidden} with a forbidden action, missing: {missing}")
ok = len(golden) >= 20 and escalate >= 3 and forbidden >= 2 and not missing
print("PASS" if ok else "FAIL")
```

Done when it prints PASS.

## Milestone 2: your documents

1. Put 3 or more documents in a new `docs\` folder, as Markdown. Each starts with a header
   line like the policy files do, then splits into sections on `##` headings:

   ```
   <!-- doc_id: plans-and-billing | version: 1.0 | effective: 2026-11-01 -->
   ```

   `ingest.py` refuses a file without it. A section becomes `doc_id#section-id`, the id the
   planner may cite.
2. In `ingest.py`, change `POLICIES_DIR` to `Path(__file__).resolve().parent / "docs"`.
3. `py ingest.py`. It prints how many sections it indexed. Check they're yours.
4. Fill `reference_contexts` (the sections a correct answer needs) and `must_cite` (their
   doc ids) in your golden set.
5. `py evals\rag_evals.py --retrieval-only`. Search only, a fraction of a cent.

Done when, for at least 15 of your 20 tickets, the "missed sections" column is empty. If a
ticket misses, fix the document or the query in `retrieve.build_query`, not the prompt
(Session 9, step 5).

## Milestone 3: your categories and actions

1. In `models.py`, replace the categories in `TriageResult.category` with yours, and the
   actions in `Action.type`. Keep `escalate_to_human`, `request_more_info` and
   `reply_only`. Replace `issue_refund` with your one write, and remove `send_replacement`.
2. Find every place the old names live:
   `Select-String -Path *.py, tests\*.py, evals\*.py -Pattern 'issue_refund|send_replacement|refund_request|product_defect'`.
   Update `rules.py`, `nodes.py` and the tests.
3. Rewrite the three prompts in `nodes.py` for your domain: `_TRIAGE_SYSTEM`,
   `PLAN_SYSTEM_PROMPT` and `_REPLY_SYSTEM`. Keep the lines about untrusted text and
   citations.

Some tests check Nimbus facts (a ticket id, `O-48291`, a policy section). For each one that
fails, adapt it to your domain or delete it, and write down which and why. Don't delete the
tests of a mechanism: idempotency, the kill switch, the budget, crash recovery, stream
replay, the guards. Those are the tests that matter most once the data is yours.

Done when `py -m pytest -q` is green and `py eval_stub.py` runs all 20 tickets with no
`error` row. Read its category column: that's triage on your tickets.

## Milestone 4: your tools

1. Copy the Nimbus mock API: `cp -r ..\..\shared\mock_order_api ..\my-api`.
2. In `..\my-api`, replace `orders_seed.json` with your records and rename the endpoints to
   fit your domain. Leave the `Idempotency-Key` handling as it is: that's the part that
   stops a write happening twice.
3. In `nodes.py`, change `lookup_order` and `issue_refund` (your read and your write) to
   call your endpoints.
4. Stop the Nimbus mock and run yours on port 9000:
   `cd ..\my-api; py -m uvicorn app:app --port 9000`.

Done when the same write sent twice with the same key returns the same id:

```
$h = @{ "Idempotency-Key" = "check-1" }
Invoke-RestMethod -Method Post http://localhost:9000/<your write> -Headers $h -ContentType application/json -Body '<your body>'
Invoke-RestMethod -Method Post http://localhost:9000/<your write> -Headers $h -ContentType application/json -Body '<your body>'
```

and your version of `tests\test_idempotent_refund.py` passes.

## Milestone 5: approval before writes

Decide your threshold: above which amount, or for which kind of write, a person must say
yes. Set it in `rules.py` (`AUTO_APPROVE_LIMIT_USD`, and `approval_reasons` for anything
that isn't an amount). Code decides the gate, never the model's own flag.

Done when your version of `test_large_refund_requires_approval` in `tests\test_rules.py`
passes, and one ticket above the threshold, submitted to the service
(`py -m uvicorn app:app --port 8000`), shows `awaiting_approval` in `GET /runs/<id>`.

## Milestone 6: cited answers

Rule 5 in `rules.py` says which actions must cite a section. Make it your write action.

Done when your versions of `test_refund_without_citation_fails` and
`test_citation_must_come_from_retrieved_chunks` pass: a plan with no citation, or with an
invented one, is rejected.

## Milestone 7: golden set and release gate

This one needs Session 10.

```
py evals\run_evals.py
py evals\run_evals.py
```

Twice, because one run is noise. Then set `evals\thresholds.json` the way its `_comment`
describes: each rate is the lower of your two runs minus 0.05, rounded down.
`forbidden_action_rate_max` stays `0.0` whatever happens. The cost and latency caps are your
budget, not a measurement. Then:

```
py -m pytest -q evals\test_release_gate.py
```

Done when the gate passes, or when you can write down which metric closes it and why. A red
gate you can explain is a finished milestone. A green gate whose thresholds you lowered
until it passed isn't.

## Milestone 8 (stretch): containers

Run your mock API and your service in containers, and resolve one ticket end to end. The
mock API is easy: the Nimbus `Dockerfile` came with your copy, and works once the file names
match. The service is harder, and these are the problems you'll hit:

- Secrets. Never `COPY .env` into an image. Pass it at run time:
  `docker run --env-file ..\..\.env ...`.
- State. `qdrant_data\`, `runs.db` and `runs.langgraph.db` live in the folder. With `--rm`
  they vanish when the container stops, unless you mount them as volumes (`-v`).
- Paths. Inside the image there are no parent folders to walk up to. Anything still reading
  `shared\` fails. `Select-String -Path *.py -Pattern 'parents\['` shows what's left.
- Networking. Inside a container, `localhost` is the container itself. Set
  `-e MOCK_ORDER_API_URL=http://host.docker.internal:9000` (Docker Desktop), or put both
  containers on one `docker network`.
- Listening. uvicorn needs `--host 0.0.0.0`, as in the mock API's Dockerfile.

Done when both run with `docker run` and one ticket goes from `POST /tickets` through
approval to `completed`.

## Checking yourself

### The checklist

The same six areas the design doc is graded on (Session 12, slide 5, from
`capstone-brief-support-resolution.md`), turned into questions about your build. Answer
each yes or no, with the file that proves it.

Workflow architecture
- [ ] You can say in one sentence why your task needs a model at all, and which steps are
  plain code.
- [ ] Plan, check, approve, act and reply are separate nodes in `graph_lg.py`. The model
  never calls the write tool itself.

Reliability
- [ ] When your API times out or errors, the run ends `failed` with a named code from
  `errors.py`, after a fixed number of retries.
- [ ] The same write, repeated with the same key, happens once (milestone 4).

Structured output
- [ ] Triage and plan are schemas in `models.py`. A plan that breaks a rule is repaired at
  most `MAX_REPAIR_ATTEMPTS` times, then the run fails.
- [ ] Every rule your domain needs is in `rules.py`, not only in a prompt. (Session 9's trap:
  a rule that lives only in the prompt is a request.)

Cost and latency
- [ ] `DESKHAND_BUDGET_USD_PER_RUN` fits your domain, and you know your cost per resolved
  ticket from `py dashboard.py` or `evals\results.json`.
- [ ] You know your p95 latency, and it's under your cap.

Observability
- [ ] Given the `request_id` of a failed run, you can find the failing node and its error
  code in `logs\runs.jsonl` in under a minute. Try it on one.

Production ownership
- [ ] Your release gate has your thresholds, and you know which metric closes it first.
- [ ] `DESKHAND_KILL_SWITCH=1` stops a run waiting at your gate, before the write.
- [ ] You can say how to roll back each of: the model, a prompt or rule, your documents.
- [ ] Your injection tickets never trigger the write: `forbidden_action_rate` is 0.

### Ask your assistant, then check its answers

Paste this into your AI coding assistant with `work\my-agent` open, followed by the
checklist above:

> Review this repo against the checklist below. For each item, say met or not met, and give
> the file and line that shows it. If you can't find evidence, say not met. Don't suggest
> fixes yet.

Session 3's rule applies: an assistant's claim isn't true until you've opened the file and
line it names. When it says "met", check the line. When it says "not met", find out whether
it missed something or you did.

### Swap with another builder

If someone else is building, swap repos. Each of you runs the other's golden set
(`py evals\run_evals.py`) and the gate, and sends back the one ticket you think their
system gets wrong. Twenty minutes each, and it finds things your own checklist won't, since
you wrote both the tickets and the code.
