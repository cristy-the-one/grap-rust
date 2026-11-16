"""P-16: invoke_model, the procedure file, and an end-to-end stub run.

The model fills the §5 JSON object. level is not in that object; the
template copies it. procedures/generate-test-spec.pg.yaml is the
procedure. Two stub runs write the same artifact and the same events.

No automotive corpus is chosen (P-17). The live check uses the repo's
ARXML fixtures. Those fixtures are not requirements, and this test says
so with the counts the run prints.
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
    SYSTEM,
    ModelError,
    ModelOperations,
    invoke_model,
    run_generate_test_spec,
)
from grap_mcp.procedure_runtime import load_procedure_file
from grap_mcp.readers import SourceItem
from grap_mcp.readers.arxml import read_arxml
from grap_mcp.schema.project import project_items

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arxml"
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
_PASSING = {
    "title": "Lamp",
    "cases": [
        {
            "id": "TC-1",
            "verifies": ["REQ-1"],
            "preconditions": "power on",
            "stimulus": "press",
            "expected_observable_result": "lamp off",
            "pass_criterion": "lamp is off",
            "kind": "positive",
            "uses": [],
        }
    ],
}
_EMPTY = {"title": "", "cases": []}


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


def _provider(payload):
    return StubProvider(responses=[json.dumps(payload)])


def _files(store):
    return {path.name: path.read_bytes() for path in sorted(Path(store).iterdir())}


def _without_run(events):
    return [{key: value for key, value in event.items() if key != "run_id"} for event in events]


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


class _ClockProvider:
    """A named provider with no token usage."""

    name = "openai-compat"
    model = "local-model"

    def __init__(self, payload, tokens=None):
        self.payload = payload
        self.token_counts = tokens
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return json.dumps(self.payload)


def test_draft_schema_is_the_section_5_object():
    """§5 and decision 2. The model fills title and cases. It does not set level."""
    assert DRAFT_SCHEMA["type"] == "object"
    assert DRAFT_SCHEMA["additionalProperties"] is False
    assert DRAFT_SCHEMA["required"] == ["title", "cases"]
    assert set(DRAFT_SCHEMA["properties"]) == {"title", "cases"}
    assert DRAFT_SCHEMA["properties"]["title"]["type"] == "string"
    cases = DRAFT_SCHEMA["properties"]["cases"]
    assert cases["type"] == "array"
    case = DRAFT_SCHEMA["$defs"]["case"]
    assert cases["items"] == {"$ref": "#/$defs/case"}
    assert case["type"] == "object"
    assert case["additionalProperties"] is False
    assert tuple(case["required"]) == _CASE_REQUIRED
    assert "level" not in case["properties"]
    assert "variant" in case["properties"]
    assert "variant" not in case["required"]
    assert case["properties"]["kind"] == {
        "type": "string",
        "enum": ["positive", "negative"],
    }
    for name in ("verifies", "uses"):
        assert case["properties"][name] == {"type": "array", "items": {"type": "string"}}
    for name in (
        "id",
        "preconditions",
        "stimulus",
        "expected_observable_result",
        "pass_criterion",
        "variant",
    ):
        assert case["properties"][name] == {"type": "string"}
    assert "Do not set level" in SYSTEM


def test_invoke_model_is_one_call_with_the_schema():
    provider = _SchemaProvider(_PASSING)
    draft = invoke_model("write the lamp test", provider)
    assert provider.schemas == [DRAFT_SCHEMA]
    assert provider.calls == [(SYSTEM, "write the lamp test")]
    assert draft == _PASSING
    assert "level" not in draft
    assert "level" not in draft["cases"][0]

    omitted = dict(_PASSING["cases"][0])
    assert "variant" not in omitted
    bare = _SchemaProvider({"title": "Lamp", "cases": [omitted]})
    assert "variant" not in invoke_model("again", bare)["cases"][0]

    varied = dict(omitted)
    varied["variant"] = "left"
    kept = _SchemaProvider({"title": "Lamp", "cases": [varied]})
    assert invoke_model("third", kept)["cases"][0]["variant"] == "left"


def test_a_reply_that_is_not_the_schema_is_refused_after_one_call():
    samples = [
        ("not json", "JSON"),
        (json.dumps({"title": "", "cases": [], "level": "software"}), "level"),
        (json.dumps(["TC-1"]), "object"),
        (
            json.dumps({
                "title": "Lamp",
                "cases": [{**_PASSING["cases"][0], "level": "software"}],
            }),
            "level",
        ),
        (
            json.dumps({
                "title": "Lamp",
                "cases": [
                    {key: value for key, value in _PASSING["cases"][0].items() if key != "stimulus"}
                ],
            }),
            "stimulus",
        ),
        (
            json.dumps({
                "title": "Lamp",
                "cases": [{**_PASSING["cases"][0], "kind": "Positive"}],
            }),
            "kind",
        ),
        (
            json.dumps({
                "title": "Lamp",
                "cases": [{**_PASSING["cases"][0], "verifies": "REQ-1"}],
            }),
            "verifies",
        ),
    ]
    for raw, needle in samples:
        provider = StubProvider(responses=[raw])
        with pytest.raises(ModelError, match=needle):
            invoke_model("write a test", provider)
        assert len(provider.calls) == 1

    silent = StubProvider(default="{}")
    with pytest.raises(ModelError, match="instructions"):
        invoke_model(None, silent)
    assert silent.calls == []


def test_a_stub_prints_n_a_for_tokens_and_duration(capsys):
    """A stub has no token usage. A wall clock would make two runs differ."""
    provider = StubProvider(responses=[json.dumps(_PASSING)])
    ops = ModelOperations(provider)
    result = ops("invoke_model", {"instructions": "write the lamp test"})
    assert set(result) == {"draft"}
    assert result["draft"] == _PASSING
    receipt = ops.calls[0]
    assert receipt["model"] == "stub"
    assert receipt["duration_ms"] == "n/a"
    assert receipt["token_counts"] == "n/a"
    assert receipt["n_cases"] == 1
    assert receipt["prompt_hash"] == hashlib.sha256(b"write the lamp test").hexdigest()
    printed = capsys.readouterr().out
    assert (
        "invoke_model model=stub n_cases=1 n_docs=n/a "
        "duration_ms=n/a token_counts=n/a"
    ) in printed

    clock = _ClockProvider(_EMPTY)
    counted = ModelOperations(clock)
    counted("invoke_model", {"instructions": "empty"})
    measured = counted.calls[0]
    assert measured["model"] == "local-model"
    assert measured["token_counts"] == "n/a"
    assert type(measured["duration_ms"]) is int and measured["duration_ms"] >= 0
    clock.token_counts = {"prompt": 3, "completion": 1}
    reported = ModelOperations(clock)
    reported("invoke_model", {"instructions": "empty"})
    assert reported.calls[0]["token_counts"] == {"completion": 1, "prompt": 3}
    text = capsys.readouterr().out
    assert f"duration_ms={measured['duration_ms']}" in text
    assert "token_counts completion=1 prompt=3" in text
    assert "n_docs=n/a" in text

    with pytest.raises(ModelError, match="invoke_model"):
        ops("validate_output", {"draft": _EMPTY})
    assert len(provider.calls) == 1


def test_procedure_file_is_the_eight_steps():
    procedure = load_procedure_file(PROCEDURE_PATH)
    assert procedure.name == "generate-test-spec"
    assert procedure.version == "0.1"
    assert Path(procedure.source_path) == PROCEDURE_PATH
    assert [step.id for step in procedure.steps] == [
        "select",
        "context",
        "template",
        "compose",
        "model",
        "check",
        "revise",
        "publish",
    ]
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
    assert len(revise.guards) == 1
    assert revise.guards[0].ref == "check.outputs.passed"
    assert revise.guards[0].op == "eq"
    assert revise.guards[0].value is False
    publish = procedure.steps[7]
    assert publish.guards == ()
    assert publish.inputs["passed"] == "check.outputs.passed"


def test_two_stub_runs_write_the_same_artifact_and_the_same_events(tmp_path, capsys):
    """§9 determinism. The stub is not a real model, so the run stays replayable."""
    kg, projection = _graph(tmp_path, capsys)
    first_ws, first_root = _ws(tmp_path, "ws-a")
    second_ws, second_root = _ws(tmp_path, "ws-b")
    first_store = tmp_path / "store-a"
    second_store = tmp_path / "store-b"
    scope = {"ids": ["REQ-1"]}
    first = run_generate_test_spec(
        kg, projection, first_ws, first_root, first_store,
        run_id="run-a", scope=scope, provider=_provider(_PASSING),
    )
    second = run_generate_test_spec(
        kg, projection, second_ws, second_root, second_store,
        run_id="run-b", scope=scope, provider=_provider(_PASSING),
    )
    assert first.recorded.status == second.recorded.status == "ok"
    assert first.recorded.rounds == second.recorded.rounds == 0
    assert first.recorded.replayable is True
    assert first.recorded.replay_note == (
        "replayable only as far as the stored evidence snapshot and a deterministic model stub"
    )
    assert [event["step_id"] for event in first.recorded.events] == [
        "select", "context", "template", "compose", "model", "check", "revise", "publish",
    ]
    assert first.recorded.events[6]["event_type"] == "procedure_step_skipped"
    assert _without_run(first.recorded.events) == _without_run(second.recorded.events)
    model = next(
        event for event in first.recorded.events
        if event["step_id"] == "model"
    )
    assert model["model"] == "stub"
    assert model["duration_ms"] == "n/a"
    assert model["token_counts"] == "n/a"
    assert model["prompt_hash"] == first.calls[0]["prompt_hash"]
    assert model["prompt_hash"] == second.calls[0]["prompt_hash"]
    assert len(first.calls) == len(second.calls) == 1
    assert first.last_draft == second.last_draft == _PASSING
    files = _files(first_store)
    assert files == _files(second_store)
    assert sorted(files) == sorted(
        name for name in files if name.endswith(".json") or name.endswith(".md")
    )
    assert len(files) == 2
    stored = json.loads(next(body for name, body in files.items() if name.endswith(".json")))
    assert stored["title"] == "Lamp"
    assert stored["level"] == "software"
    assert stored["cases"][0]["level"] == "software"
    assert stored["cases"][0]["verifies"] == ["REQ-1"]
    assert "variant" not in stored["cases"][0]
    rendered = next(body.decode() for name, body in files.items() if name.endswith(".md"))
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
    assert "n_cases=1 n_docs=n/a" in printed
    assert "invoke_model n_calls=1 model=stub duration_ms=n/a token_counts=n/a" in printed


def test_a_stub_that_fails_validation_emits_nothing(tmp_path, capsys):
    """The empty stub does not invent a case. The run fails and stores nothing."""
    kg, projection = _graph(tmp_path, capsys)
    ws, root = _ws(tmp_path, "ws")
    store = tmp_path / "store"
    result = run_generate_test_spec(
        kg, projection, ws, root, store,
        run_id="run-fail",
        scope={"ids": ["REQ-1"]},
        provider=_provider(_EMPTY),
    )
    assert result.recorded.status == "failed-validation"
    assert result.recorded.rounds == 2
    assert result.last_draft == _EMPTY
    assert len(result.calls) == 3
    assert all(call["model"] == "stub" and call["n_cases"] == 0 for call in result.calls)
    assert not store.exists()
    assert not any(event["step_id"] == "publish" for event in result.recorded.events)
    assert result.recorded.replayable is True
    printed = capsys.readouterr().out
    assert printed.count(
        "invoke_model model=stub n_cases=0 n_docs=n/a duration_ms=n/a token_counts=n/a"
    ) == 3
    assert "invoke_model n_calls=3 model=stub duration_ms=n/a token_counts=n/a" in printed


def test_fixture_run_has_no_requirements_and_reports_the_alarm(tmp_path, capsys):
    """The two ARXML fixtures share one source id. They are not a requirements corpus.

    The projector's fallback_links alarm is pre-existing. This test records
    the counts it prints and does not change the projector.
    """
    items, _report = read_arxml(
        [_FIXTURES / "system-4.2.arxml", _FIXTURES / "ecu-extract-4.2.arxml"],
        source_id="system-and-extract",
    )
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = project_items(items, kg)
    requirements = [node for node in projection["nodes"] if node["type"] == "Requirement"]
    assert requirements == []
    assert projection["alarm"]["fallback_links"] is True
    counts = projection["counts"]
    alarm = (
        f"alarm: fallback_links={counts['fallback_links']}/{counts['links']} "
        f"exceeds 20% (documents={counts['documents']})"
    )
    ws, root = _ws(tmp_path, "ws")
    store = tmp_path / "store"
    result = run_generate_test_spec(
        kg, projection, ws, root, store,
        run_id="run-fixtures",
        scope={"source": "system-and-extract"},
        provider=_provider(_EMPTY),
    )
    assert result.recorded.status == "ok"
    assert result.recorded.rounds == 0
    assert result.last_draft == _EMPTY
    assert len(result.calls) == 1
    stored = json.loads(next(path.read_text() for path in store.glob("*.json")))
    assert stored["cases"] == []
    assert stored["level"] == "software"
    printed = capsys.readouterr().out
    assert alarm in printed
    assert "instructions=n/a n_requirements=0" in printed
    assert (
        "invoke_model model=stub n_cases=0 n_docs=n/a duration_ms=n/a token_counts=n/a"
    ) in printed
    assert "artifact=n/a" in printed
    assert "n_cases=0" in printed
