"""Ingestion for the support knowledge base: shared/kb -> chunks -> Qdrant "kb".

The policy pack (ingest.py) is six tidy Markdown files that all customers may
read. The knowledge base is what a support desk really has: manuals, internal
agent macros, saved carrier pages, ops emails and a ticket export, some for
customers, some for staff only, some already out of date. So this pipeline
does four things ingest.py never had to:

1. Parse per format (kb_parsers.py): Markdown, HTML, email, CSV.
2. Join the manifest. shared/kb/manifest.json stands in for the source
   system's register (SharePoint permissions, a document's status): who may
   read each file (`allowed_groups`) and whether it is `current` or
   `superseded`. The text carries none of that, and a file the manifest does
   not list is NOT indexed. No entry means nobody said who may read it, and
   "everyone" is the wrong guess.
3. Chunk and label. Every chunk carries doc_id, source_path, source_type,
   last_modified, content_hash, allowed_groups, status and effective_from as
   its Qdrant payload, so kb_search.py can filter on them INSIDE the search
   and kb_sync.py can tell which files changed.
4. Embed twice. A dense vector (the embedding model, through llm.embed_texts,
   as for the policy pack) and a sparse BM25 vector (plain Python below, no
   model and no API), so kb_search.py can run keyword and meaning search in
   one query.

The collection lives in the same local Qdrant folder as the policy pack
(qdrant_data/), next to "policies". Local mode still lets one process open
that folder at a time: stop the service before running this.

This file uses qdrant_client directly, not LangChain's QdrantVectorStore as
ingest.py does. The store keeps one unnamed vector per point and nests the
metadata under "metadata."; here a point has two named vectors (dense and
bm25) and a flat payload, so the access filter in kb_search.py reads
`allowed_groups`, the field the manifest wrote.

Run:  py kb_ingest.py        (rebuilds the collection; kb_sync.py updates it)
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from langchain_text_splitters import MarkdownHeaderTextSplitter
from qdrant_client import QdrantClient, models

import llm
from ingest import QDRANT_PATH
from kb_parsers import SOURCE_TYPES, parse_file

KB_DIR = Path(__file__).resolve().parents[2] / "shared" / "kb"
MANIFEST_NAME = "manifest.json"
COLLECTION = "kb"
DENSE = "dense"  # the named vector from the embedding model
SPARSE = "bm25"  # the named sparse vector: BM25 keyword weights

# The reader groups the manifest may grant. A typo ("customers") would
# otherwise grant nobody, silently: a document no one can ever retrieve.
KNOWN_GROUPS = ("customer", "support-agent")
STATUSES = ("current", "superseded")

# A section longer than this is cut at paragraph breaks. About 400 tokens at
# ~4 characters a token: one topic, small enough that its vector still means
# one thing. Most manual sections are far shorter and stay whole.
MAX_CHUNK_CHARS = 1600
EMBED_BATCH = 128  # chunks per embedding request

# `#` gives the article title, `##` the section. The same structural cut as
# the policy pack (ingest.py): help articles are written as one topic per
# `##` ("Pairing", "Factory reset"), so a section is a natural chunk. HTML comes
# out of kb_parsers with its h1/h2/h3 as `#`/`##`, so pages split the same way.
_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("#", "title"), ("##", "section")], strip_headers=True
)

# ---------------------------------------------------------------------------
# BM25 sparse vectors, in plain Python
# ---------------------------------------------------------------------------

# BM25 is the classic keyword score: a query word counts more the rarer it is
# in the corpus (IDF), and more when it recurs in a chunk, with diminishing
# returns (term-frequency saturation), relative to the chunk's length. It
# catches the exact tokens a dense vector blurs: "2608", "TKL", "EVL".
#
# Split between the two sides, the way Qdrant's own BM25 works:
# - here, each chunk becomes a sparse vector {token id: saturated tf weight};
# - Qdrant multiplies in the IDF at query time (Modifier.IDF on the sparse
#   vector config), from the points it holds, so adding or deleting files
#   (kb_sync.py) updates every word's rarity with no re-embedding.
# No model, no download, no API: a tokeniser and one formula.
BM25_K1 = 1.2  # how fast repeats stop counting: tf=1 -> 1.0, tf=3 -> ~1.6, never above 2.2
BM25_B = 0.75  # how much a long chunk is discounted (0 = not at all, 1 = fully)
# The average chunk length in tokens. Measured on shared/kb on 29 October 2026
# (181 chunks). A constant, not recomputed per ingest: kb_sync.py adds chunks
# one file at a time, and every stored weight must use the same yardstick. A
# stale value only shifts the length discount a little.
BM25_AVG_LEN = 43

# Words too common to say anything about a chunk. Short on purpose: "not",
# "no" and numbers stay, they carry meaning in support text ("no light").
_STOPWORDS = frozenset(
    "a an and are as at be but by can do for from has have how i if in is it its "
    "me my of on or our so than that the their them then there these they this "
    "to was we were what when where which who will with you your".split()
)


def bm25_tokens(text: str) -> list[str]:
    """Lowercase words and numbers, stopwords out, a plural "s" trimmed.
    "Bulbs" and "bulb" should match; "2.4" and "65w" stay whole tokens."""
    tokens = []
    for token in re.findall(r"[a-z0-9]+(?:[.'][a-z0-9]+)*", text.lower()):
        if token in _STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        tokens.append(token)
    return tokens


def _token_id(token: str) -> int:
    """A token's sparse index: crc32 of the word. Stable in every process
    (Python's hash() is salted per run) and no vocabulary file to keep in
    step. Two words sharing a crc32 is possible and harmless at this size."""
    return zlib.crc32(token.encode("utf-8"))


def sparse_embed_documents(texts: list[str]) -> list[models.SparseVector]:
    """One BM25 document vector per chunk: saturated, length-normalised tf."""
    vectors = []
    for text in texts:
        tokens = bm25_tokens(text)
        counts = Counter(_token_id(t) for t in tokens)
        norm = BM25_K1 * (1 - BM25_B + BM25_B * len(tokens) / BM25_AVG_LEN)
        vectors.append(models.SparseVector(
            indices=list(counts),
            values=[tf * (BM25_K1 + 1) / (tf + norm) for tf in counts.values()],
        ))
    return vectors


def sparse_embed_query(text: str) -> models.SparseVector:
    """A query vector: every distinct word once, weight 1. Qdrant's dot
    product then sums IDF x tf-weight over the words the chunk shares: BM25."""
    ids = sorted({_token_id(t) for t in bm25_tokens(text)})
    return models.SparseVector(indices=ids, values=[1.0] * len(ids))


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

def load_manifest(kb_dir: Path = KB_DIR) -> dict[str, dict]:
    """The document register, keyed by path relative to kb_dir ("manuals/x.md").

    Checked hard: a bad entry is an ingest error, never a guess. An access
    rule that fails to parse must not turn into "visible to everyone".
    """
    path = kb_dir / MANIFEST_NAME
    if not path.exists():
        raise SystemExit(f"no {MANIFEST_NAME} in {kb_dir}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw["documents"] if isinstance(raw, dict) else raw
    by_path: dict[str, dict] = {}
    doc_ids: set[str] = set()
    for entry in entries:
        where = entry.get("doc_id") or entry.get("path") or "?"
        for key in ("doc_id", "path", "allowed_groups", "status"):
            if key not in entry:
                raise ValueError(f"manifest entry {where}: missing {key!r}")
        groups = entry["allowed_groups"]
        if not groups or not set(groups) <= set(KNOWN_GROUPS):
            raise ValueError(f"manifest entry {where}: allowed_groups {groups} "
                             f"must be a non-empty list from {list(KNOWN_GROUPS)}")
        if entry["status"] not in STATUSES:
            raise ValueError(f"manifest entry {where}: status {entry['status']!r} "
                             f"must be one of {list(STATUSES)}")
        if entry["doc_id"] in doc_ids:
            raise ValueError(f"manifest: doc_id {entry['doc_id']!r} appears twice")
        doc_ids.add(entry["doc_id"])
        by_path[Path(entry["path"]).as_posix()] = entry
    return by_path


# Files at the root of kb_dir that describe the corpus instead of being part
# of it. (manifest.json and eval_questions.json have no parser anyway.)
CORPUS_IGNORE = frozenset({"README.md"})


def corpus_files(kb_dir: Path = KB_DIR) -> list[str]:
    """Every parseable file under kb_dir, as manifest-style relative paths."""
    return sorted(
        rel
        for p in kb_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SOURCE_TYPES
        and (rel := p.relative_to(kb_dir).as_posix()) not in CORPUS_IGNORE
    )


def file_hash(path: Path) -> str:
    """sha256 of the file's bytes: the same bytes, the same hash, on any run.
    kb_sync.py compares it with the stored one to find changed files."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "part"


def _split_long(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Cut at paragraph breaks into pieces of at most ~limit characters. A
    single paragraph longer than the limit stays whole: cutting a sentence
    in half is worse than one long chunk."""
    if len(text) <= limit:
        return [text]
    pieces, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text):
        if current and len(current) + len(paragraph) + 2 > limit:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def chunk_records(records: list[dict], source_type: str) -> list[dict]:
    """Records from a parser -> chunks {chunk_id, section, text, extra}.

    Markdown and HTML split at `##`; the text before the first `##` (a
    manual's model number and summary) becomes an "overview" chunk instead
    of being dropped. An email is one chunk; a CSV row is one chunk, with
    its row id as the chunk id. Anything too long is cut at paragraphs.
    """
    chunks: list[dict] = []
    used: set[str] = set()

    def add(chunk_id: str, section: str | None, text: str, extra: dict) -> None:
        pieces = _split_long(text)
        for n, piece in enumerate(pieces, start=1):
            cid = chunk_id if len(pieces) == 1 else f"{chunk_id}-{n}"
            base, k = cid, 2
            while cid in used:  # two sections with the same heading
                cid, k = f"{base}-{k}", k + 1
            used.add(cid)
            chunks.append({"chunk_id": cid, "section": section, "text": piece, "extra": extra})

    for record in records:
        meta = record["metadata"]
        if source_type in {"markdown", "html"}:
            for split in _SPLITTER.split_text(record["text"]):
                section = split.metadata.get("section")
                body = split.page_content.strip()
                if not body:
                    continue
                add(_slugify(section) if section else "overview", section or "Overview", body, {})
        elif source_type == "csv":
            row_id = meta.get("row_id") or f"row-{meta['row']}"
            add(_slugify(row_id), row_id, record["text"], {"row_id": meta.get("row_id")})
        else:  # email
            extra = {k: meta[k] for k in ("from", "sent_at") if meta.get(k)}
            add("message", None, record["text"], extra)
    return chunks


def file_chunks(kb_dir: Path, rel_path: str, entry: dict) -> list[dict]:
    """Parse, chunk and label one file. Each chunk: {id, text, payload}."""
    path = kb_dir / rel_path
    source_type = SOURCE_TYPES[path.suffix.lower()]
    records = parse_file(path)
    # The manifest's title wins over the file's: the register is the
    # authority on what a document is called, as on everything else here.
    title = entry.get("title") or next(
        (r["metadata"].get("title") for r in records if r["metadata"].get("title")), None
    ) or rel_path
    content_hash = file_hash(path)
    # last_modified: the register's date when it has one, else the file's own.
    last_modified = entry.get("last_modified") or datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).isoformat(timespec="seconds")

    labelled = []
    for index, chunk in enumerate(chunk_records(records, source_type)):
        kb_id = f"kb:{entry['doc_id']}#{chunk['chunk_id']}"
        # The title and section go in front of the body. Fourteen manuals have a
        # "Warranty" section, four a "Pairing" and four a "Factory reset", and
        # the reply prompt reads chunk text without its id: the heading is the
        # only place it learns which product "hold power for 10 seconds" is for.
        # It is also embedded, which retrieval does not need: measured on 29
        # October, embedding the body alone raised dense Hit@1 from 0.70 to 0.78
        # (Session 9's "cleanup PR"). Embedding the body and storing the heading
        # is the next experiment; rerun the metrics before making it.
        heading = f"{title} > {chunk['section']}" if chunk["section"] else title
        text = f"{heading}\n{chunk['text']}"
        labelled.append({
            # uuid5 of the citation id: re-ingesting the same section gives
            # the same point id (see ingest.py).
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, kb_id)),
            "text": text,
            "payload": {
                "kb_id": kb_id,
                "doc_id": entry["doc_id"],
                "chunk_id": chunk["chunk_id"],
                "chunk_index": index,
                "title": title,
                "section": chunk["section"],
                "text": text,
                "source_path": rel_path,
                "source_type": source_type,
                "last_modified": last_modified,
                "content_hash": content_hash,
                # From the manifest, never from the text: who may read it, and
                # whether it is still the version to trust.
                "allowed_groups": list(entry["allowed_groups"]),
                "status": entry["status"],
                "effective_from": entry.get("effective_from"),
                "superseded_by": entry.get("superseded_by"),
                "owner": entry.get("owner"),
                **chunk["extra"],
            },
        })
    return labelled


# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------

def create_collection(client: QdrantClient, dim: int) -> None:
    """(Re)create the "kb" collection: a named dense vector and a named BM25
    sparse vector on every point."""
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        COLLECTION,
        vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        # IDF is computed by Qdrant over the stored points, so adding or
        # removing documents updates every word's weight with no re-embedding.
        sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    # On a Qdrant server you would add keyword payload indexes on
    # allowed_groups, status and source_path here, so filtered search stays
    # fast at a million points. Local mode ignores payload indexes.


def has_sparse(client: QdrantClient) -> bool:
    """Does this collection have BM25 vectors? (A hand-made or older index
    may not, and hybrid search would then match nothing on the keyword side.)"""
    params = client.get_collection(COLLECTION).config.params
    return bool(params.sparse_vectors) and SPARSE in params.sparse_vectors


def upsert_chunks(client: QdrantClient, chunks: list[dict]) -> llm.Usage:
    """Embed and store chunks in batches. Returns the embedding bill."""
    usage = llm.Usage(prompt_tokens=0, completion_tokens=0, cost_usd=0.0)
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start:start + EMBED_BATCH]
        texts = [c["text"] for c in batch]
        dense, batch_usage = llm.embed_texts(texts)
        usage = llm.Usage(
            prompt_tokens=usage.prompt_tokens + batch_usage.prompt_tokens,
            completion_tokens=0,
            cost_usd=usage.cost_usd + batch_usage.cost_usd,
        )
        sparse = sparse_embed_documents(texts)
        client.upsert(COLLECTION, points=[
            models.PointStruct(id=c["id"], vector={DENSE: d, SPARSE: s}, payload=c["payload"])
            for c, d, s in zip(batch, dense, sparse)
        ])
    return usage


def plan_corpus(kb_dir: Path = KB_DIR) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Which files will be indexed, and which are refused or missing.

    Returns (indexable {rel_path: manifest entry}, problems {"unlisted": [...],
    "missing": [...]}). Unlisted = on disk, not in the manifest: not indexed.
    Missing = in the manifest, not on disk: nothing to index.
    """
    manifest = load_manifest(kb_dir)
    on_disk = set(corpus_files(kb_dir))
    indexable = {p: e for p, e in manifest.items() if p in on_disk}
    return indexable, {
        "unlisted": sorted(on_disk - set(manifest)),
        "missing": sorted(set(manifest) - on_disk),
    }


def ingest(
    kb_dir: Path = KB_DIR,
    qdrant_path: Path = QDRANT_PATH,
    client: QdrantClient | None = None,
) -> dict:
    """Rebuild the "kb" collection from scratch. Returns a report dict."""
    indexable, problems = plan_corpus(kb_dir)
    chunks = [c for rel, entry in sorted(indexable.items())
              for c in file_chunks(kb_dir, rel, entry)]
    if not chunks:
        raise SystemExit(f"nothing to index in {kb_dir}")

    own_client = client is None
    client = client or QdrantClient(path=str(qdrant_path))
    try:
        # One embedding call to learn the vector size: the collection needs it
        # before the first point goes in. (The LangChain store does the same
        # with "dummy_text"; here it is the first real chunk, so not wasted:
        # it is embedded again with its batch, a few tokens on the bill.)
        probe, probe_usage = llm.embed_texts([chunks[0]["text"]])
        create_collection(client, len(probe[0]))
        usage = upsert_chunks(client, chunks)
        count = client.count(COLLECTION).count
    finally:
        if own_client:
            client.close()  # release the folder lock for the next process

    by_type: dict[str, int] = {}
    for c in chunks:
        by_type[c["payload"]["source_type"]] = by_type.get(c["payload"]["source_type"], 0) + 1
    return {
        "files": len(indexable),
        "chunks": count,
        "by_source_type": by_type,
        "superseded_files": sorted(p for p, e in indexable.items() if e["status"] == "superseded"),
        "staff_only_files": sorted(p for p, e in indexable.items()
                                   if "customer" not in e["allowed_groups"]),
        "usage": llm.Usage(
            prompt_tokens=usage.prompt_tokens + probe_usage.prompt_tokens,
            completion_tokens=0,
            cost_usd=usage.cost_usd + probe_usage.cost_usd,
        ),
        **problems,
    }


if __name__ == "__main__":
    report = ingest()
    usage = report["usage"]
    kinds = ", ".join(f"{n} {t}" for t, n in sorted(report["by_source_type"].items()))
    print(f"indexed {report['files']} files as {report['chunks']} chunks ({kinds}) "
          f"into '{COLLECTION}' at {QDRANT_PATH}")
    print(f"vectors: dense ({llm.EMBED_MODEL}) + BM25 sparse")
    print(f"staff only: {len(report['staff_only_files'])} files; "
          f"superseded: {', '.join(report['superseded_files']) or 'none'}")
    for path in report["unlisted"]:
        print(f"NOT INDEXED (no manifest entry, so no permissions): {path}")
    for path in report["missing"]:
        print(f"missing file (in the manifest, not on disk): {path}")
    print(f"embedding: {usage.prompt_tokens} tokens, ${usage.cost_usd:.6f}")
