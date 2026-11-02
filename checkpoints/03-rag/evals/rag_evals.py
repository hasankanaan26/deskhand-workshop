"""rag_evals.py — measure the retrieval layer with RAGAS (Session 9).

eval_stub.py grades the plan's DECISIONS. This grades the RAG layer under
them, per golden ticket:

  context precision   of the sections retrieved, the share that are on the
                      ticket's `reference_contexts` list (IDBasedContextPrecision)
  context recall      of the sections on that list, the share that were
                      retrieved (IDBasedContextRecall)
  faithfulness        the share of claims in the plan's rationale that the
                      planner's inputs support: the retrieved sections, the
                      ticket and the order record (Faithfulness, an LLM
                      judge; full mode only). Against the sections alone a
                      correct plan scores near 0, because "delivered 5 days
                      ago, $45" is order data, not policy.

The two context metrics compare section ids, so they need no LLM: they are
deterministic and nearly free. Tickets whose `reference_contexts` is empty
(nothing to retrieve yet) are left out of them.

Modes:
  py evals\\rag_evals.py --retrieval-only   query = golden category + raw ticket
                                           text; embeddings only, no mock API
  py evals\\rag_evals.py                    the real planning pipeline
                                           (eval_stub.plan_ticket; needs the mock
                                           API on :9000), plus faithfulness
  --k N                                    retrieve N sections, not retrieve.TOP_K
  --query-style rewrite | hyde             retrieval only: a model rewrites the
                                           ticket into a search question (rewrite)
                                           or a made-up policy paragraph (hyde)
                                           before build_query; the generated text
                                           is printed, so you can see what was
                                           searched. The service never does this:
                                           measure first, add it only if the
                                           numbers say so.

What RAGAS gives you: the metric definitions, the faithfulness judge's prompts
(split the rationale into claims, check each claim against the context), and
one `evaluate()` call over a dataset. What it does NOT give you: the labels.
`reference_contexts` in the golden set is your judgement, and a metric is
only as good as the list it compares against. It also doesn't price its own
LLM calls; that is `llm.usage_for` again.

Needs: provider credentials in the repo .env and a built index (`py ingest.py`).
"""
from __future__ import annotations

import os

# RAGAS sends usage telemetry unless told not to. Set before it is imported.
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402
import warnings  # noqa: E402
from pathlib import Path  # noqa: E402

from langchain_core.callbacks import UsageMetadataCallbackHandler  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import errors  # noqa: E402
import llm  # noqa: E402
import retrieve  # noqa: E402
from eval_stub import load_ticket_text, plan_ticket  # noqa: E402

# RAGAS 0.4 is midway through a rewrite: metrics are moving to
# `ragas.metrics.collections` and `evaluate()` to an `@experiment` decorator.
# The new metrics have no ID-based context metrics yet, and their
# Faithfulness only accepts a RAGAS-built OpenAI client, which would bypass
# llm.py. So this script uses the older metric classes, LangChain wrapper and
# `evaluate()`, and hides their deprecation warnings.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from ragas import EvaluationDataset, SingleTurnSample, evaluate  # noqa: E402
    from ragas import RunConfig  # noqa: E402
    from ragas.llms import LangchainLLMWrapper  # noqa: E402
    from ragas.metrics import (  # noqa: E402
        Faithfulness,
        IDBasedContextPrecision,
        IDBasedContextRecall,
    )

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_PATH = EVALS_DIR / "rag_results.json"
GOLDEN_PATH = EVALS_DIR.parent / "golden" / "golden_tickets.json"
TICKETS_PATH = EVALS_DIR.parents[2] / "shared" / "data" / "tickets.json"

# Both context metrics treat the retrieved list as a set: rank doesn't count,
# so a needed section at rank 1 and at rank 6 score the same. Because the
# list is cut at k, they are precision@k and recall@k. A rank-aware metric
# such as MRR (1 / the rank of the first relevant hit) would tell those two
# apart; nothing here computes one.
#
# Low precision at k=6 is mostly arithmetic. Most labelled tickets need one
# or two sections, so six retrieved caps precision near 0.25 even when
# retrieval is perfect. Recall is the number to watch: a missed section is
# a rule the planner never saw.
PRECISION = "id_based_context_precision"
RECALL = "id_based_context_recall"
FAITHFULNESS = "faithfulness"

# RAGAS's defaults run 16 scoring jobs at once with a 180 s budget each. Each
# faithfulness job is several LLM calls, so on a small Azure deployment most
# of them hit rate limits and time out. Four at a time finishes.
JUDGE_RUN_CONFIG = RunConfig(max_workers=4, timeout=300, max_retries=5)


def _evaluate(dataset: EvaluationDataset, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return evaluate(dataset, show_progress=False, **kwargs)


def _section_id(chunk: dict) -> str:
    """The citation id, doc_id#section_id: the string the planner cites,
    rules.py checks and the golden labels use, so all three agree."""
    return f"{chunk['doc_id']}#{chunk['section_id']}"


# ---------------------------------------------------------------------------
# Query styles (retrieval only). The service sends `build_query`'s plain
# query. These two "search upgrades" exist here so a number decides whether
# either is worth adding: run all three, compare recall, read the queries.
# ---------------------------------------------------------------------------

QUERY_STYLES = ("plain", "rewrite", "hyde")

# Query rewriting: a model translates the customer's words into the
# handbook's words before the search. The risk is that the model decides
# what the ticket is about. README "Try this" 5: it turned T-1001 into a
# returns question, and the refund window fell out of the results.
REWRITE_PROMPT = (
    "You turn a customer support ticket for Nimbus Electronics (an online "
    "electronics store) into ONE short search question for the company's policy "
    "handbook: refunds, returns and exchanges, warranty, shipping, promotions, "
    "and when to escalate to a human. Name the policy topic the ticket is really "
    "about, in the handbook's formal words, even if the customer never uses them. "
    "Reply with the question only."
)

# HyDE (Hypothetical Document Embeddings): search with a made-up answer
# instead of the question. A fake policy paragraph is written like the real
# ones, so its vector can land nearer them than a customer complaint does.
# Its facts may be wrong; only its neighbourhood matters. README "Try this" 5
# records the cost: it searched for a price-match policy that doesn't exist.
HYDE_PROMPT = (
    "Write the short policy paragraph (about 60 words) that a Nimbus Electronics "
    "policy handbook would contain to settle this customer ticket, in the formal "
    "style of an internal policy document. Invent specifics freely: only the "
    "style and the topic matter, because the text is used to search the real "
    "handbook, never shown to anyone. Reply with the paragraph only."
)


def restyle_query(style: str, ticket_text: str) -> tuple[str, llm.Usage]:
    """What the search is given in place of the raw ticket text.

    `plain` is the ticket text itself (what the service sends). `rewrite` and
    `hyde` make one cheap model call each; the result replaces the ticket
    text in `build_query`, so the category prefix stays.
    """
    if style == "plain":
        return ticket_text, llm.usage_for(llm.DEFAULT_MODEL, 0, 0)
    if style not in QUERY_STYLES:
        raise ValueError(f"unknown query style {style!r}; one of {QUERY_STYLES}")
    system = REWRITE_PROMPT if style == "rewrite" else HYDE_PROMPT
    text, usage = llm.text_completion(
        [{"role": "system", "content": system},
         {"role": "user", "content": ticket_text[:2000]}],  # caps cost on long threads
        max_output_tokens=160,  # a ~60-word paragraph fits with room to spare
    )
    # One line of single spaces, so the generated query prints in one cell.
    return " ".join(text.split()), usage


# ---------------------------------------------------------------------------
# One case per golden ticket
# ---------------------------------------------------------------------------

def retrieval_case(entry: dict, ticket_text: str, query_style: str = "plain") -> dict:
    """Retrieval only: the golden category stands in for triage."""
    # With triage out of the loop, a misclassified ticket can't drag the
    # retrieval score down: this isolates the retriever. The text is
    # load_ticket_text's, not normalize_ticket's. They can differ in
    # whitespace, and on over-length tickets, where the service keeps only
    # the newest part.
    search_text, rewrite_usage = restyle_query(query_style, ticket_text)
    query = retrieve.build_query(entry["expected_category"], search_text)
    chunks, usage = retrieve.retrieve(query)
    return {
        "ticket_id": entry["ticket_id"],
        "query": query,
        "query_style": query_style,
        "retrieved": [_section_id(c) for c in chunks],
        "retrieved_texts": [c["text"] for c in chunks],
        "reference": entry["reference_contexts"],
        "rationale": None,
        "judge_contexts": [],
        "cost_usd": usage.cost_usd + rewrite_usage.cost_usd,
        "query_cost_usd": rewrite_usage.cost_usd,
    }


def pipeline_case(entry: dict, ticket_text: str) -> dict:
    """The real pipeline up to validation, as eval_stub.py runs it."""
    state = plan_ticket(entry["ticket_id"], ticket_text)
    plan = state.resolution_plan
    return {
        "ticket_id": entry["ticket_id"],
        # The judge's user_input. The search query itself was built inside
        # retrieve_policy, from triage's category.
        "query": ticket_text,
        "retrieved": [_section_id(c) for c in state.policy_chunks],
        "retrieved_texts": [c["text"] for c in state.policy_chunks],
        "reference": entry["reference_contexts"],
        "rationale": plan.rationale if plan else None,
        # Everything the planner was shown, so the judge can tell an invented
        # claim from one that came from the ticket or the order record.
        "judge_contexts": [c["text"] for c in state.policy_chunks] + [
            f"Ticket:\n{state.normalized_ticket or ticket_text}",
            f"Order record:\n{json.dumps(state.order_data)}",
        ],
        "cost_usd": state.cost_usd,  # the run's recorded cost, query embedding included
    }


# ---------------------------------------------------------------------------
# RAGAS
# ---------------------------------------------------------------------------

def score_context(cases: list[dict]) -> dict[str, dict[str, float]]:
    """ID-based precision and recall per ticket. No LLM involved."""
    # No labels, nothing to compare against. An empty retrieval is left out
    # too: precision would divide by zero.
    scored = [c for c in cases if c["reference"] and c["retrieved"]]
    if not scored:
        return {}
    dataset = EvaluationDataset(samples=[
        SingleTurnSample(
            user_input=c["query"],
            retrieved_context_ids=c["retrieved"],
            reference_context_ids=c["reference"],
        )
        for c in scored
    ])
    result = _evaluate(dataset, metrics=[IDBasedContextPrecision(), IDBasedContextRecall()])
    # result.scores is one dict per sample, in dataset order.
    return {c["ticket_id"]: row for c, row in zip(scored, result.scores)}


def ragas_llm() -> LangchainLLMWrapper:
    """The judge for faithfulness: our chat model, behind RAGAS's wrapper.

    A fresh model, because the wrapper sets `.temperature` and `.n` on
    whatever it wraps. On Claude it must not touch temperature at all
    (sampling parameters are a 400), and `bypass_n` sends one request per
    completion instead of asking for `n`.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return LangchainLLMWrapper(
            llm.fresh_chat_model(max_output_tokens=4096),
            bypass_temperature=llm.PROVIDER == "anthropic",
            bypass_n=True,
        )


def score_faithfulness(cases: list[dict]) -> tuple[dict[str, dict[str, float]], llm.Usage]:
    """LLM-judged faithfulness of each rationale to the planner's inputs."""
    # LLM-as-judge: a second model splits the rationale into claims and checks
    # each against the contexts; the score is supported claims / all claims.
    # It costs real calls and can vary between runs, so only full mode runs it.
    scored = [c for c in cases if c["rationale"] and c["judge_contexts"]]
    if not scored:
        return {}, llm.usage_for(llm.DEFAULT_MODEL, 0, 0)
    dataset = EvaluationDataset(samples=[
        SingleTurnSample(
            user_input=c["query"],
            response=c["rationale"],
            retrieved_contexts=c["judge_contexts"],
        )
        for c in scored
    ])
    meter = UsageMetadataCallbackHandler()
    result = _evaluate(dataset, metrics=[Faithfulness()], llm=ragas_llm(),
                       callbacks=[meter], run_config=JUDGE_RUN_CONFIG)
    return {c["ticket_id"]: row for c, row in zip(scored, result.scores)}, judge_usage(meter)


def judge_usage(meter: UsageMetadataCallbackHandler) -> llm.Usage:
    """RAGAS doesn't price its own calls. The callback saw every reply's
    token counts, keyed by the model name the provider returned."""
    prompt = completion = 0
    cost = 0.0
    for model, counts in meter.usage_metadata.items():
        usage = llm.usage_for(model, counts["input_tokens"], counts["output_tokens"])
        prompt += usage.prompt_tokens
        completion += usage.completion_tokens
        cost += usage.cost_usd
    return llm.Usage(prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _number(value) -> float | None:
    """RAGAS reports 'could not score' as NaN; JSON and averages want None."""
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def merge(cases: list[dict], *score_maps: dict[str, dict[str, float]]) -> list[dict]:
    """One row per ticket with every metric that scored it. `missed` lists
    the labelled sections retrieval did not return: read it first when
    recall drops."""
    rows = []
    for case in cases:
        scores: dict[str, float | None] = {}
        for score_map in score_maps:
            for name, value in score_map.get(case["ticket_id"], {}).items():
                scores[name] = _number(value)
        rows.append({
            "ticket_id": case["ticket_id"],
            "query": case["query"],
            "retrieved": case["retrieved"],
            "reference": case["reference"],
            "missed": [r for r in case["reference"] if r not in case["retrieved"]],
            "rationale": case["rationale"],
            "scores": scores,
        })
    return rows


def summarize(rows: list[dict]) -> dict[str, dict[str, float | int]]:
    """Mean of each metric over the tickets it could score, and that count."""
    # n differs per metric: the context metrics skip unlabelled tickets,
    # faithfulness skips plans without a rationale. Compare means at equal n.
    summary = {}
    for name in (PRECISION, RECALL, FAITHFULNESS):
        values = [r["scores"][name] for r in rows if r["scores"].get(name) is not None]
        if values:
            summary[name] = {"mean": round(sum(values) / len(values), 3), "n": len(values)}
    return summary


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _printable(text: str, limit: int = 90, encoding: str | None = None) -> str:
    """A model-written query, cut for the table and safe for the console.

    On Windows a redirected stdout is cp1252, and a rewritten query can hold
    a character it cannot encode; `rich` would then crash the whole report.
    """
    cut = text[:limit] + ("..." if len(text) > limit else "")
    encoding = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    return cut.encode(encoding, "replace").decode(encoding, "replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retrieval-only", action="store_true",
                        help="skip triage and planning; no mock API, no judge")
    parser.add_argument("--k", type=int, default=None,
                        help=f"sections to retrieve (default retrieve.TOP_K = {retrieve.TOP_K})")
    parser.add_argument("--query-style", choices=QUERY_STYLES, default="plain",
                        help="retrieval only: what the search is given instead of the raw "
                             "ticket text (rewrite: a search question; hyde: a made-up "
                             "policy paragraph). One cheap model call per ticket.")
    args = parser.parse_args(argv)
    if args.k is not None:
        # retrieve() reads TOP_K at call time, so this reaches the service's
        # retrieve_policy node too in full mode.
        retrieve.TOP_K = args.k
    if args.query_style != "plain" and not args.retrieval_only:
        parser.error("--query-style needs --retrieval-only: the service always sends "
                     "build_query's plain query, so the full pipeline cannot be restyled")
    restyled = args.query_style != "plain"

    console = Console()
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["golden_tickets"]
    tickets = json.loads(TICKETS_PATH.read_text(encoding="utf-8"))["tickets"]

    cases: list[dict] = []
    skipped: dict[str, str] = {}
    for entry in golden:
        ticket_text = load_ticket_text(entry["ticket_id"], tickets)
        if ticket_text is None:
            console.print(f"[yellow]skipping {entry['ticket_id']}: not in dataset[/]")
            continue
        try:
            if args.retrieval_only:
                case = retrieval_case(entry, ticket_text, query_style=args.query_style)
            else:
                case = pipeline_case(entry, ticket_text)
        except errors.DeskhandError as exc:
            # In checkpoint 04 the input guard stops injection tickets before
            # retrieval runs. Nothing was retrieved, so there is nothing to score.
            skipped[entry["ticket_id"]] = exc.internal_error_code
            console.print(f"[yellow]skipping {entry['ticket_id']}: "
                          f"{exc.internal_error_code}[/]")
            continue
        except Exception as exc:  # a crash is a skipped ticket, not a crashed eval
            # Azure's content filter, for one, rejects injection tickets with
            # a 400 that llm.py has no taxonomy code for.
            skipped[entry["ticket_id"]] = f"{type(exc).__name__}: {str(exc)[:120]}"
            console.print(f"[red]skipping {entry['ticket_id']}: "
                          f"{skipped[entry['ticket_id']]}[/]")
            continue
        cases.append(case)

    context_scores = score_context(cases)
    faith_scores, judge = ({}, llm.usage_for(llm.DEFAULT_MODEL, 0, 0))
    if not args.retrieval_only:
        faith_scores, judge = score_faithfulness(cases)

    rows = merge(cases, context_scores, faith_scores)
    summary = summarize(rows)
    pipeline_cost = sum(c["cost_usd"] for c in cases)
    rewrite_cost = sum(c.get("query_cost_usd", 0.0) for c in cases)

    table = Table(title=f"RAG metrics, k={retrieve.TOP_K}"
                  + (" (retrieval only)" if args.retrieval_only else "")
                  + (f", query style: {args.query_style}" if restyled else ""))
    columns = ["ticket", "precision", "recall", "faithful", "missed sections"]
    if restyled:
        columns.append("what was searched")  # log the generated queries
    for column in columns:
        table.add_column(column)
    for row in rows:
        s = row["scores"]
        cells = [row["ticket_id"], _fmt(s.get(PRECISION)), _fmt(s.get(RECALL)),
                 _fmt(s.get(FAITHFULNESS)), ", ".join(row["missed"]) or ""]
        if restyled:
            cells.append(_printable(row["query"]))
        table.add_row(*cells)
    console.print(table)
    for name, stats in summary.items():
        console.print(f"{name}: {stats['mean']:.3f}  (n={stats['n']})")
    console.print(
        f"cost: pipeline ${pipeline_cost:.6f}"
        + (f" (of which query rewrites ${rewrite_cost:.6f})" if restyled else "")
        + f" + judge ${judge.cost_usd:.6f} = ${pipeline_cost + judge.cost_usd:.6f}"
    )

    RESULTS_PATH.write_text(json.dumps({
        "k": retrieve.TOP_K,
        "mode": "retrieval_only" if args.retrieval_only else "pipeline",
        "query_style": args.query_style,
        "summary": summary,
        "cost_usd": {"pipeline": round(pipeline_cost, 6), "judge": round(judge.cost_usd, 6)},
        "per_ticket": rows,
        "skipped": skipped,
    }, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
