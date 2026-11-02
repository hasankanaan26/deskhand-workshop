"""Ingestion pipeline: policy markdown -> sections -> embeddings -> Qdrant.

Chunking is STRUCTURAL: one chunk per `##` section. Policy documents are short
and their sections are self-contained rules, so splitting mid-section would
only destroy meaning. Each chunk carries metadata (doc_id, section_id,
version, effective) so plans can cite "refund-policy#window" and the
validator can check those citations.

Built on LangChain. What it gives you here:
- `MarkdownHeaderTextSplitter` finds the `##` sections. It also normalises
  whitespace: blank lines go, and paragraph breaks become markdown line
  breaks. Same words, slightly different bytes than the raw file.
- `QdrantVectorStore` creates the collection, stores each chunk as
  {page_content, metadata}, and embeds through whatever `Embeddings` object
  it is handed.

What it does NOT give you, so this file still has to:
- the doc_id / version / effective header comment, and section ids from
  `{#anchor}` or the heading;
- stable point ids, so re-running ingest replaces sections instead of
  duplicating them;
- the cost. The store calls `llm.MeteredEmbeddings`, which goes through
  `llm.embed_texts` and keeps the bill, including one call you didn't ask
  for: the store embeds "dummy_text" to learn the vector size.

Run:  py ingest.py        (re-runnable: the collection is rebuilt each time)
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import MarkdownHeaderTextSplitter

import llm

POLICIES_DIR = Path(__file__).resolve().parents[2] / "shared" / "policies"
# Qdrant "local mode": the index is a folder of files next to this script,
# opened in-process. No database server to install or run in class.
QDRANT_PATH = Path(__file__).resolve().parent / "qdrant_data"
COLLECTION = "policies"  # Qdrant's name for one index, roughly a table

# Every policy file opens with a header comment, for example
#   <!-- doc_id: refund-policy | version: 2.3 | effective: 2026-06-01 -->
# doc_id is the first half of every citation id ("refund-policy#window").
# version and effective ride along on each chunk, so the plan prompt and the
# internal note can say which version of a rule was applied.
_HEADER_RE = re.compile(
    r"<!--\s*doc_id:\s*(?P<doc_id>[\w-]+)\s*\|\s*version:\s*(?P<version>[\w.]+)"
    r"\s*\|\s*effective:\s*(?P<effective>[\d-]+)\s*-->"
)
# An optional explicit id on a heading: `## Approval Thresholds {#approval}`.
# It lets an author reword a heading without breaking the citations that use
# it. The current policy pack has none, so every id there is a slug.
_ANCHOR_RE = re.compile(r"\{#(?P<anchor>[\w-]+)\}\s*$")

# Chunking: cut each document into pieces ("chunks") that are embedded and
# retrieved one by one. Size is the trade-off. A chunk that holds several
# rules gets one blurred vector and drags unrelated text into the prompt. A
# chunk that is too small loses the sentence that qualifies its rule. Splitters
# that cut every N characters add overlap between neighbours so a sentence
# cut at a boundary survives whole in one of them. Cutting at `##` needs no
# overlap: no section is ever cut, and the 27 sections average about 75
# tokens, one topic each. `###` and deeper would stay inside their section.
_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("##", "section")], strip_headers=True
)


def _slugify(heading: str) -> str:
    """Section id from heading text, "Refund Window" -> "refund-window".
    Used when the heading has no {#anchor}."""
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def parse_policy_file(path: Path) -> list[Document]:
    """Split one policy markdown file into section chunks with metadata."""
    text = path.read_text(encoding="utf-8")
    header = _HEADER_RE.search(text)
    if header is None:
        raise ValueError(f"{path.name}: missing doc_id header comment")
    meta = header.groupdict()

    documents: list[Document] = []
    for split in _SPLITTER.split_text(text):
        heading = split.metadata.get("section")
        if heading is None:  # the title and intro before the first ## section
            continue
        # The splitter keeps the `{#anchor}` as part of the heading text.
        anchor = _ANCHOR_RE.search(heading)
        heading_text = _ANCHOR_RE.sub("", heading).strip()
        section_id = anchor.group("anchor") if anchor else _slugify(heading_text)
        documents.append(
            Document(
                # Qdrant point ids must be integers or UUIDs. uuid5 hashes a
                # name into a UUID, so the same section always gets the same id.
                # Stable id per section => re-running ingest updates in place.
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{meta['doc_id']}#{section_id}")),
                # strip_headers moved the heading into metadata; put it back in
                # front of the body. It names the topic ("Window", "Exclusions")
                # in words a query is likely to share, and it gets embedded.
                page_content=f"{heading_text}\n{split.page_content}",
                # Metadata is stored next to the vector but never embedded.
                # retrieve.py returns it with each hit; rules.py checks every
                # citation against doc_id#section_id.
                metadata={
                    "doc_id": meta["doc_id"],
                    "section_id": section_id,
                    "version": meta["version"],
                    "effective": meta["effective"],
                },
            )
        )
    return documents


def load_policy_pack(policies_dir: Path = POLICIES_DIR) -> list[Document]:
    """Every section of every policy file, in a stable order."""
    return [
        doc
        for path in sorted(policies_dir.glob("*.md"))
        for doc in parse_policy_file(path)
    ]


def ingest(
    policies_dir: Path = POLICIES_DIR, qdrant_path: Path = QDRANT_PATH
) -> tuple[int, llm.MeteredEmbeddings]:
    """Parse, embed, and store every policy section.

    Returns the section count and the meter, whose `.usage` is the bill and
    `.calls` the number of embedding calls the store made.
    """
    documents = load_policy_pack(policies_dir)
    if not documents:
        raise SystemExit(f"no policy files found in {policies_dir}")

    # An embedding is a list of numbers (1,536 of them for the default
    # text-embedding-3-small) that places a text so that texts with similar
    # meaning land close together. from_documents embeds every chunk (in
    # batches of 64, so the 27 sections go in one call), creates the
    # collection with LangChain's default distance, cosine, and stores each
    # vector with its text and metadata. Queries must be embedded by the same
    # model, or their vectors cannot be compared with these.
    meter = llm.MeteredEmbeddings()
    store = QdrantVectorStore.from_documents(
        documents,
        meter,
        path=str(qdrant_path),
        collection_name=COLLECTION,
        force_recreate=True,  # vector size can change with the embedding model
    )
    # Local mode locks the folder for one client at a time; free it so
    # retrieve.py (or the next ingest) can open it.
    store.client.close()
    return len(documents), meter


if __name__ == "__main__":
    count, meter = ingest()
    print(
        f"ingested {count} policy sections into {QDRANT_PATH}\n"
        f"{meter.calls} embedding calls, {meter.usage.prompt_tokens} tokens, "
        f"${meter.usage.cost_usd:.6f}"
    )
