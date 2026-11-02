"""The two model outputs must be valid strict JSON schemas: every object
closed (additionalProperties false), every field typed and required.
An untyped `dict` field is exactly what a strict provider rejects."""
from __future__ import annotations

import pytest

from models import Proposal, ReplyReading


def objects(node, path="$"):
    """Every object schema inside a JSON schema, with where it was found."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node or "additionalProperties" in node:
            yield path, node
        for key, value in node.items():
            yield from objects(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from objects(value, f"{path}[{i}]")


@pytest.mark.parametrize("model", [ReplyReading, Proposal], ids=lambda m: m.__name__)
def test_strict_schema(model):
    schema = model.model_json_schema()
    found = list(objects(schema))
    assert found, "no objects at all?"
    for path, obj in found:
        assert obj.get("additionalProperties") is False, path
        assert obj.get("properties"), f"{path}: an object with no listed fields"
        assert set(obj.get("required", [])) == set(obj["properties"]), f"{path}: every field must be required"
