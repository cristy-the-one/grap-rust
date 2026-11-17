"""P-16: invoke_model and procedures/generate-test-spec.pg.yaml.

§5 and §11 of docs/spec-procedure-runtime.md. The model fills the §5
test specification. ``level`` is copied from the template and a value in
the model reply does not replace it. The procedure file loads under the
P-10 / P-10a checks as they stand. One stub run walks select, fetch,
template, compose, model, validate, and emit. A second stub answers a
draft that fails validation, then one that passes, and the run reports
rounds = 1.

No automotive corpus is chosen (P-17). These tests build one requirement
in the test itself. They do not read gold/ or real-data-law-ro/.
"""

import hashlib
import json
from pathlib import Path

import pygrap
import pytest

from grap_mcp.kee.llm import StubProvider
from grap_mcp.procedure_model import (
    DRAFT_SCHEMA,
    PROCEDURE_PATH,
    ModelError,
    invoke_model,
    run_generate_test_spec,
)
from grap_mcp.procedure_runtime import load_procedure_file
from grap_mcp.readers import SourceItem
from grap_mcp.schema.project import project_items

_CASE_REQUIRED = (
    "id",
    "verifies",
    "preconditions",
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
    "kind",
    "uses",
)
_TEMPLATE = {
    "id": "default",
    "name": "Default test specification",
    "level": "software",
}


def _case(**overrides):
    case = {
        "id": "TC-1",
        "verifies": ["REQ-1"],
        "preconditions": "power on",
        "stimulus": "press",
        "expected_observable_result": "lamp off",
        "pass_criterion": "lamp is off",
        "kind": "positive",
        "uses": [],
    }
    case.update(overrides)
    return case


def _reply(**case_overrides):
    """A model object that also sets level. The template must win."""
    return {
        "title": "Lamp",
        "level": "system",
        "cases": [_case(level="hardware", **case_overrides)],
    }


def _item():
    return SourceItem(
        source_id="fix",
        release=None,
        item_id="REQ-1",
        kind_hint="requirement",
        text="The lamp shall be off.",
        heading_path=[],
        attributes={},
        links=[],
        file="fix.txt",
        sha256="0" * 64,
    )


def _graph(tmp_path, capsys):
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = project_items([_item()], kg)
    capsys.readouterr()
    return kg, projection


def _ws(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    return pygrap.Workspace.open(str(root)), root


def _provider(*payloads):
    return StubProvider(responses=[json.dumps(payload) for payload in payloads])


class _SchemaProvider:
    """Records the schema handed to with_json_schema and answers once."""

    name = "stub"

    def __init__(self, payload):
        self.payload = payload
        self.schemas = []
        self.calls = []

    def with_json_schema(self, schema):
        self.schemas.append(schema)
        return self

    def complete(self, system, user):
        self.calls.append((system, user))
        return json.dumps(self.payload)


def test_draft_schema_is_the_section_5_object_and_level_is_not_a_model_field():
    """§5. The model fills title and the case fields. It is not asked for level."""
    assert DRAFT_SCHEMA["type"] == "object"
    assert DRAFT_SCHEMA["additionalProperties"] is False
    assert DRAFT_SCHEMA["required"] == ["title", "cases"]
    assert set(DRAFT_SCHEMA["properties"]) == {"title", "cases"}
    assert "level" not in DRAFT_SCHEMA["properties"]
    case = DRAFT_SCHEMA["$defs"]["case"]
    assert DRAFT_SCHEMA["properties"]["cases"]["items"] == {"$ref": "#/$defs/case"}
    assert case["additionalProperties"] is False
    assert tuple(case["required"]) == _CASE_REQUIRED
    assert "level" not in case["properties"]
    assert "variant" in case["properties"]
    assert "variant" not in case["required"]
    assert case["properties"]["kind"]["enum"] == ["positive", "negative"]
    for name in ("verifies", "uses"):
        assert case["properties"][name] == {
            "type": "array",
            "items": {"type": "string"},
        }


def test_a_model_level_does_not_override_the_template():
    """§5a and §10 decision 2. level is copied from the template."""
    provider = _SchemaProvider(_reply())
    draft = invoke_model("write the lamp test", provider, _TEMPLATE)
    assert provider.schemas == [DRAFT_SCHEMA]
    assert len(provider.calls) == 1
    assert provider.calls[0][1] == "write the lamp test"
    assert draft["level"] == "software"
    assert draft["cases"][0]["level"] == "software"
    assert draft["title"] == "Lamp"
    assert draft["cases"][0]["verifies"] == ["REQ-1"]
    dumped = json.dumps(draft)
    assert "system" not in dumped
    assert "hardware" not in dumped

    varied = _reply(variant="left")
    kept = _SchemaProvider(varied)
    assert invoke_model("again", kept, _TEMPLATE)["cases"][0]["variant"] == "left"


def test_a_reply_that_is_not_the_schema_is_refused_after_one_call():
    raw = json.dumps({
        "title": "Lamp",
        "cases": [{**_case(), "priority": "high"}],
    })
    provider = StubProvider(responses=[raw])
    with pytest.raises(ModelError, match="priority"):
        invoke_model("write a test", provider, _TEMPLATE)
    assert len(provider.calls) == 1

    broken = StubProvider(responses=["not json"])
    with pytest.raises(ModelError, match="JSON"):
        invoke_model("write a test", broken, _TEMPLATE)
    assert len(broken.calls) == 1


def test_procedure_file_loads_under_the_existing_checks():
    """§11. P-10 / P-10a are unchanged: load_procedure_file accepts the file."""
    procedure = load_procedure_file(PROCEDURE_PATH)
    assert procedure.name == "generate-test-spec"
    assert procedure.version == "0.1"
    assert Path(procedure.source_path) == PROCEDURE_PATH
    assert [step.operation for step in procedure.steps] == [
        "select_requirements",
        "fetch_context",
        "choose_template",
        "compose_instructions",
        "invoke_model",
        "validate_output",
        "refine",
        "emit",
    ]
    assert procedure.steps[0].inputs["scope"] == {"source": "corpus"}
    assert procedure.steps[2].inputs["template_id"] == "default"
    assert "issues" not in procedure.steps[3].inputs
    revise = procedure.steps[6]
    assert revise.max_rounds == 2
    assert revise.guards[0].ref == "check.outputs.passed"
    assert revise.guards[0].op == "eq"
    assert revise.guards[0].value is False
    assert procedure.steps[7].inputs["passed"] == "check.outputs.passed"


def test_stub_run_selects_fetches_composes_validates_and_emits(tmp_path, capsys):
    """One stub walk: select, fetch, template, compose, model, validate, emit."""
    kg, projection = _graph(tmp_path, capsys)
    workspace, root = _ws(tmp_path, "ws")
    store = tmp_path / "store"
    result = run_generate_test_spec(
        kg, projection, workspace, root, store,
        run_id="run-ok",
        scope={"ids": ["REQ-1"]},
        provider=_provider(_reply()),
    )
    assert result.recorded.status == "ok"
    assert result.recorded.rounds == 0
    assert result.recorded.replayable is True
    ran = [
        event["operation"]
        for event in result.recorded.events
        if event["event_type"] == "procedure_step"
    ]
    assert ran == [
        "select_requirements",
        "fetch_context",
        "choose_template",
        "compose_instructions",
        "invoke_model",
        "validate_output",
        "emit",
    ]
    skipped = [
        event for event in result.recorded.events
        if event["event_type"] == "procedure_step_skipped"
    ]
    assert [event["operation"] for event in skipped] == ["refine"]
    assert len(result.drafts) == 1
    assert result.drafts[0]["level"] == "software"
    assert result.drafts[0]["cases"][0]["level"] == "software"
    assert result.calls[0]["model"] == "stub"
    assert result.calls[0]["duration_ms"] == "n/a"
    assert result.calls[0]["token_counts"] == "n/a"
    assert result.calls[0]["n_cases"] == 1
    model = next(
        event for event in result.recorded.events
        if event["operation"] == "invoke_model"
    )
    assert model["model"] == "stub"
    assert model["duration_ms"] == "n/a"
    assert model["token_counts"] == "n/a"
    assert model["prompt_hash"] == result.calls[0]["prompt_hash"]
    assert model["prompt_hash"] == hashlib.sha256(
        result.calls[0]["instructions"].encode("utf-8")
    ).hexdigest()
    stored = json.loads(next(path.read_text() for path in store.glob("*.json")))
    assert stored["level"] == "software"
    assert stored["cases"][0]["level"] == "software"
    assert stored["cases"][0]["verifies"] == ["REQ-1"]
    assert stored["title"] == "Lamp"
    rendered = next(path.read_text() for path in store.glob("*.md"))
    assert "## TC-1 (positive)" in rendered
    assert "Verifies: REQ-1" in rendered
    printed = capsys.readouterr().out
    assert "template id=default level=software n_requirements=1 n_docs=1" in printed
    assert "instructions n_requirements=1 n_context=0 n_docs=1" in printed
    assert (
        "invoke_model model=stub n_cases=1 n_docs=n/a "
        "duration_ms=n/a token_counts=n/a"
    ) in printed
    assert "passed=true n_issues=0 n_cases=1 n_requirements=1 n_docs=1" in printed
    assert "invoke_model n_calls=1 model=stub duration_ms=n/a token_counts=n/a" in printed


def test_first_draft_fails_validation_and_the_second_passes_in_one_round(tmp_path, capsys):
    """The stub's first draft fails validation. The second passes. rounds = 1.

    §6's limit stays 2. This run uses one of those rounds.
    """
    kg, projection = _graph(tmp_path, capsys)
    workspace, root = _ws(tmp_path, "ws")
    store = tmp_path / "store"
    failing = _reply(stimulus="")
    passing = _reply()
    result = run_generate_test_spec(
        kg, projection, workspace, root, store,
        run_id="run-retry",
        scope={"ids": ["REQ-1"]},
        provider=_provider(failing, passing),
    )
    assert result.recorded.status == "ok"
    assert result.recorded.rounds == 1
    assert len(result.drafts) == 2
    assert len(result.calls) == 2
    assert result.drafts[0]["cases"][0]["stimulus"] == ""
    assert result.drafts[1]["cases"][0]["stimulus"] == "press"
    assert result.drafts[0]["level"] == "software"
    assert result.drafts[1]["level"] == "software"
    assert result.drafts[0]["cases"][0]["level"] == "software"
    assert result.drafts[1]["cases"][0]["level"] == "software"
    failed = [
        event for event in result.recorded.events
        if event["event_type"] == "procedure_check_failed"
    ]
    assert len(failed) == 1
    assert failed[0]["check"] == "completeness"
    assert failed[0]["value"] == "stimulus"
    assert failed[0]["case"] == "TC-1"
    assert failed[0]["iteration"] == 1
    refine = [
        event for event in result.recorded.events
        if event["event_type"] == "procedure_refine_round"
    ]
    assert len(refine) == 1
    assert refine[0]["iteration"] == 1
    emits = [
        event for event in result.recorded.events
        if event["operation"] == "emit" and event["event_type"] == "procedure_step"
    ]
    assert len(emits) == 1
    assert emits[0]["iteration"] == 2
    stored = json.loads(next(path.read_text() for path in store.glob("*.json")))
    assert stored["cases"][0]["stimulus"] == "press"
    assert stored["level"] == "software"
    assert stored["cases"][0]["level"] == "software"
    printed = capsys.readouterr().out
    assert printed.count(
        "invoke_model model=stub n_cases=1 n_docs=n/a duration_ms=n/a token_counts=n/a"
    ) == 2
    assert "invoke_model n_calls=2 model=stub duration_ms=n/a token_counts=n/a" in printed
    assert "passed=false" in printed
    assert "passed=true" in printed
