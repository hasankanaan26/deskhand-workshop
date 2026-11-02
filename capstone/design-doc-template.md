# Resolvo Design Doc — Template

> Student handout. You fill this in **one section per week** (see the schedule at the bottom), not the night before demo day. Target length when assembled: 2–4 pages. Diagrams, tables, and pseudocode count as content; formatting does not.
>
> Each section has three prompts. Answer them in your own words, from your own repo. If you cannot answer one yet, write *"not yet — because …"*. That sentence is worth more than a guess.

---

## 0. Header

- **Author:** · **Repo:** · **Checkpoint your build is based on:**
- **Four versions your current build runs against:** prompt(s) · model id · schema (`plan_version`) · corpus (policy version)

## 1. Workflow architecture

1. List your steps in order as a table: step · one job in ≤ 8 words · who (code, model, a person) · reads · writes · check · **waits for** (a date, a reply, a payment, a person's yes, or nothing) · **woken by** (a timer, an event from outside, a person). Steps that wait are where your system needs a saved row, a wake-up time and a way back in.

   | Step | One job (≤ 8 words) | Who | Reads | Writes | Check | Waits for | Woken by |
   | --- | --- | --- | --- | --- | --- | --- | --- |
   | | | | | | | | |

2. Which steps run synchronously inside the HTTP request, and which run asynchronously? Why is that line where it is?
3. Where exactly do tool calls, validation, and repair happen, and what is persisted after each node?
4. Why this problem, why this shape: score the use case on the Session 5 filter (volume · tolerant of review · cheap to verify, 0–2 each) and say in two sentences why it is a workflow and not an agent. If you would promote one step to a bounded agent later, name the step and what bounds it (budget, gate, key).

*Include:* a diagram of the graph (a photo of a whiteboard is fine, `draw_mermaid()` output is better).

## 2. State

1. Paste your `TicketResolutionState` (or an abbreviated version).
2. Which fields are for engineers only and which may reach a customer or agent? Mark them.
3. What distinguishes a *request* from a *run* in your system, and where is that visible?
4. Which field holds the next wake-up, and which status says a person owns the run?

## 3. Structured output, validation, repair

1. How is the plan schema enforced, and what is validated *beyond* the schema (business rules)? Name three rules.
2. When validation and generation disagree, which wins, and how does the repair prompt learn what went wrong?
3. How many repair attempts, what happens on exhaustion, and what happens to persisted `v1` plans when `plan_version` becomes `v2`?

## 4. Failure handling

Fill every row. "We retry" is not an answer; name the error code, the terminal or resumable state, and what the agent sees.

| Scenario | What happens (code · state · user sees) | Reproduced in my lab? |
| --- | --- | --- |
| LLM returns malformed resolution JSON | | |
| Order-lookup API times out | | |
| Plan is valid JSON but violates policy | | |
| Streaming connection drops mid-reply | | |
| Agent refreshes the page mid-generation | | |
| LLM provider rate limits | | |
| Retrieval returns two contradictory policy documents | | |
| Same ticket produces different resolutions | | |
| Cost spikes on a 40-message thread | | |
| Crash after the refund executed, before the ticket updated | | |

## 5. Streaming and frontend contract

1. List your event types and what each carries. Which one thing streams token-wise, and why only that?
2. How does a client tell *active* from *completed* from *failed* from *recoverable* (awaiting approval, resumable)?
3. Describe reconnect in three steps.

## 6. Cost and latency

1. Your cost per **successful** resolution and your p95 latency, from your own eval run. Which node dominates each?
2. Your budget cap, your token caps, your retry limits, your model routing — as a table.
3. Which failures fail fast, which degrade, which wait? (Reuse the Session 5 decision table if it matches yours.)

## 7. Observability

1. Walk one failed run from `request_id` to root cause using only your logs. Paste the log lines that got you there.
2. Which of the four versions (prompt, model, schema, corpus) are recorded on every run, and where?
3. What does your dashboard show, and which number would you look at first at 3 AM?

## 8. Pseudocode

Paste the routing logic of your graph (the `if valid / elif repairs < 2 / else fail` shape), and one node in full.

## 9. Production readiness

1. **Gates:** which metrics, which thresholds, what happens when one is red the day before release? Give one example where you would ship anyway and say why the risk is acceptable.
2. **Rollout and rollback:** shadow → canary → full — what is the flag, what is the allowlist, and what exactly do you roll back (which of the four versions) when the dashboard turns red?
3. **Ownership:** who owns this, what pages them (list the alerts), what does not page, and what is in the runbook.

---

## Schedule

| Write it after | Sections |
| --- | --- |
| Session 3 | §3 |
| Session 6 | §1, §2, §5 |
| Session 9 | §4 (rows 3 and 7), §3 business-rules addendum |
| Session 10 | §6, §9 gates |
| Session 11 | §4 (remaining rows), §7, §9 rollout and ownership |
| Session 12 | assemble, trim to 2–4 pages, peer review of §4 and §9 |

Graded with the rubric in [capstone-brief-support-resolution.md](capstone-brief-support-resolution.md).
