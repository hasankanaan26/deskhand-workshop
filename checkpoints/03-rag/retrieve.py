"""Retrieval: embed the query, return the top-k policy sections with scores.

Chunks come back as dicts {doc_id, section_id, text, score, version,
effective} — the shape stored in TicketResolutionState.policy_chunks.

The index is LangChain's `QdrantVectorStore`, but the query is embedded here,
through `llm.embed_texts`, and searched with
`similarity_search_with_score_by_vector`. That way each call's cost comes back
with its vector, and the store never makes a model call of its own.

Spot-check from the terminal:  py retrieve.py "cracked case refund"
Only one document:             py retrieve.py --doc warranty "cracked case refund"

`doc_ids` is a filter INSIDE the search (Qdrant's `filter=`), not rows dropped
afterwards: ask for k=6 from a 4-section document and you get 4, all of them
legitimate, and the other documents' text never leaves the store. The service
never passes it (one company, no permissions); Session 8 shows what it is for.
"""
from __future__ import annotations

import argparse
import atexit
import sys
from pathlib import Path

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models

import llm
from ingest import COLLECTION, QDRANT_PATH

# k: how many sections the planner is shown. A bigger k finds more of the
# sections a ticket needs (recall rises) and brings more it doesn't need with
# them (precision falls, the prompt grows). README "Try this" 2 and 8 put
# numbers on it: at k=20, recall 0.97 and precision 0.07.
TOP_K = 6

# One store per process, opened on first use. Local mode locks the folder to
# one client, and opening it again for every ticket would be wasted work.
_store: QdrantVectorStore | None = None


def build_query(category: str | None, ticket_text: str) -> str:
    """The retrieval query for a ticket: its category plus the ticket's start.

    `retrieve_policy` and `evals/rag_evals.py` both call this, so the eval
    measures the query the service really sends.
    """
    # One vector stands for the whole query, so a long thread blurs it. The
    # first 500 characters keep the opening, where the problem is usually
    # stated, and the category prefix adds a topic word in policy language.
    return f"{category or 'support ticket'}: {ticket_text[:500]}"


def _open_store(client: QdrantClient) -> QdrantVectorStore:
    if not client.collection_exists(COLLECTION):
        raise RuntimeError(f"no '{COLLECTION}' collection — run `py ingest.py` first")
    return QdrantVectorStore(
        client=client,
        collection_name=COLLECTION,
        # The constructor insists on an Embeddings object; retrieve() never
        # lets the store use it.
        embedding=llm.MeteredEmbeddings(),
        # Validation would embed "dummy_text" to check the vector size: a
        # paid call every time the store opens.
        validate_collection_config=False,
    )


def _get_store(qdrant_path: Path = QDRANT_PATH) -> QdrantVectorStore:
    global _store
    if _store is None:
        if not qdrant_path.exists():
            raise RuntimeError(
                f"no Qdrant index at {qdrant_path} — run `py ingest.py` first"
            )
        client = QdrantClient(path=str(qdrant_path))
        # Close before interpreter teardown, or QdrantClient.__del__ prints a
        # harmless but alarming traceback on exit.
        atexit.register(client.close)
        _store = _open_store(client)
    return _store


def _chunk(doc: Document, score: float) -> dict:
    """One hit in the shape the pipeline passes around. doc_id#section_id is
    the id the planner must cite and rules.py checks; version goes into the
    plan prompt and the internal note."""
    meta = doc.metadata
    if "doc_id" not in meta:
        raise RuntimeError(
            "the index was built by an older ingest.py — re-run `py ingest.py`"
        )
    return {
        "doc_id": meta["doc_id"],
        "section_id": meta["section_id"],
        "version": meta["version"],
        "effective": meta["effective"],
        "text": doc.page_content,
        # Cosine similarity, higher is closer. Read it as a ranking within one
        # query, not as a confidence: the same number can top one query's
        # list and sit near the bottom of another's.
        "score": round(score, 4),
    }


def _doc_filter(doc_ids: list[str] | None) -> models.Filter | None:
    """A Qdrant filter on the stored `doc_id` label (LangChain keeps metadata
    under the `metadata` payload key). None means every document."""
    if not doc_ids:
        return None
    return models.Filter(must=[models.FieldCondition(
        key="metadata.doc_id", match=models.MatchAny(any=list(doc_ids))
    )])


def retrieve(
    query: str,
    k: int | None = None,
    client: QdrantClient | None = None,
    doc_ids: list[str] | None = None,
) -> tuple[list[dict], llm.Usage]:
    """Top-k policy chunks for a query, plus the embedding Usage (for cost).

    `k` defaults to TOP_K as it is when called, so `rag_evals.py --k` can
    change it for the whole pipeline. `doc_ids` restricts the search to those
    documents, inside the store's own search call (see the module docstring).
    """
    k = TOP_K if k is None else k
    # Same embedding model as ingest, so the query vector and the section
    # vectors can be compared.
    vectors, usage = llm.embed_texts([query])
    store = _open_store(client) if client is not None else _get_store()
    # Similarity search: score every stored section against the query vector
    # and return the k closest, best first. It ranks by meaning, not by
    # authority. A refund-window question lands near refund-policy#window
    # (14 days) and returns-and-exchanges#returns-window (30 days) alike, so
    # the planted contradiction comes back together. Choosing between them is
    # left to the planner's prompt, and README "Try this" 1 shows that a
    # prompt alone does not make the choice safe.
    hits = store.similarity_search_with_score_by_vector(
        vectors[0], k=k, filter=_doc_filter(doc_ids)
    )
    return [_chunk(doc, score) for doc, score in hits], usage


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="search the policy index")
    parser.add_argument("query", nargs="*", help="the search text (bare: no category prefix)")
    parser.add_argument("--k", type=int, default=None, help=f"sections to return (default {TOP_K})")
    parser.add_argument("--doc", action="append", default=None, metavar="DOC_ID",
                        help="only this document, e.g. warranty (repeatable)")
    args = parser.parse_args()
    question = " ".join(args.query) or "refund window for delivered items"
    chunks, usage = retrieve(question, k=args.k, doc_ids=args.doc)
    print(f"query: {question!r} (embedding: {usage.prompt_tokens} tokens, "
          f"${usage.cost_usd:.8f})"
          + (f"  only: {', '.join(args.doc)}" if args.doc else "") + "\n")
    for chunk in chunks:
        print(f"{chunk['score']:.4f}  {chunk['doc_id']}#{chunk['section_id']}")
        print(f"        {chunk['text'][:100].replace(chr(10), ' ')}...")
    if args.doc and len(chunks) < (args.k or TOP_K):
        print(f"\n{len(chunks)} of {args.k or TOP_K} asked: the filter ran inside the search, "
              "so only that document's sections could come back.")
