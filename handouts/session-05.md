# Session 5, try this tonight: your use-case card, then crash after a refund

You read the tool-calling pre-read, watched a service die right after paying, restart, and
pay only once, walked the four stops in the code, and then spent the second half with no
code: which jobs are worth handing to an LLM, one job taken apart, and what tool calling
looks like inside a company. Here is the same thing to run yourself, plus one piece of
paper. About 35 minutes. Everything happens in `checkpoints/02-service`, with the mock order API in `shared/mock_order_api`.

## 1. Your use-case card (10 min, on paper)

One task from your own job that someone has wanted to hand to an LLM. Fill in the card:

```
Task:            one sentence, from your own job
Who asks for it: a team, a customer, a manager
Reads:           what it needs to look up
Writes:          what it changes in the world, or "nothing"
Can't be undone: the step, if any, and who signs it
Volume 0-2 / Review 0-2 / Check 0-2  =  __ / 6
```

Volume: does it happen 50 times a day or 5 times a month? Review: does a person see the
risky part before it acts? Check: can you tell it's right much faster than doing it
yourself? Six is a strong candidate; three or less usually isn't worth building. Most cards
land at 4 or less, and that is the point of the filter.

Bring the card on Friday, when we decide which shape each job needs: one prompt, a chain, a
workflow, or an agent. Then score your capstone the same way. That becomes §1 of your
design doc: why this problem, why this shape.

## 2. Bring it up (5 min)

One terminal, from the repo root, venv active:

```
py lab\server.py
```

Open http://localhost:8010/?notes=1&cites=1 and press Setup. Start the mock order API, then
start checkpoint 02 with **Start · plain**. Every run below costs well under a cent. Keep a
second terminal in `checkpoints\02-service` for step 3's log search.

Skipped the pre-read's "Try it yourself"? The gated refund and the chaos modes are there,
and they need exactly this setup.

## 3. Crash after the refund, then count the money (15 min)

In Setup, on checkpoint 02's row: **Stop**, tick "crash after refund", **Start · plain**.
The card shows an orange "crash after refund" badge.

Run T-1004 in the lab. If it stops at the gate on your model, reject it and use T-1048
instead: an approved run that crashes is not resumed. The service dies right after
the refund is sent, before the run is saved. The headline turns orange, "service down ·
reconnecting". Within a minute, in Setup, **Start · plain** with nothing ticked. It finds the
unfinished run and does the refund step again, and the same panel carries on. Then, in the
terminal in `checkpoints\02-service`, with the request id from the lab:

```
Select-String <request_id> logs\runs.jsonl
```

Two `refund.issued` lines, the same refund id, the same key: two requests, one refund.

Now break it. In `nodes.py`, `execute_actions`, change the key to `uuid.uuid4().hex` and
add `import uuid` at the top. In Setup: Stop 02, tick "crash after refund", Start · plain.
Run T-1004, let it die, Start · plain with nothing ticked, search the log. Two refund ids:
the customer was paid twice. Put the line back, then Stop and Start · plain once more.

## 4. Prove it without a key (2 min)

```
cd checkpoints\02-service
py -m pytest tests/test_idempotent_refund.py tests/test_crash_after_refund.py -q
```

5 passed, offline, under a second. A fake order API counts the requests and the refunds.

## Stretch: the same crash on LangGraph (15 min)

Start 02 with **Start · LangGraph** instead (or `$env:DESKHAND_RUNTIME = "langgraph"` in the
terminal) and repeat step 3. Predict first: does LangGraph stop the refund step from
running twice? Read `graph_lg.py` next to `graph.py`, and the reference slides after the
deck's "After class" divider.

## Going further

Two to four links per topic, each with one line on why it matters.

**Tool calling, per provider.** The same schema shape under three names.
[OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling),
[Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview),
[Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling).

**MCP, the Model Context Protocol.** Tool calling standardised: a server offers tools, any
client can use them. [The protocol site](https://modelcontextprotocol.io/), the
[public server registry](https://registry.modelcontextprotocol.io/), and the
[announcement](https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation)
of its move to the Linux Foundation's Agentic AI Foundation (9 December 2025; co-founded
by Anthropic, Block and OpenAI, with support from Google, Microsoft, AWS, Cloudflare and
Bloomberg).

**The platforms.** Read each one's governance page before its demo: it says what you still
own. [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/),
[Microsoft Foundry Agent Service](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)
and [Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/),
[Gemini for Business](https://cloud.google.com/gemini-enterprise) (sold as Gemini Enterprise editions) and Google's
[Agent Development Kit](https://adk.dev/),
[Salesforce Agentforce](https://www.salesforce.com/agentforce/). A gateway in front of all
of them: [LiteLLM](https://docs.litellm.ai/).

**Safe writes.** [Stripe's idempotent requests](https://docs.stripe.com/api/idempotent_requests)
are the pattern Deskhand copies. Once a run writes to two systems you need the
[transactional outbox](https://microservices.io/patterns/data/transactional-outbox.html)
and the [saga pattern](https://microservices.io/patterns/data/saga.html).

**The risks.** [OWASP Top 10 for LLM applications](https://genai.owasp.org/llm-top-10/),
in particular LLM06, Excessive Agency, and Simon Willison's
[lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/): private
data, untrusted content and a way to send something out.

**Scoring use cases.** [RICE](https://www.intercom.com/blog/rice-simple-prioritization-for-product-managers/),
the version your product team uses, next to the three questions from class.

## Before Friday

Bring your card. Skim `checkpoints/02-service/agent_demo.py`: the same two tools, with the
model in charge. More exercises: `checkpoints/02-service/README.md` ("Try this", items 1
to 3).
