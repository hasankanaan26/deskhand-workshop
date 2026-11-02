# Capstone Design Brief — Production AI Workflow for Support Ticket Resolution

> Student-facing handout. Distributed end of Week 2, due before demo day (Session 12).
> Adapted for teaching from a real senior/lead AI engineer interview assignment.

## Purpose

This brief asks you to design (building is optional: the reference implementation is the course repo) a **production-grade AI workflow**, not a demo.

We are not looking for a perfect implementation or polished presentation. We care about **how you think**: how you structure AI systems, how you handle failure modes, and how you make production tradeoffs. This is the difference between "it worked on my machine once" and "my team can run this at 3 AM."

---

## The System: "Resolvo" — AI Support Resolution for an Online Store

You work at a mid-size e-commerce company. Customers submit support tickets in natural language:

> "I ordered the wireless headphones two weeks ago, order #48291. They arrived with a cracked case. I want a refund or a replacement — and honestly the delay was unacceptable, so I'd like to know what you'll do about it."

The system should produce:

- A valid, machine-readable **resolution plan** (category, priority, suggested actions, refund amount if any, whether human approval is required)
- A **customer-facing reply draft** (polite, grounded in actual policy)
- An **internal note** for the support agent (reasoning, citations to policy docs, order data used)
- A **validation report** (does the plan comply with company policy?)
- A **confidence score**

The workflow involves LLM calls, structured output generation, retrieval over policy documents, validation, repair, tool calls (order lookup, refund issuance), human approval gates, streaming responses, and persistence of intermediate state.

### Current Problems (the system you are replacing)

The v1 support bot has real production issues:

- Sometimes it returns invalid resolution schemas.
- Sometimes it sends customers a generic LLM apology instead of an answer.
- Sometimes partial failures are streamed to the support agent's screen.
- Retries are not always effective.
- The order-lookup API may fail, time out, or return inconsistent data.
- It once suggested a refund larger than the order total.
- Costs spike on long ticket threads.
- The same ticket can produce different resolutions.
- A crash after issuing a refund left the ticket unresolved — and a retry refunded twice.
- Nobody can debug why a specific run failed.

---

## Your Task

Design a better production AI workflow for this system. Cover the following areas in a short technical design document (2–4 pages). Diagrams, pseudocode, and tables welcome.

### 1. Workflow Architecture

- Why is this problem worth an LLM at all (volume, tolerance of review, cost of verifying the output), and why a fixed workflow rather than an agent that chooses its own steps? If one step would be better as a bounded agent, name it and say what bounds it.
- What agents, graph nodes, or workflow steps exist, and what is each responsible for?
- What runs synchronously vs asynchronously?
- Which steps wait (for a date, a reply, a payment, an approval), what wakes them, and where is that saved?
- What gets persisted?
- How are customer-facing messages separated from machine-facing outputs?
- Where do tool calls, validation, and repair happen?
- How is the final output produced?

### 2. State / Graph Design

Define the state object that flows through the workflow. Include fields such as: request ID, raw ticket, normalized ticket, retrieved policy chunks, order data, generated resolution plan, validation errors, repair attempts, tool results, approval status, current status, next wake-up time, who owns the case, customer reply draft, internal error code, user-visible error message, cost/latency metadata.

### 3. Structured Output, Validation, and Repair

- How is the resolution schema generated and validated (JSON/schema level)?
- How does **business-rule validation** work (refund ≤ order total, action allowed for this ticket category, policy citations exist)?
- What happens when the schema is invalid? Do you use an LLM repair step? How many attempts? What happens when repair fails?
- How are schema versions handled?

### 4. Failure Handling

Explain how your workflow handles each of these. Be specific — "we retry" is not a failure model.

| Failure Scenario | Expected Explanation |
| --- | --- |
| LLM returns malformed resolution JSON | What happens next? |
| Order-lookup API times out | Retry, fallback, or fail? |
| Plan is valid JSON but violates policy (refund > order total) | How is this detected and repaired? |
| Streaming connection drops mid-reply | What does the support agent see? |
| Agent refreshes the page mid-generation | How does the system reconnect? |
| LLM provider rate limits | What retry or fallback strategy? |
| Retrieval returns two contradictory policy documents | Which wins, and how does the answer reflect it? |
| Same ticket produces different resolutions | How do you improve determinism? |
| Cost spikes on a 40-message ticket thread | How do you control it? |
| A node crashes **after** the refund tool executed but before the ticket updated | What state is persisted, and how does the run recover without refunding twice? |

### 5. Streaming and Frontend Contract

- What events are streamed to the support agent's UI, and what should **not** be streamed?
- How are partial failures and terminal states communicated?
- How does reconnect work? How does the frontend know a run is active, completed, failed, or recoverable?

### 6. Cost and Latency Control

Per-node cost tracking, token limits, retry limits, model routing (cheap model for triage, capable model for the plan?), caching, prompt compression for long threads, budget caps, fail-fast criteria, and cost per **successful** resolution.

### 7. Observability and Debugging

Logs, metrics, traces, error taxonomy, request/run IDs, prompt and model versioning, tool-call tracking, validation-failure tracking, cost/latency dashboards. The bar: an engineer can debug a failed ticket run **without guessing**.

### 8. Pseudocode

Provide pseudocode for the core workflow (Python-like or LangGraph-style):

```python
class TicketResolutionState(TypedDict):
    request_id: str
    ticket_text: str
    normalized_ticket: str | None
    order_data: dict | None
    policy_chunks: list[dict]
    resolution_plan: dict | None
    validation_errors: list[str]
    repair_attempts: int
    requires_approval: bool
    customer_reply_draft: str | None
    internal_note: str | None
    status: Literal["queued", "running", "needs_repair",
                    "awaiting_approval", "failed", "completed"]
    user_visible_error: str | None
    internal_error_code: str | None
    cost_usd: float
```

```python
def normalize_ticket(state): ...
def fetch_order_data(state): ...
def retrieve_policy(state): ...
def generate_resolution_plan(state): ...
def validate_plan(state): ...          # schema + business rules
def repair_plan(state): ...
def approval_gate(state): ...          # human-in-the-loop for write actions
def execute_actions(state): ...        # idempotent!
def draft_customer_reply(state): ...
def finalize(state): ...
def handle_failure(state): ...
```

```python
if plan_valid:
    go_to("approval_gate" if plan.requires_approval else "execute_actions")
elif repair_attempts < 2:
    go_to("repair_plan")
else:
    go_to("failed")
```

### 9. Production Readiness Checklist

This ships next week. As the lead engineer, what do you require before approving release? Include: required tests, golden ticket set, regression checks, failure simulation, cost and latency benchmarks, determinism checks, streaming/reconnect tests, observability requirements, rollback plan, feature flags / gradual rollout, and the ownership/support model.

---

## Deliverable

A short technical design document (2–4 pages) covering the sections above, presented at demo day in a five-minute design review; a live demo is welcome if you built something. We evaluate **clarity of thinking, production judgment, and technical depth**, not formatting.

Students who build: say what you changed relative to checkpoint 04.

## How demo day runs

Friday 13 November, Session 12. Your full document is collected on Saturday 7 November, and the same day you get your slot and the two rubric areas you will answer for.

- You have five minutes. Put one page of your document on the screen (or your build, if you have one) and walk the room through it for two and a half minutes.
- The room then calls one failure row from section 4, and you have a minute to answer it.
- The room shows thumbs on your two rubric areas (strong, weak, or not shown) and asks one question. Then the next presenter swaps in.
- If you built something, say by Wednesday 11 November. You may swap one of your areas for the one your build shows best. Name the ticket before each run; the room calls the outcome first.
- The room's thumbs are feedback, not the grade. Your grade is Hasan's written review of the full document against the rubric below, sent by Friday 20 November.

## Evaluation Rubric

| Area | Strong submissions | Weak submissions |
| --- | --- | --- |
| **Workflow architecture** | Justify the use case (volume, review tolerance, verifiability) and the shape (why a workflow, not an agent); clearly separate generation, validation, repair, approval, execution, explanation, finalization, error handling, streaming, persistence | A single LLM call with basic JSON parsing, or an agent loop with no argument for why the steps could not be fixed |
| **Reliability** | Clear terminal states, bounded retries, error-specific retry policies, durable state, idempotency, recovery from partial failures, safe user-facing errors | Generic retries with no failure boundaries |
| **Structured output discipline** | Strict schema validation, business-rule validation, schema versioning, repair loops, machine output separated from human explanation | Trusting LLM output directly |
| **Cost & latency awareness** | Per-node cost tracking, token controls, budget caps, model routing, caching, cost per successful run | Ignoring cost, or only "use a cheaper model" |
| **Observability** | A failed run is debuggable via request ID, run ID, prompt/model version, tool logs, validation errors, retry history, terminal state | "We add logging" |
| **Production ownership** | Answers what blocks release, what is acceptable risk, what is monitored, flagged, and rolled back, and what the team needs to operate it | Implementation only; no rollout, quality gates, or supportability |
