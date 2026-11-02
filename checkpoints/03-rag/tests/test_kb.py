"""Offline tests for the support knowledge base: parsers, the manifest join,
the access filter, superseded documents, hybrid and rerank, and sync.

No network and no model downloads. Dense embeddings are a bag of hashed
words (texts sharing words get close vectors), BM25 is the real one (plain
Python, offline by construction), and the reranker counts query words.
Qdrant runs in local mode in tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import pytest
from qdrant_client import QdrantClient, models

import kb_ingest
import kb_parsers
import kb_search
import kb_sync
import llm

# ---------------------------------------------------------------------------
# A small knowledge base, one file per format and every trap kind
# ---------------------------------------------------------------------------

MANUAL_MD = """# AirBeat Wireless Headphones: help

Model NB-AB-200, over-ear Bluetooth headphones.

## Pairing

Hold the power button for 4 seconds until the LED flashes blue.

## Resetting

Hold power and volume down for 10 seconds to reset the headphones.
"""

MACRO_MD = """# Goodwill credit

## Limits

Agents may give goodwill credit up to $10 without approval from a lead.
"""

OLD_WARRANTY_MD = """# Warranty replacements (2025)

## Returning the old unit

Send the broken unit back first; the replacement ships when it arrives.
"""

NEW_WARRANTY_MD = """# Warranty replacements

## Returning the old unit

The replacement ships first; return the broken unit within 30 days.
"""

CARRIER_HTML = """<!DOCTYPE html><html><head><title>Holiday schedule | Parcelink</title>
<script>var tracking = "analytics";</script><style>body{color:red}</style></head>
<body>
<div id="cookie-banner" role="dialog"><p>We use cookies. Accept all</p></div>
<header class="site-header"><nav><a href="/track">Track</a> <a href="/ship">Ship</a></nav></header>
<main><article>
<h1>Holiday 2026 ship-by dates</h1>
<p class="meta">Published 19 October 2026 &middot; all services</p>
<h2>Ship-by dates</h2>
<table><tr><th>Service</th><th>Ship by</th></tr>
<tr><td>Ground</td><td>16 December</td></tr></table>
<h2>No delivery</h2><ul><li>Christmas Day</li><li>New Year's Day</li></ul>
</article><aside><p>Related: Track a parcel</p></aside></main>
<footer><p>Copyright Parcelink. Privacy. Terms.</p></footer>
</body></html>
"""

RECALL_EML = (
    "From: Nimbus Ops <ops@nimbus.example>\r\n"
    "To: Support Team <support@nimbus.example>\r\n"
    "Subject: =?utf-8?q?=5BRECALL=5D_PowerHub_65W_=E2=80=94_date_code_2608?=\r\n"
    "Date: Mon, 12 Oct 2026 08:14:47 -0700\r\n"
    "Received: from mail.example by mx.example; Mon, 12 Oct 2026 08:14:52 -0700\r\n"
    "MIME-Version: 1.0\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n"
    "Content-Transfer-Encoding: quoted-printable\r\n"
    "\r\n"
    "Chargers with date code 2608 overheat. Offer a replacement or a full =\r\n"
    "refund, whatever the delivery date.\r\n"
)

PAST_CSV = (
    "﻿ticket_id,product,issue_summary,resolution_notes\r\n"
    "T-0612,GlowCore Bulbs,bulbs would not join wifi,joined the 2.4 GHz network\r\n"
    "T-0667,Trailblazer 20K,charges laptop slowly,\"22.5 W max, expected\"\r\n"
    "\r\n"
)

MANIFEST = {"documents": [
    {"doc_id": "manual-airbeat", "path": "manuals/airbeat.md", "title": "AirBeat Wireless Headphones",
     "allowed_groups": ["customer", "support-agent"], "status": "current",
     "effective_from": "2026-05-04", "owner": "Product Support"},
    {"doc_id": "macro-goodwill", "path": "macros/goodwill.md", "title": "Goodwill credit",
     "allowed_groups": ["support-agent"], "status": "current", "effective_from": "2026-01-10"},
    {"doc_id": "warranty-2025", "path": "manuals/warranty-2025.md", "title": "Warranty replacements (2025)",
     "allowed_groups": ["customer", "support-agent"], "status": "superseded",
     "effective_from": "2025-01-01", "superseded_by": "warranty"},
    {"doc_id": "warranty", "path": "manuals/warranty.md", "title": "Warranty replacements",
     "allowed_groups": ["customer", "support-agent"], "status": "current", "effective_from": "2026-07-01"},
    {"doc_id": "carrier-holiday-2026", "path": "carrier/holiday-2026.html",
     "allowed_groups": ["customer", "support-agent"], "status": "current", "effective_from": "2026-10-19"},
    {"doc_id": "ops-recall-powerhub", "path": "ops/recall.eml",
     "allowed_groups": ["support-agent"], "status": "current", "effective_from": "2026-10-12"},
    {"doc_id": "past-tickets", "path": "past-tickets.csv", "title": "Resolved tickets",
     "allowed_groups": ["support-agent"], "status": "current", "effective_from": "2026-10-01"},
]}


def write_kb(root: Path, manifest: dict = MANIFEST) -> Path:
    kb = root / "kb"
    files = {
        "manuals/airbeat.md": MANUAL_MD, "macros/goodwill.md": MACRO_MD,
        "manuals/warranty-2025.md": OLD_WARRANTY_MD, "manuals/warranty.md": NEW_WARRANTY_MD,
        "carrier/holiday-2026.html": CARRIER_HTML, "past-tickets.csv": PAST_CSV,
        # Nobody registered this one: it must never be indexed.
        "macros/draft-unlisted.md": "# Draft\n\n## Secret\n\nUnreviewed refund shortcut.\n",
    }
    for rel, text in files.items():
        (kb / rel).parent.mkdir(parents=True, exist_ok=True)
        (kb / rel).write_text(text, encoding="utf-8", newline="")
    (kb / "ops").mkdir(exist_ok=True)
    (kb / "ops" / "recall.eml").write_bytes(RECALL_EML.encode("ascii"))
    (kb / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return kb


# ---------------------------------------------------------------------------
# Fakes for the three models
# ---------------------------------------------------------------------------

DIM = 64
NO_USAGE = llm.Usage(prompt_tokens=0, completion_tokens=0, cost_usd=0.0)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _bucket(word: str, size: int) -> int:
    # md5, not hash(): Python salts hash() per process.
    return int(hashlib.md5(word.encode()).hexdigest(), 16) % size


def fake_embed(texts: list[str], model: str | None = None):
    """Bag of hashed words, normalised: shared words -> high cosine."""
    vectors = []
    for text in texts:
        v = [0.0] * DIM
        for w in _words(text):
            v[_bucket(w, DIM)] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vectors.append([x / norm for x in v])
    return vectors, llm.Usage(prompt_tokens=len(texts), completion_tokens=0, cost_usd=0.0)


@pytest.fixture()
def fakes(monkeypatch):
    calls = {"embed": 0}

    def counting_embed(texts, model=None):
        calls["embed"] += 1
        return fake_embed(texts, model)

    monkeypatch.setattr(llm, "embed_texts", counting_embed)
    # The fake reranker: how many of the query's words the text contains.
    monkeypatch.setattr(kb_search, "rerank_scores", lambda q, texts, reranker="auto": (
        [float(len(set(_words(q)) & set(_words(t)))) for t in texts], "fake", NO_USAGE))
    return calls


@pytest.fixture()
def kb_client(tmp_path, fakes):
    kb = write_kb(tmp_path)
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    kb_ingest.ingest(kb_dir=kb, client=client)
    yield client
    client.close()


def search(client, query, groups, **kw):
    return kb_search.search_kb(query, groups, client=client, **kw)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_html_keeps_the_article_and_drops_the_chrome() -> None:
    [record] = kb_parsers.parse_html(CARRIER_HTML)
    text = record["text"]
    for chrome in ("analytics", "color:red", "cookies", "Track", "Copyright", "Related"):
        assert chrome not in text
    assert record["metadata"]["title"] == "Holiday 2026 ship-by dates"
    assert "## Ship-by dates" in text and "## No delivery" in text  # headings survive as ##
    assert "Ground | 16 December" in text
    assert "- Christmas Day" in text
    assert "Published 19 October 2026 · all services" in text  # &middot; decoded


def test_email_keeps_subject_and_body_not_the_headers() -> None:
    [record] = kb_parsers.parse_email(RECALL_EML.encode("ascii"))
    assert record["text"].startswith("[RECALL] PowerHub 65W — date code 2608\n\n")
    assert "a full refund, whatever the delivery date" in record["text"]  # soft break joined
    assert "Received" not in record["text"] and "MIME" not in record["text"]
    meta = record["metadata"]
    assert meta["from"] == "Nimbus Ops <ops@nimbus.example>"
    assert meta["sent_at"] == "2026-10-12T08:14:47-07:00"


def test_csv_is_one_record_per_row() -> None:
    records = kb_parsers.parse_csv(PAST_CSV.lstrip("﻿"))
    assert len(records) == 2  # the blank last line is not a row
    assert records[0]["metadata"] == {"row": 1, "row_id": "T-0612"}
    assert "resolution notes: joined the 2.4 GHz network" in records[0]["text"]
    assert "22.5 W max, expected" in records[1]["text"]  # a quoted comma stays in its cell


def test_csv_with_a_byte_order_mark_keeps_its_first_column(tmp_path) -> None:
    path = tmp_path / "t.csv"
    path.write_text(PAST_CSV, encoding="utf-8")
    assert kb_parsers.parse_file(path)[0]["metadata"]["row_id"] == "T-0612"


def test_markdown_is_one_record_with_its_title() -> None:
    [record] = kb_parsers.parse_markdown(MANUAL_MD)
    assert record["metadata"]["title"] == "AirBeat Wireless Headphones: help"


def test_unknown_extension_is_refused(tmp_path) -> None:
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="no parser"):
        kb_parsers.parse_file(path)


# ---------------------------------------------------------------------------
# The manifest join
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad, message", [
    ({"allowed_groups": ["customers"]}, "allowed_groups"),
    ({"allowed_groups": []}, "allowed_groups"),
    ({"status": "draft"}, "status"),
])
def test_a_bad_manifest_entry_fails_the_ingest(tmp_path, bad, message) -> None:
    docs = [dict(MANIFEST["documents"][0], **bad)] + MANIFEST["documents"][1:]
    kb = write_kb(tmp_path, {"documents": docs})
    with pytest.raises(ValueError, match=message):
        kb_ingest.load_manifest(kb)


def test_duplicate_doc_id_fails(tmp_path) -> None:
    docs = MANIFEST["documents"] + [dict(MANIFEST["documents"][0], path="manuals/copy.md")]
    with pytest.raises(ValueError, match="twice"):
        kb_ingest.load_manifest(write_kb(tmp_path, {"documents": docs}))


def test_unlisted_files_are_not_indexed_and_missing_ones_reported(tmp_path) -> None:
    docs = MANIFEST["documents"] + [{"doc_id": "ghost", "path": "manuals/ghost.md",
                                     "allowed_groups": ["customer"], "status": "current"}]
    indexable, problems = kb_ingest.plan_corpus(write_kb(tmp_path, {"documents": docs}))
    assert "macros/draft-unlisted.md" not in indexable
    assert problems == {"unlisted": ["macros/draft-unlisted.md"], "missing": ["manuals/ghost.md"]}


def test_chunks_carry_the_manifest_labels(tmp_path) -> None:
    kb = write_kb(tmp_path)
    manifest = kb_ingest.load_manifest(kb)
    chunks = kb_ingest.file_chunks(kb, "manuals/airbeat.md", manifest["manuals/airbeat.md"])
    assert [c["payload"]["chunk_id"] for c in chunks] == ["overview", "pairing", "resetting"]
    payload = chunks[2]["payload"]
    assert payload["kb_id"] == "kb:manual-airbeat#resetting"
    assert payload["allowed_groups"] == ["customer", "support-agent"]
    assert payload["status"] == "current" and payload["effective_from"] == "2026-05-04"
    assert payload["source_type"] == "markdown" and payload["source_path"] == "manuals/airbeat.md"
    assert payload["content_hash"] == kb_ingest.file_hash(kb / "manuals/airbeat.md")
    assert payload["last_modified"]
    # The product name rides in every chunk, so near-duplicate manuals differ.
    assert chunks[2]["text"].startswith("AirBeat Wireless Headphones > Resetting\n")
    # Stable ids: the same file chunks to the same point ids.
    again = kb_ingest.file_chunks(kb, "manuals/airbeat.md", manifest["manuals/airbeat.md"])
    assert [c["id"] for c in chunks] == [c["id"] for c in again]


def test_csv_rows_and_email_become_one_chunk_each(tmp_path) -> None:
    kb = write_kb(tmp_path)
    manifest = kb_ingest.load_manifest(kb)
    rows = kb_ingest.file_chunks(kb, "past-tickets.csv", manifest["past-tickets.csv"])
    assert [c["payload"]["kb_id"] for c in rows] == ["kb:past-tickets#t-0612", "kb:past-tickets#t-0667"]
    [mail] = kb_ingest.file_chunks(kb, "ops/recall.eml", manifest["ops/recall.eml"])
    assert mail["payload"]["source_type"] == "email"
    assert mail["payload"]["sent_at"].startswith("2026-10-12")


def test_ingest_counts(tmp_path, fakes) -> None:
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        report = kb_ingest.ingest(kb_dir=write_kb(tmp_path), client=client)
        assert report["files"] == 7
        assert report["unlisted"] == ["macros/draft-unlisted.md"]
        assert report["by_source_type"] == {"markdown": 6, "html": 3, "email": 1, "csv": 2}
        assert report["chunks"] == 12
        assert kb_ingest.has_sparse(client)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# The access filter
# ---------------------------------------------------------------------------

def test_caller_groups_is_required_and_checked(kb_client) -> None:
    with pytest.raises(TypeError):
        kb_search.search_kb("reset", client=kb_client)  # no default to fall back on
    for bad in ("customer", [], ["admin"]):
        with pytest.raises(ValueError):
            search(kb_client, "reset", bad)


@pytest.mark.parametrize("mode, rerank", [("dense", False), ("hybrid", False), ("hybrid", True)])
def test_a_customer_never_receives_a_support_agent_chunk(kb_client, mode, rerank) -> None:
    for query in ("goodwill credit up to $10 without approval", "date code 2608 recall",
                  "past ticket resolution notes wifi", "reset headphones"):
        result = search(kb_client, query, ["customer"], k=20, mode=mode, rerank=rerank)
        assert all("customer" in c["allowed_groups"] for c in result.chunks), query
        assert not any(c["doc_id"] in {"macro-goodwill", "ops-recall-powerhub", "past-tickets"}
                       for c in result.chunks)


def test_the_filter_runs_inside_the_search(kb_client) -> None:
    # 7 chunks a customer may read that are current: manual 3, warranty 1,
    # carrier 3. Ask for 20 and get 7: k is a ceiling, and nothing forbidden
    # came back to be dropped.
    result = search(kb_client, "anything at all", ["customer"], k=20)
    assert len(result.chunks) == 7
    assert result.trace["filter"] == "allowed_groups has any of ['customer'] AND status = current"


def test_a_support_agent_finds_the_macro(kb_client) -> None:
    result = search(kb_client, "goodwill credit without approval", ["support-agent"], k=3)
    assert result.chunks[0]["kb_id"] == "kb:macro-goodwill#limits"
    assert result.trace["caller_groups"] == ["support-agent"]


def test_superseded_is_excluded_unless_asked_for(kb_client) -> None:
    query = "send the broken unit back first replacement ships"
    current = search(kb_client, query, ["customer"], k=20)
    assert "warranty-2025" not in {c["doc_id"] for c in current.chunks}
    with_old = search(kb_client, query, ["customer"], k=20, include_superseded=True)
    old = [c for c in with_old.chunks if c["doc_id"] == "warranty-2025"]
    assert old and old[0]["status"] == "superseded"


def test_channels_map_to_groups_and_unknown_is_customer() -> None:
    assert kb_search.groups_for_channel("agent_desk") == ("support-agent",)
    assert kb_search.groups_for_channel("self_serve") == ("customer",)
    for channel in (None, "email", "chat", "web", "lab", "admin"):
        assert kb_search.groups_for_channel(channel) == ("customer",)


# ---------------------------------------------------------------------------
# Hybrid and rerank
# ---------------------------------------------------------------------------

def test_hybrid_returns_fused_results_with_a_trace(kb_client) -> None:
    result = search(kb_client, "2608", ["support-agent"], k=3, mode="hybrid")
    assert result.chunks[0]["doc_id"] == "ops-recall-powerhub"
    assert result.trace["mode"] == "hybrid" and len(result.trace["hits"]) == 3


def test_bm25_tokens_keep_numbers_and_trim_plurals() -> None:
    assert kb_ingest.bm25_tokens("The Bulbs flicker on 2.4 GHz, date code 2608!") == [
        "bulb", "flicker", "2.4", "ghz", "date", "code", "2608"]
    assert kb_ingest.bm25_tokens("glass press") == ["glass", "press"]  # -ss stays


def test_bm25_weights_saturate_and_discount_length() -> None:
    once, thrice = kb_ingest.sparse_embed_documents(["recall", "recall recall recall"])
    assert once.values[0] < thrice.values[0] < kb_ingest.BM25_K1 + 1  # more, never unbounded
    padding = " ".join(f"word{n}" for n in range(200))
    short, long = kb_ingest.sparse_embed_documents(["recall", f"recall {padding}"])
    rid = kb_ingest._token_id("recall")
    assert long.values[long.indices.index(rid)] < short.values[short.indices.index(rid)]
    query = kb_ingest.sparse_embed_query("recall recall 2608")
    assert sorted(query.values) == [1.0, 1.0]  # each distinct word once


def test_hybrid_refuses_an_index_without_bm25(tmp_path, fakes) -> None:
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        client.create_collection(kb_ingest.COLLECTION, vectors_config={
            kb_ingest.DENSE: models.VectorParams(size=DIM, distance=models.Distance.COSINE)})
        with pytest.raises(RuntimeError, match="no BM25"):
            search(client, "reset", ["customer"], mode="hybrid")
    finally:
        client.close()


def test_rerank_reorders_a_wider_pool(kb_client, monkeypatch) -> None:
    seen = {}

    def spy(query, texts, reranker="auto"):
        seen["pool"] = len(texts)
        # Prefer the chunk that mentions "volume": a different order from dense.
        return [10.0 if "volume" in t else 0.0 for t in texts], "spy", NO_USAGE

    monkeypatch.setattr(kb_search, "rerank_scores", spy)
    result = search(kb_client, "headphones", ["customer"], k=2, rerank=True)
    assert seen["pool"] == 7  # every customer-visible current chunk: the pool is 20
    assert result.trace["pool"] == kb_search.RERANK_POOL
    assert result.trace["reranker"] == "spy"
    assert result.chunks[0]["kb_id"] == "kb:manual-airbeat#resetting"
    assert result.chunks[0]["rank"] == 1 and "first_stage_rank" in result.chunks[0]
    assert len(result.chunks) == 2


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def test_sync_with_nothing_changed_makes_no_calls(tmp_path, fakes) -> None:
    kb = write_kb(tmp_path)
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        kb_ingest.ingest(kb_dir=kb, client=client)
        fakes["embed"] = 0
        report = kb_sync.sync(kb_dir=kb, client=client)
        assert fakes["embed"] == 0
        assert len(report["unchanged"]) == 7
        assert not (report["added"] or report["changed"] or report["relabelled"] or report["removed"])
    finally:
        client.close()


def test_sync_reembeds_changed_drops_removed_relabels_without_embedding(tmp_path, fakes) -> None:
    kb = write_kb(tmp_path)
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        kb_ingest.ingest(kb_dir=kb, client=client)

        # 1. A manual gains a section: re-embedded, alone.
        (kb / "manuals/airbeat.md").write_text(
            MANUAL_MD + "\n## Firmware\n\nUpdate in the Nimbus Hub app.\n", encoding="utf-8")
        # 2. The carrier page is deleted at the source.
        (kb / "carrier/holiday-2026.html").unlink()
        # 3. The macro is (wrongly, say) opened to customers in the register.
        manifest = json.loads((kb / "manifest.json").read_text(encoding="utf-8"))
        for doc in manifest["documents"]:
            if doc["doc_id"] == "macro-goodwill":
                doc["allowed_groups"] = ["customer", "support-agent"]
        (kb / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        fakes["embed"] = 0
        report = kb_sync.sync(kb_dir=kb, client=client)
        assert [c["path"] for c in report["changed"]] == ["manuals/airbeat.md"]
        assert report["changed"][0]["chunks"] == 4
        assert report["removed"] == [{"path": "carrier/holiday-2026.html", "points": 3}]
        assert [r["path"] for r in report["relabelled"]] == ["macros/goodwill.md"]
        assert fakes["embed"] == 1  # one batch: the changed manual only
        assert report["chunks"] == 12 + 1 - 3

        # The index now answers as the source system says.
        ids = {c["kb_id"] for c in search(client, "firmware nimbus hub", ["customer"], k=20).chunks}
        assert "kb:manual-airbeat#firmware" in ids
        assert not any(i.startswith("kb:carrier-holiday-2026") for i in ids)
        assert "kb:macro-goodwill#limits" in ids  # relabelled: customers now see it

        # A second run finds nothing to do.
        again = kb_sync.sync(kb_dir=kb, client=client)
        assert len(again["unchanged"]) == 6 and not again["changed"] and not again["removed"]
    finally:
        client.close()


def test_sync_refuses_without_a_collection(tmp_path, fakes) -> None:
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        with pytest.raises(SystemExit, match="kb_ingest"):
            kb_sync.sync(kb_dir=write_kb(tmp_path), client=client)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Which reranker runs
# ---------------------------------------------------------------------------

def test_auto_rerank_falls_back_to_the_llm_when_no_cross_encoder(monkeypatch) -> None:
    from models import KbRerank, PassageScore

    monkeypatch.setattr(kb_search, "_load_cross_encoder", lambda allow_download: None)
    prompts = []

    def fake_llm(messages, response_model, **kwargs):
        prompts.append(messages[-1]["content"])
        assert response_model is KbRerank
        # Passage 3 is skipped on purpose: it must score 0, not crash.
        return KbRerank(scores=[PassageScore(passage=1, relevance=2),
                                PassageScore(passage=2, relevance=9)]), \
            llm.Usage(prompt_tokens=50, completion_tokens=10, cost_usd=0.0001)

    monkeypatch.setattr(llm, "structured_completion", fake_llm)
    scores, name, usage = kb_search.rerank_scores("reset", ["a", "b", "c"])
    assert scores == [2.0, 9.0, 0.0]
    assert name.startswith("llm ")
    assert usage.cost_usd == pytest.approx(0.0001)
    assert "[2] b" in prompts[0]


def test_auto_rerank_uses_a_cached_cross_encoder(monkeypatch) -> None:
    class FakeCrossEncoder:
        def rerank(self, query, texts):
            return iter([0.5, -1.0])

    monkeypatch.setattr(kb_search, "_load_cross_encoder", lambda allow_download: FakeCrossEncoder())
    monkeypatch.setattr(llm, "structured_completion",
                        lambda *a, **k: pytest.fail("no LLM call when the model is there"))
    scores, name, usage = kb_search.rerank_scores("reset", ["a", "b"])
    assert scores == [0.5, -1.0]
    assert name == f"cross-encoder {kb_search.RERANK_MODEL}"
    assert usage.cost_usd == 0.0


def test_auto_never_starts_a_download(monkeypatch) -> None:
    calls = []

    class Recorder:
        def __init__(self, model_name, local_files_only=False):
            calls.append(local_files_only)
            raise ValueError("not in cache")

    monkeypatch.setattr(kb_search, "TextCrossEncoder", Recorder)
    monkeypatch.setattr(kb_search, "_cross_encoder", None)
    monkeypatch.setattr(kb_search, "_cross_encoder_missing", False)
    assert kb_search._load_cross_encoder(allow_download=False) is None
    assert kb_search._load_cross_encoder(allow_download=False) is None  # remembered
    assert calls == [True]
