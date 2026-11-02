"""The policy is data: read it fresh, record every version you apply.

policies.json is the source of truth and operations edit it. The engine
reads the file on every evaluate (cached by its modification time, so a
thousand ticks cost nothing), validates it into a `Policy`, and records any
version it has not seen before in the policy_versions table. That table is
the audit copy: "which rules were in force on the night of the 2 a.m.
reminder" is answered by the version on the event row plus this table.

A policy file that does not parse is a named failure, POLICY_INVALID. The
engine then escalates the case instead of guessing a cadence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

import errors
import store
from models import Policy

POLICIES_PATH = Path(os.getenv("DUNNING_POLICIES") or Path(__file__).resolve().parent / "policies.json")

_cache: dict[str, object] = {"path": None, "mtime": None, "policies": {}}


def _load(path: Path) -> dict[str, Policy]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise errors.DeskhandError(errors.POLICY_INVALID, f"{path.name}: {exc}") from exc
    policies: dict[str, Policy] = {}
    for tenant, record in raw.items():
        try:
            policies[tenant] = Policy.model_validate({"tenant": tenant, **record})
        except ValidationError as exc:
            raise errors.DeskhandError(errors.POLICY_INVALID, f"{tenant}: {exc}") from exc
    return policies


def all_policies() -> dict[str, Policy]:
    path = Path(POLICIES_PATH)
    mtime = path.stat().st_mtime if path.exists() else None
    if _cache["path"] != path or _cache["mtime"] != mtime or mtime is None:
        _cache["policies"] = _load(path)
        _cache["path"], _cache["mtime"] = path, mtime
    return _cache["policies"]  # type: ignore[return-value]


def get(tenant: str, db_path: Path | None = None) -> Policy:
    """The tenant's current policy, recorded in the audit table if new."""
    policies = all_policies()
    if tenant not in policies:
        raise errors.DeskhandError(errors.POLICY_INVALID, f"no policy for tenant {tenant!r}")
    policy = policies[tenant]
    store.record_policy_version(
        tenant, policy.version, policy.model_dump_json(), store.now_wall(), db_path
    )
    return policy


def file_info() -> dict:
    path = Path(POLICIES_PATH)
    return {
        "path": str(path),
        "mtime": path.stat().st_mtime if path.exists() else None,
    }
