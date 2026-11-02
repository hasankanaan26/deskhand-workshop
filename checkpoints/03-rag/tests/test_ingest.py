"""Offline tests for the ingestion + retrieval pipeline.

Embeddings are monkeypatched with deterministic vectors at our boundary
(`llm.embed_texts`); Qdrant runs in local mode against a temp directory, so
no network or API key is needed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

import ingest
import llm
import retrieve

POLICY_MD = """<!-- doc_id: refund-policy | version: 2.3 | effective: 2026-06-01 -->

# Refund Policy

Intro paragraph before any section.

## Refund Window

Refunds are available within 14 days of delivery.

## Approval Thresholds {#approval}

Refunds up to $50 are auto-approved; above that a manager signs off.
"""

# One deterministic unit vector per known text, orthogonal to each other.
_FAKE_VECTORS = {
    "window": [1.0, 0.0, 0.0, 0.0],
    "approval": [0.0, 1.0, 0.0, 0.0],
    "other": [0.0, 0.0, 1.0, 0.0],
}


def _fake_embed(texts: list[str], model: str = "fake") -> tuple[list[list[float]], llm.Usage]:
    """A keyword stand-in for the embedding model. Texts about the window share
    one vector, so a window query must find the window section; a fixed cost
    per call lets the tests check the meter's arithmetic."""
    vectors = []
    for text in texts:
        lowered = text.lower()
        if "14 days" in lowered or "window" in lowered:
            vectors.append(_FAKE_VECTORS["window"])
        elif "approv" in lowered:
            vectors.append(_FAKE_VECTORS["approval"])
        else:
            vectors.append(_FAKE_VECTORS["other"])
    usage = llm.Usage(prompt_tokens=len(texts), completion_tokens=0, cost_usd=0.001)
    return vectors, usage


def _policies(tmp_path: Path) -> Path:
    policies = tmp_path / "policies"
    policies.mkdir()
    (policies / "refund-policy.md").write_text(POLICY_MD, encoding="utf-8")
    return policies


def test_parse_policy_file_splits_on_sections(tmp_path: Path) -> None:
    path = tmp_path / "refund-policy.md"
    path.write_text(POLICY_MD, encoding="utf-8")

    docs = ingest.parse_policy_file(path)

    # The title and intro before the first ## section are not a chunk.
    assert [d.metadata["section_id"] for d in docs] == ["refund-window", "approval"]
    assert all(d.metadata["doc_id"] == "refund-policy" for d in docs)
    assert all(d.metadata["version"] == "2.3" for d in docs)
    assert all(d.metadata["effective"] == "2026-06-01" for d in docs)
    assert "14 days" in docs[0].page_content
    # Heading first, anchor stripped.
    assert docs[1].page_content.startswith("Approval Thresholds\n")


def test_section_ids_are_stable(tmp_path: Path) -> None:
    # Same file, same point ids, and one id per section.
    path = tmp_path / "refund-policy.md"
    path.write_text(POLICY_MD, encoding="utf-8")
    first = [d.id for d in ingest.parse_policy_file(path)]
    second = [d.id for d in ingest.parse_policy_file(path)]
    assert first == second
    assert len(set(first)) == 2


def test_parse_policy_file_requires_header(tmp_path: Path) -> None:
    path = tmp_path / "broken.md"
    path.write_text("# No header\n\n## Section\ntext\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing doc_id header"):
        ingest.parse_policy_file(path)


def test_real_policy_pack_parses_into_27_sections() -> None:
    # The real shared/policies. A renamed heading changes a section id and
    # breaks every citation and golden label that uses it. The last two
    # asserts are the two sides of the planted 14-day vs 30-day contradiction.
    docs = ingest.load_policy_pack()
    ids = [f"{d.metadata['doc_id']}#{d.metadata['section_id']}" for d in docs]
    assert len(ids) == 27
    assert len(set(ids)) == 27
    assert "refund-policy#window" in ids
    assert "returns-and-exchanges#returns-window" in ids


def test_ingest_then_retrieve_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(llm, "embed_texts", _fake_embed)
    qdrant_path = tmp_path / "qdrant"

    count, meter = ingest.ingest(policies_dir=_policies(tmp_path), qdrant_path=qdrant_path)
    assert count == 2
    # The store's hidden "dummy_text" call to size the collection, then the
    # sections themselves — both on the bill.
    assert meter.calls == 2
    assert meter.usage.prompt_tokens == 3
    assert meter.usage.cost_usd == pytest.approx(0.002)

    client = QdrantClient(path=str(qdrant_path))  # ingest released the lock
    try:
        chunks, usage = retrieve.retrieve("what is the refund window?", k=1, client=client)
        assert len(chunks) == 1
        assert chunks[0]["doc_id"] == "refund-policy"
        assert chunks[0]["section_id"] == "refund-window"
        assert chunks[0]["score"] > 0.9  # identical fake vectors: cosine 1.0
        assert set(chunks[0]) == {
            "doc_id", "section_id", "version", "effective", "text", "score"
        }
        assert usage.cost_usd == pytest.approx(0.001)  # the query embedding

        chunks, _ = retrieve.retrieve("who approves large refunds?", k=2, client=client)
        assert chunks[0]["section_id"] == "approval"
    finally:
        client.close()


def test_doc_filter_runs_inside_the_search(tmp_path: Path, monkeypatch) -> None:
    """`doc_ids` is Qdrant's own filter: k is a ceiling, not a promise, and
    another document's sections never come back to be dropped afterwards."""
    monkeypatch.setattr(llm, "embed_texts", _fake_embed)
    qdrant_path = tmp_path / "qdrant"
    ingest.ingest(policies_dir=_policies(tmp_path), qdrant_path=qdrant_path)

    client = QdrantClient(path=str(qdrant_path))
    try:
        chunks, _ = retrieve.retrieve("refund window", k=5, client=client,
                                      doc_ids=["refund-policy"])
        assert [c["section_id"] for c in chunks] == ["refund-window", "approval"]  # 2 of 5 asked
        chunks, _ = retrieve.retrieve("refund window", k=5, client=client, doc_ids=["warranty"])
        assert chunks == []
        chunks, _ = retrieve.retrieve("refund window", k=5, client=client, doc_ids=None)
        assert len(chunks) == 2
    finally:
        client.close()


def test_reingest_replaces_instead_of_duplicating(tmp_path: Path, monkeypatch) -> None:
    # Two ingests, one copy of each section. force_recreate drops the
    # collection first, so this would pass with random ids too; the ids
    # themselves are pinned by test_section_ids_are_stable.
    monkeypatch.setattr(llm, "embed_texts", _fake_embed)
    policies, qdrant_path = _policies(tmp_path), tmp_path / "qdrant"
    ingest.ingest(policies_dir=policies, qdrant_path=qdrant_path)
    ingest.ingest(policies_dir=policies, qdrant_path=qdrant_path)

    client = QdrantClient(path=str(qdrant_path))
    try:
        assert client.count(ingest.COLLECTION).count == 2
    finally:
        client.close()


def test_retrieve_rejects_an_index_from_the_old_ingest(tmp_path: Path, monkeypatch) -> None:
    """Before LangChain, payloads were flat dicts. Say so instead of crashing."""
    from qdrant_client.models import Distance, PointStruct, VectorParams

    monkeypatch.setattr(llm, "embed_texts", _fake_embed)
    client = QdrantClient(path=str(tmp_path / "qdrant"))
    try:
        client.create_collection(
            ingest.COLLECTION, vectors_config=VectorParams(size=4, distance=Distance.COSINE)
        )
        client.upsert(ingest.COLLECTION, points=[PointStruct(
            id=1, vector=_FAKE_VECTORS["window"],
            payload={"doc_id": "refund-policy", "section_id": "window", "text": "..."},
        )])
        with pytest.raises(RuntimeError, match="older ingest.py"):
            retrieve.retrieve("refund window", k=1, client=client)
    finally:
        client.close()


def test_build_query_matches_the_service() -> None:
    # The eval and the service share this function; pin its shape.
    assert retrieve.build_query("refund_request", "x" * 600) == "refund_request: " + "x" * 500
    assert retrieve.build_query(None, "hello") == "support ticket: hello"


def test_golden_set_shape() -> None:
    golden_path = Path(__file__).resolve().parents[1] / "golden" / "golden_tickets.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))["golden_tickets"]
    sections = {
        f"{d.metadata['doc_id']}#{d.metadata['section_id']}"
        for d in ingest.load_policy_pack()
    }

    assert len(golden) == 46
    assert len({g["ticket_id"] for g in golden}) == 46
    for entry in golden:
        assert entry["ticket_id"].startswith("T-10")
        assert entry["expected_category"] in {
            "refund_request", "replacement", "shipping_delay",
            "product_defect", "account", "other",
        }
        assert isinstance(entry["expected_action_types"], list)
        assert isinstance(entry["expected_requires_approval"], bool)
        assert isinstance(entry["must_cite"], list)
        assert entry["notes"]
        # Every labelled section must exist in the policy pack (typo catcher),
        # and every must_cite doc must be covered by a labelled section.
        refs = entry["reference_contexts"]
        assert set(refs) <= sections, f"{entry['ticket_id']}: unknown {set(refs) - sections}"
        assert set(entry["must_cite"]) <= {r.split("#")[0] for r in refs}
