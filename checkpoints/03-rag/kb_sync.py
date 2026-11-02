"""Keep the "kb" collection in step with shared/kb, re-embedding only what changed.

kb_ingest.py rebuilds everything: every chunk embedded again, the index
empty while it runs. Fine for 30 files in class, not for a desk with 30,000
articles where three changed overnight. This script compares each file with
what the index already holds and touches only the differences:

- added:      on disk and in the manifest, not in the index -> embed and store
- changed:    its content_hash differs from the stored one -> delete its old
              points, embed and store the new chunks (the chunk count may
              change, so delete-then-insert rather than overwrite)
- relabelled: same bytes, but the manifest changed who may read it or its
              status (say, current -> superseded) -> update the payload of
              its points, no embedding call at all
- removed:    in the index, gone from disk or dropped from the manifest ->
              delete its points, so a withdrawn document cannot be retrieved

The relabel case matters as much as the content one. A permission revoked in
the source system that never reaches the index is a leak that no test of the
text will find.

Run:  py kb_sync.py          (prints what it did; nothing changed = no calls)
"""
from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient, models

import llm
from ingest import QDRANT_PATH
from kb_ingest import (
    COLLECTION,
    KB_DIR,
    file_chunks,
    file_hash,
    plan_corpus,
    upsert_chunks,
)

# Manifest fields stored on every point that can change without the file
# changing. A new doc_id or title changes the chunk ids or the embedded text,
# so those count as "changed", not "relabelled".
LABELS = ("allowed_groups", "status", "effective_from", "superseded_by", "owner")


def _by_path(source_path: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(
        key="source_path", match=models.MatchValue(value=source_path))])


def stored_files(client: QdrantClient) -> dict[str, dict]:
    """What the index holds, per source file: its hash, labels, point count.
    Read with scroll (page through every point, payload only, no vectors)."""
    files: dict[str, dict] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            COLLECTION, limit=256, offset=offset, with_payload=True, with_vectors=False
        )
        for point in points:
            p = point.payload or {}
            entry = files.setdefault(p["source_path"], {
                "content_hash": p.get("content_hash"),
                "doc_id": p.get("doc_id"),
                "title": p.get("title"),
                **{label: p.get(label) for label in LABELS},
                "points": 0,
            })
            entry["points"] += 1
        if offset is None:
            return files


def sync(
    kb_dir: Path = KB_DIR,
    qdrant_path: Path = QDRANT_PATH,
    client: QdrantClient | None = None,
) -> dict:
    """Bring the index in line with kb_dir. Returns what was done, per kind."""
    indexable, problems = plan_corpus(kb_dir)
    own_client = client is None
    client = client or QdrantClient(path=str(qdrant_path))
    report: dict = {"added": [], "changed": [], "relabelled": [], "removed": [],
                    "unchanged": [], **problems}
    usage = llm.Usage(prompt_tokens=0, completion_tokens=0, cost_usd=0.0)
    try:
        if not client.collection_exists(COLLECTION):
            raise SystemExit(f"no '{COLLECTION}' collection — run `py kb_ingest.py` first")
        stored = stored_files(client)
        to_embed: list[dict] = []

        for rel, entry in sorted(indexable.items()):
            new_hash = file_hash(kb_dir / rel)
            old = stored.get(rel)
            if old is None:
                chunks = file_chunks(kb_dir, rel, entry)
                to_embed += chunks
                report["added"].append({"path": rel, "chunks": len(chunks)})
            elif (old["content_hash"] != new_hash or old["doc_id"] != entry["doc_id"]
                  or (entry.get("title") and old["title"] != entry["title"])):
                client.delete(COLLECTION, points_selector=models.FilterSelector(filter=_by_path(rel)))
                chunks = file_chunks(kb_dir, rel, entry)
                to_embed += chunks
                report["changed"].append({"path": rel, "old_hash": old["content_hash"][:12],
                                          "new_hash": new_hash[:12], "chunks": len(chunks)})
            else:
                new_labels = {label: entry.get(label) for label in LABELS}
                new_labels["allowed_groups"] = list(entry["allowed_groups"])
                diff = {k: (old[k], v) for k, v in new_labels.items() if old[k] != v}
                if diff:
                    # set_payload merges these keys into every point of the file:
                    # no re-embedding, the vectors did not change.
                    client.set_payload(COLLECTION, payload=new_labels, points=_by_path(rel))
                    report["relabelled"].append({"path": rel, "diff": diff})
                else:
                    report["unchanged"].append(rel)

        for rel in sorted(set(stored) - set(indexable)):
            client.delete(COLLECTION, points_selector=models.FilterSelector(filter=_by_path(rel)))
            report["removed"].append({"path": rel, "points": stored[rel]["points"]})

        if to_embed:
            usage = upsert_chunks(client, to_embed)
        report["chunks"] = client.count(COLLECTION).count
    finally:
        if own_client:
            client.close()
    report["usage"] = usage
    return report


def print_report(report: dict) -> None:
    for item in report["added"]:
        print(f"+ added       {item['path']} ({item['chunks']} chunks)")
    for item in report["changed"]:
        print(f"~ changed     {item['path']} ({item['old_hash']} -> {item['new_hash']}, "
              f"{item['chunks']} chunks re-embedded)")
    for item in report["relabelled"]:
        changes = "; ".join(f"{k}: {a} -> {b}" for k, (a, b) in item["diff"].items())
        print(f"= relabelled  {item['path']} ({changes}; no embedding)")
    for item in report["removed"]:
        print(f"- removed     {item['path']} ({item['points']} points deleted)")
    for path in report["unlisted"]:
        print(f"! not indexed {path} (no manifest entry, so no permissions)")
    usage = report["usage"]
    print(f"{len(report['unchanged'])} files unchanged; index now holds {report['chunks']} "
          f"chunks; embedding: {usage.prompt_tokens} tokens, ${usage.cost_usd:.6f}")


if __name__ == "__main__":
    print_report(sync())
