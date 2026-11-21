"""verifies items are the scope requirement ids when context is known.

Synthetic requirement ids only. A recording provider keeps the schema it
was given. A stub has no with_json_schema.
"""

import json
from pathlib import Path

import pygrap

from grap_mcp.kee.llm import StubProvider
from grap_mcp.procedure_context import select_requirements
from grap_mcp.procedure_model import (
    DRAFT_SCHEMA,
    ModelOperations,
    _Operations,
    invoke_model,
)
from grap_mcp.readers import SourceItem
from grap_mcp.schema.project import project_items

_TEMPLATE = {
    "id": "default",
    "name": "Default test specification",
    "level": "software",
}


def _item(item_id):
    return SourceItem(
        source_id="made-up",
        release=None,
        item_id=item_id,
        kind_hint="requirement",
        text="The lamp shall be off.",
        heading_path=[],
        attributes={},
        links=[],
        file="made-up.txt",
        sha256="0" * 64,
    )


def _draft():
    return {
        "title": "Lamp",
        "cases": [{
            "id": "TC-1",
            "verifies": ["REQ-A"],
            "preconditions": "power on",
            "stimulus": "press",
            "expected_observable_result": "lamp off",
            "pass_criterion": "lamp is off",
            "kind": "positive",
            "uses": [],
            "variant": "",
            "checks": [],
        }],
    }


def _graph(tmp_path: Path):
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = project_items([_item("REQ-B"), _item("REQ-A")], kg)
    return kg, projection


class _RecordingProvider:
    """Records the schema handed to with_json_schema and answers once."""

    name = "stub"

    def __init__(self, payload):
        self.payload = payload
        self.schemas = []

    def with_json_schema(self, schema):
        self.schemas.append(schema)
        return self

    def complete(self, system, user):
        return json.dumps(self.payload)


def _fetch_then_invoke(tmp_path, provider):
    kg, projection = _graph(tmp_path)
    requirements = select_requirements(kg, projection, {"ids": ["REQ-B", "REQ-A"]})
    ops = _Operations(kg, projection, tmp_path / "store", provider)
    fetched = ops("fetch_context", {"requirements": requirements})
    ops.model.template = _TEMPLATE
    result = ops("invoke_model", {"instructions": "write a test"})
    return fetched, result


def test_fetch_context_limits_verifies_to_scope_requirement_ids(tmp_path):
    """Groups REQ-B then REQ-A. The schema enum is those ids, sorted."""
    provider = _RecordingProvider(_draft())
    fetched, _result = _fetch_then_invoke(tmp_path, provider)
    assert [group["item_id"] for group in fetched["context"]["requirements"]] == [
        "REQ-B",
        "REQ-A",
    ]
    assert provider.schemas[0]["$defs"]["case"]["properties"]["verifies"]["items"] == {
        "type": "string",
        "enum": ["REQ-A", "REQ-B"],
    }


def test_invoke_model_without_fetch_context_keeps_draft_schema():
    """No fetch_context. The recorded schema is DRAFT_SCHEMA."""
    provider = _RecordingProvider(_draft())
    invoke_model("write a test", provider, _TEMPLATE)
    assert provider.schemas == [DRAFT_SCHEMA]

    model = ModelOperations(_RecordingProvider(_draft()), _TEMPLATE)
    model("invoke_model", {"instructions": "write a test"})
    assert model.provider.schemas == [DRAFT_SCHEMA]


def test_draft_schema_verifies_items_stay_a_string_after_a_call_with_ids(tmp_path):
    """A call that knows the ids does not change DRAFT_SCHEMA."""
    provider = _RecordingProvider(_draft())
    _fetch_then_invoke(tmp_path, provider)
    assert DRAFT_SCHEMA["$defs"]["case"]["properties"]["verifies"]["items"] == {
        "type": "string",
    }


def test_a_provider_without_json_schema_still_returns_the_draft(tmp_path):
    """A stub has no with_json_schema. The draft still comes back."""
    provider = StubProvider(responses=[json.dumps(_draft())])
    _fetched, result = _fetch_then_invoke(tmp_path, provider)
    assert result["draft"]["title"] == "Lamp"
    assert result["draft"]["cases"][0]["id"] == "TC-1"
    assert result["draft"]["cases"][0]["verifies"] == ["REQ-A"]
