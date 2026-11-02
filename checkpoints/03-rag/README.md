# Checkpoint 03 — RAG over the Policy Pack (Sessions 7–9)

Checkpoint 02's planner obeyed hardcoded thresholds but had no idea what the
company's policies actually say — ask it about the refund window and it would
confidently invent one. This checkpoint grounds every plan in retrieved policy
text, makes citations a **validated requirement**, and measures the retrieval
layer with RAGAS. It also shows the limit of a prompt: the planted policy
contradiction is retrieved every time, and the model still picks a side.

Since the replan of 29 October 2026 it also teaches the production split: **code decides, retrieval grounds**.
Rule 6 checks the return window and final sale against the order API, and a
second corpus, the support knowledge base in `shared/kb/`, is searched by an
agentic `research_kb` node with permissions, freshness and measured retrieval.
See "The support knowledge base" below.

Diff this folder against `../02-service` to see exactly what RAG added.

## What it demonstrates

- **Structural ingestion on LangChain** (`ingest.py`) — LangChain's
  `MarkdownHeaderTextSplitter` cuts the policy markdown at `##` (chunk =
  section). Each chunk is a `Document` with metadata `{doc_id, section_id,
  version, effective}` and a stable id. LangChain's `QdrantVectorStore` stores
  them in Qdrant **local mode** (`qdrant_data/`, no server to run). The
  splitter normalises whitespace, so the stored text has the same words as the
  file but not the same bytes.
- **Embeddings with an honest bill** — `llm.embed_texts` calls LangChain's
  `AzureOpenAIEmbeddings` or `OpenAIEmbeddings`. Those classes return bare
  vectors and drop the API's token count, so `llm.py` counts tokens itself
  with tiktoken. On the 27 policy sections plus two short strings the local
  count matched Azure's bill exactly (2,017 tokens; the sections alone are
  2,008). The store gets `llm.MeteredEmbeddings`, which
  routes its calls through the same boundary. That is how you can see that
  `py ingest.py` makes **two** embedding calls: LangChain embeds
  `"dummy_text"` first to learn the vector size.
- **Retrieval** (`retrieve.py`) — top-k=6 with scores. The query is embedded
  through `llm.embed_texts` and searched with
  `similarity_search_with_score_by_vector`, so each call's cost comes back
  with it. Chunks land in `state.policy_chunks` via the new
  `retrieve_policy` node.
- **Citations as a business rule** — rule 5 in `rules.py`: any plan with
  `issue_refund` or `send_replacement` must cite at least one retrieved
  policy section, and citations must come from the retrieved set (no invented
  sources). Violations go through the same bounded repair loop as every other
  rule.
- **The contradiction, and why a prompt is not a guard** — refund-policy.md
  says 14 days; returns-and-exchanges.md says 30. The plan prompt says that
  when retrieved sections conflict, the plan must `escalate_to_human`. That
  rule lives **only in the prompt**: nothing in `rules.py` enforces it. On
  gpt-4.1-mini, T-1010 and T-1026 retrieved both sections in 6 runs out of 6,
  named the conflict in the rationale, and refunded $48.50 anyway. See Try
  this 1.
- **A first eval** (`eval_stub.py` + `golden/golden_tickets.json`) — 46
  golden tickets across the tag spectrum, graded on category, actions,
  approval, validity, and citations.
- **Measuring retrieval with RAGAS** (`evals/rag_evals.py`) — each golden
  ticket now lists `reference_contexts`, the sections a correct plan relies
  on. RAGAS scores, per ticket:
  - **context precision**: the share of retrieved sections that are on the
    list;
  - **context recall**: the share of listed sections that were retrieved;
  - **faithfulness**: the share of the rationale's claims that the planner's
    inputs support (an LLM judge).

  Precision and recall compare section ids, so they need no LLM and cost a
  fraction of a cent.

## Changed vs checkpoint 02

- New: `ingest.py`, `retrieve.py`, `golden/golden_tickets.json`,
  `eval_stub.py`, `evals/rag_evals.py`.
- `nodes.py`: new `retrieve_policy` node between `fetch_order_data` and
  `generate_resolution_plan`; the plan prompt now includes the retrieved
  sections, requires citations, and mandates escalation on contradictions;
  `finalize` writes cited sections (with versions) into the internal note.
- `rules.py`: `check_plan` takes `policy_chunks` and enforces rule 5
  (citations required for refund/replacement; citations must be retrieved).
- `llm.py`: 02's LangChain boundary plus embeddings: `embedding_model()`
  (LangChain's OpenAI or Azure class; on Gemini, OpenRouter, Ollama and LM
  Studio `OpenAIEmbeddings` at their `base_url`, sending text instead of
  token ids), `embed_texts` priced from a tiktoken
  count (`count_embedding_tokens`), `MeteredEmbeddings` for the vector store,
  and embedding entries in `PRICE_TABLE`. For the RAGAS judge it adds
  `fresh_chat_model()`, an uncached model, because RAGAS sets `.temperature`
  on the model it wraps. It also adds `usage_for()`, to price token counts
  that arrive through a callback.
- **LangGraph is the only runtime.** The plain runner (`graph.py`) and the
  runtime switch (`runtime.py`) are gone, and `DESKHAND_RUNTIME` is ignored.
  02's `agent_demo.py` (and its test) is not carried over either: it exists
  to show the shape Deskhand is not, and once is enough.
  `app.py` imports `graph_lg` directly, and an approval answers the run's
  pending `interrupt()`. `graph_lg.py` is 02's file with one new entry in
  `DESTINATIONS`, for `retrieve_policy`. `nodes.py` still knows nothing about
  LangGraph.
- Tests: recovery/happy-path tests mock `retrieve.retrieve`; new
  `test_ingest.py` (parsing, an offline Qdrant round trip with fake vectors,
  and a check that every `reference_contexts` id exists in the policy pack);
  new `test_rag_evals.py` (RAGAS's ID metrics really run, offline); new
  citation cases in `test_rules.py`. `test_recovery.py` runs
  on LangGraph only and resumes the crashed run from its checkpoint.
  `test_langgraph.py`, `test_llm.py` and `test_tools.py` come over from 02;
  `test_llm.py` gains `embed_texts` cases.

### Carried over from 02 (2026-10-09)

The ownership and no-silent-cap rules, `requested_refund_usd`, the richer
approval payload and `tests/test_laundering.py` are identical to 02's (see its
README, "The laundered refund"). `eval_stub.plan_ticket` now fills
`customer_name` from the dataset record, so the ownership rule runs in the
eval table exactly as in the service. The golden set has 46 entries; the
26 new ones carry `must_not_action_types`, which 03's table ignores and 04's
gate enforces.

### Carried over from 02 (2026-10-12): free text and the unknown sender

Identical to 02's (see its README, "Free text and the unknown sender"). The
gate is decided in code by `rules.approval_reasons`: a refund or replacement
whose sender nobody vouched for (`customer_match is None`) stops at the
approval gate whatever the amount, with the reason "sender unknown: nobody
vouched that this customer owns the order", carried as `approval_reasons` on
`approval.required` and the interrupt. `POST /tickets` takes an optional
`customer_name` with `ticket_text` (422 with a `ticket_id`), so the ownership
rule runs on free text too. Dataset tickets and the eval table are unchanged:
they always carry the record's name.

### The support knowledge base (2026-10-29, Sessions 7 to 9)

Added beside the policy path, which is unchanged: a ticket outside the three
categories below takes exactly the route, and sends exactly the prompts, it
did before.

- **Rule 6** (`rules.py`, `RETURN_WINDOW_DAYS = 14`): a refund outside the
  return window, counted from the order's `delivered_at`, or of a final-sale
  item is a violation. The business chose 14 (refund-policy v2.3 is newer and
  governs money); the 30 in returns-and-exchanges v1.1 is stale text its owner
  has to fix. Try this 1's contradiction tickets now fail in code, whatever
  the model decides.
- **The corpus** (`shared/kb/`, see its README): 30 documents in four formats
  (manuals, support-agent-only macros, carrier HTML pages, ops emails, a CSV
  of past tickets) plus `manifest.json`, the stand-in for SharePoint's
  permissions and document register: `allowed_groups`, `status`
  (`current` or `superseded`), `effective_from`. The files carry no metadata.
- **Ingestion** (`kb_parsers.py`, `kb_ingest.py`, `kb_sync.py`): one parser
  per format, the manifest joined fail-closed (no entry, not indexed), a dense
  and a BM25 sparse vector per chunk in collection `kb` of the same
  `qdrant_data/`. `kb_sync.py` re-embeds only changed files, deletes removed
  ones, and applies a permission or status change without an embedding call.
- **Search** (`kb_search.py`): `search_kb(query, caller_groups, ...)` has no
  default reader. The access and status filters run inside the Qdrant query,
  never after it. `mode="hybrid"` fuses dense and BM25 with RRF; `rerank=True`
  reranks a top-20 pool with a cached cross-encoder (fastembed, optional) or
  else the chat model, and the trace says which ran.
- **The loop** (`research_kb` in `nodes.py`): for `product_defect`,
  `replacement` and `shipping_delay` tickets, search, let the model grade the
  result, rewrite and retry, at most `MAX_KB_ATTEMPTS = 2` rewrites, each step
  an event (`kb.searched`, `kb.graded`, `kb.rewritten`). The reader groups
  come from `state.channel` (`POST /tickets` takes an optional `channel`:
  `agent_desk` reads support-agent documents, anything else reads as a
  customer), never from the model. The reply only sees chunks a customer may
  read. `kb:` citations pass rule 5 if retrieved, but never count as the
  policy citation a refund needs.
- **Measurement** (`evals/kb_retrieval_metrics.py`, `evals/kb_regression.py`):
  Hit@1, Hit@5, MRR and Recall@5 per mode over `shared/kb/eval_questions.json`,
  plus leaks and stale hits; the regression script compares a run with
  `evals/kb_baseline.json` and exits 1 on a drop. Measured on 29 October
  (text-embedding-3-small, gpt-4.1-mini reranking, 27 scored questions):

  | Mode | Hit@1 | Hit@5 | MRR | Leaks | ms per question |
  | --- | --- | --- | --- | --- | --- |
  | dense | 0.70 | 0.93 | 0.82 | 0 | 30 |
  | hybrid | 0.74 | 1.00 | 0.83 | 0 | 38 |
  | hybrid + rerank | 0.93 | 1.00 | 0.96 | 0 | 3,052 |

  The service runs hybrid without rerank: the planner reads the top 5, and
  hybrid already puts the right document there.

`eval_stub.py` still measures the policy path only (it calls the nodes by
hand and skips `research_kb`).

## Run it (Windows)

```powershell
cd checkpoints\03-rag

# 1. Build the index (one-time; re-runnable). Needs the provider keys in ..\..\.env
#    On Azure that includes AZURE_OPENAI_EMBEDDING_DEPLOYMENT (a separate deployment).
py ingest.py

# 2. Spot-check retrieval
py retrieve.py "cracked case, want a refund"

# 3. Same service as checkpoint 02 on LangGraph (mock order API on 9000 in
#    another terminal). Checkpoints land in runs.langgraph.db next to runs.db.
py -m uvicorn app:app --port 8000

# 4. Run the golden set (mock API + index required)
py eval_stub.py

# 5. Measure retrieval with RAGAS. Retrieval only: embeddings, no mock API,
#    about $0.0001. Writes evals\rag_results.json.
py evals\rag_evals.py --retrieval-only
#    The same, with a model rewriting each ticket into a search question
#    (or into a made-up policy paragraph, --query-style hyde) before the
#    search: 38 cheap chat calls, about a cent. The table gains a column
#    with the text that was actually searched. See Try this 5.
py evals\rag_evals.py --retrieval-only --query-style rewrite

# 6. The full pipeline plus the faithfulness judge (mock API required).
#    About $0.06 on gpt-4.1-mini, two to three minutes.
py evals\rag_evals.py

# 7. The support knowledge base: index it (about $0.0003), search it as a
#    customer and as a support agent, then re-sync after editing a file.
py kb_ingest.py
py kb_search.py "goodwill credit for a late parcel" --as customer --mode hybrid
py kb_search.py "goodwill credit for a late parcel" --as support-agent --mode hybrid
py kb_sync.py

# 8. Measure it (dense, hybrid, hybrid + rerank; about 4 cents with the
#    chat-model reranker), then compare with the saved baseline.
py evals\kb_retrieval_metrics.py
py evals\kb_regression.py
```

Two kinds of noise from RAGAS are harmless: a `ResourceTracker.__del__`
traceback at exit (a `multiprocess` bug on Python 3.12.0), and on Azure one
injection ticket skipped because Azure's own content filter refuses it.

Tests (fully offline — LLM, embeddings, and order API all mocked):

```powershell
py -m pytest tests -q
```

Note: the golden set's ticket ids assume the shared dataset's tag layout
(T-1001..T-1010 happy_path, T-1011.. ambiguous, etc. — see the `_comment` in
`golden/golden_tickets.json`). Skim `shared/data/tickets.json` once and adjust
any expectation you disagree with — arguing with your own golden set is
exactly the Session 9 exercise.

## Try this

1. **Prompts are not guards.** Submit T-1010 or T-1026, the
   `contradiction_trigger` tickets, through the service, and read the
   rationale. Measured on gpt-4.1-mini, plan only, three runs each:

   | Model | Escalated | Refunded $48.50 | Both sections retrieved | Conflict named in rationale |
   | --- | --- | --- | --- | --- |
   | gpt-4.1-mini | 0 of 6 | 6 of 6 | 6 of 6 | 6 of 6 |
   | claude-haiku-5-5 | 4 of 4 | 0 of 4 | not recorded | not recorded |
   | claude-sonnet-5-5 (plan) | 2 of 2 | 0 of 2 | not recorded | not recorded |

   The Claude rows come from checkpoint 04's eval runs (same plan prompt,
   same retriever), so they record the decision only.

   Retrieval did its job, and gpt-4.1-mini saw the conflict and said so ("we
   honor the longer 30-day window"). Then it did the one thing the prompt
   forbids. `py evals\rag_evals.py` scores these plans 1.00 on recall and
   about 0.9 on faithfulness, and `rules.py` passes them. Every check is
   green, and the decision is wrong.

   - Rerun it on your model. Use `LLM_PROVIDER=anthropic` with
     `EMBEDDING_PROVIDER=azure`, or a bigger Azure deployment. The Claude
     models follow this rule and gpt-4.1-mini never does, so whether the
     rule holds depends on which model you deploy. That is the problem:
     a money-moving decision should not change when the model does.
   - Make it one. Add a rule to `rules.py`: if the retrieved chunks include
     both `refund-policy#window` and `returns-and-exchanges#returns-window`,
     a plan with `issue_refund` must also contain `escalate_to_human`. Watch
     the repair loop push the model there. Then ask what that rule hardcodes,
     and how policy metadata (say, a `conflicts_with` field written at
     ingest) would let it generalise.
   - Last, delete the 30-day section from a copy of the policies, re-ingest,
     and watch the same ticket become a clean decline.
   - Since 29 October rule 6 does this in code: O-77201 was delivered 21 days
     ago, so the $48.50 refund is a violation and the repair loop moves the
     plan off it. Comment out the rule-6 block and rerun to see the before.
2. **Starve the retriever.** Run `py evals\rag_evals.py --retrieval-only`,
   then the same with `--k 1` (or change `TOP_K` in `retrieve.py` to 1).
   Measured: context recall falls from 0.83 to 0.43, while precision rises
   from 0.21 to 0.60. Which tickets lost a section they need? Now run
   `py eval_stub.py` at k=1: does the *citations* column or the *actions*
   column break first? (This is the "missing chunk" failure mode from
   Session 9, now with a number on it.)
3. **Invent a citation.** In `nodes.py`, append a fake id like
   `"refund-policy#gold-tier"` to the plan's citations before validation and
   watch rule 5 catch it, route through repair, and (if the model keeps
   inventing it) end in `REPAIR_EXHAUSTED` — grounding is enforced, not hoped
   for.
4. **Draw the graph RAG changed.** Print the compiled graph as Mermaid:

   ```powershell
   py -c "import graph_lg; print(graph_lg.build().compile().get_graph().draw_mermaid())"
   ```

   Paste it into a Mermaid viewer and find `retrieve_policy`. Adding a node
   took one function in `nodes.py` and one line in `DESTINATIONS`. Now
   misspell that line's target and run `py -m pytest`: `compile()` rejects
   it before any ticket runs.
5. **Find the section nobody retrieves.** In the `--retrieval-only` table,
   T-1005 ($429.99 monitor, chargeback threat) and T-1033 (attorney letter)
   miss `escalation-matrix#when-to-escalate` even at k=6. The query is the
   category plus the ticket text, and the ticket talks about dead pixels and
   a router, not about escalation. Try a change in `retrieve.build_query`,
   for example adding the triage sentiment, and see whether recall moves
   without hurting the other tickets. `retrieve_policy` uses the same
   function, so whatever the eval measures is what the service sends.

   Before you write your own fix, try the two textbook ones. The eval can
   have a model rewrite each ticket first, either into a search question or
   into a made-up policy paragraph (HyDE), and prints what it searched:

   ```powershell
   py evals\rag_evals.py --retrieval-only --query-style rewrite
   py evals\rag_evals.py --retrieval-only --query-style hyde
   ```

   Measured on 12 October (gpt-4.1-mini writing, text-embedding-3-small
   searching, k=6):

   | What the search was given | Recall | Precision | Extra cost |
   | --- | --- | --- | --- |
   | the ticket (the service's query) | 0.72 | 0.19 | none |
   | a search question (`rewrite`) | 0.41 | 0.11 | about $0.005 |
   | a made-up policy paragraph (`hyde`) | 0.56 | 0.15 | about $0.008 |

   Both lost. Neither found T-1005's or T-1033's section. The rewrite turned
   T-1001 into a question about "returns and exchanges", and the refund
   window and the $50 threshold fell out: recall 1.0 became 0.0. HyDE wrote
   T-1035 a price-match policy that doesn't exist and searched with it. Read
   the "what was searched" column: a model that rewrites the ticket decides
   what it is about. The restyled rows score 36 tickets, not 38: the
   provider's content filter refused the two injection tickets. The service
   never restyles its query, and `--query-style` refuses to run without
   `--retrieval-only`.
6. **Judge the judge.** `rag_evals.py` gives the faithfulness judge the
   planner's full inputs: the retrieved sections, the ticket and the order
   record. In `pipeline_case`, cut `judge_contexts` down to the policy
   sections alone and rerun. T-1004's correct plan drops from 1.0 to 0.0,
   because "delivered 5 days ago, $45" is order data, not policy. A metric
   is only as good as the context you hand it, and so is a reference list:
   argue with the `reference_contexts` labels the way Session 9 argues with
   the golden set.
7. **Filter first, then search.** `retrieve()` takes `doc_ids`, a Qdrant
   filter on the stored `doc_id` label that runs *inside* the search call:

   ```powershell
   py retrieve.py --doc warranty "cracked case, want a refund"
   ```

   Six asked, four returned: warranty has four sections, so k is a ceiling,
   not a promise. Now do it the other way: search without the filter and
   drop the non-warranty rows afterwards. You keep 2 of 6 (1 of 6 for
   `--doc promo-terms`), and the other rows' text had already left the store,
   passed through your code, and could have reached a log. The service never
   passes `doc_ids` (one company, no permissions). Where the label is a
   tenant or a reader group, this is the difference between a filter and a
   leak, and the label must come from the authenticated request, never from
   the ticket text. The lab's search box has the same picker and shows both
   tables.
8. **Find the ceiling before you buy a reranker.** A reranker fetches many
   candidates cheaply, has a slower model reread each one next to the
   ticket, and keeps the best few. It can only reorder what the first search
   found, so measure that first:

   ```powershell
   py evals\rag_evals.py --retrieval-only --k 20
   ```

   Measured: recall rises from 0.72 at k=6 to 0.97 at k=20, while precision
   falls from 0.19 to 0.07. T-1005's missing section is at rank 8 and
   T-1033's at rank 12: in the pool, just not in the six. Only T-1050 still
   misses at 20. So a reranker could help here, where rewriting the query
   (Try this 5) could not. Handing all 20 to the planner is not the answer:
   the policy text triples and the conflicting refund windows appear
   together even more often.
