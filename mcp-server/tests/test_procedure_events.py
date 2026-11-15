"""P-14: events and the evidence snapshot (§8).

A tracked run writes one event per step. The event carries the step id,
the operation, the procedure version, and the hashes of that step's inputs
and outputs, and it links every graph node the step read. A skipped step,
a failed check, and each refine round are events. ``invoke_model`` also
records the model, the prompt hash, token counts, and duration.

``fetch_context`` stores the node keys it returned with each node's content
hash. Changing a node afterwards leaves those recorded hashes as they were,
and the difference is detectable. A real model is not replayable; the
record says a second run can differ. Counts are printed with denominators.
Nothing to measure is ``n/a``.
"""

import hashlib
import inspect
import json

import pygrap
import pytest

from grap_mcp.procedure_context import (
    GraphOperations,
    fetch_context,
    select_requirements,
)
from grap_mcp.procedure_events import (
    evidence_diff,
    format_diff,
    format_evidence,
    load_recorded,
    run_procedure,
    sha256_canonical,
)
from grap_mcp.procedure_interpreter import DEFAULT_MAX_ROUNDS, StubOperations
from grap_mcp.procedure_runtime import load_procedure
from grap_mcp.readers import SourceItem, SourceLink
from grap_mcp.schema.project import project_items


def _pg(body: str):
    return load_procedure(inspect.cleandoc(body) + "\n")


def _link(kind, target):
    return SourceLink(type=kind, target_id=target, role="", context=[])


def _item(item_id, links=(), *, kind_hint="text", attributes=None):
    return SourceItem(
        source_id="fix",
        release=None,
        item_id=item_id,
        kind_hint=kind_hint,
        text=item_id,
        heading_path=[],
        attributes={} if attributes is None else dict(attributes),
        links=list(links),
        file="fix.txt",
        sha256="0" * 64,
    )


def _graph(tmp_path):
    """REQ-LOWER's statement reaches four nodes within two steps.

    SIG and NOTE are one step out. PDU and SCALE are two. The frame FR
    is the third step and is not part of the context. REQ-BARE has no
    statement.
    """
    items = [
        _item(
            "REQ-LOWER",
            [_link("has_statement", "ST-L")],
            kind_hint="requirement",
        ),
        _item("REQ-BARE", kind_hint="requirement"),
        _item(
            "ST-L",
            [_link("about", "SIG"), _link("refers_to", "NOTE")],
            kind_hint="statement",
        ),
        _item(
            "SIG",
            [_link("part_of", "PDU"), _link("has_type", "SCALE")],
            attributes={"tag": "I-SIGNAL"},
        ),
        _item("PDU", [_link("part_of", "FR")], attributes={"tag": "I-SIGNAL-I-PDU"}),
        _item("FR", attributes={"tag": "CAN-FRAME"}),
        _item("SCALE", attributes={"tag": "COMPU-METHOD"}),
        _item("NOTE"),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    return kg, project_items(items, kg)


def _key(projection, type_name, native_id):
    found = [
        node for node in projection["nodes"]
        if node["type"] == type_name and node["native_id"] == native_id
    ]
    assert len(found) == 1, (type_name, native_id, found)
    return found[0]


def _ws(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    return pygrap.Workspace.open(str(root)), root


def _loop(max_rounds: str = "") -> str:
    lines = [
        "name: generate-test-spec",
        'version: "0.1"',
        "steps:",
        "  - id: select",
        "    operation: select_requirements",
        "    inputs:",
        "      scope:",
        "        - REQ-1",
        "  - id: context",
        "    operation: fetch_context",
        "    inputs:",
        "      requirements: select.outputs.requirements",
        "  - id: template",
        "    operation: choose_template",
        "    inputs:",
        "      requirements: select.outputs.requirements",
        "  - id: compose",
        "    operation: compose_instructions",
        "    inputs:",
        "      requirements: select.outputs.requirements",
        "      context: context.outputs.context",
        "      template: template.outputs.template",
        "  - id: model",
        "    operation: invoke_model",
        "    inputs:",
        "      instructions: compose.outputs.instructions",
        "  - id: check",
        "    operation: validate_output",
        "    inputs:",
        "      draft: model.outputs.draft",
        "      requirements: select.outputs.requirements",
        "      context: context.outputs.context",
        "  - id: revise",
        "    operation: refine",
        "    inputs:",
        "      issues: check.outputs.issues",
    ]
    if max_rounds != "":
        lines.append(f"    max_rounds: {max_rounds}")
    lines.extend([
        "    guards:",
        "      - {ref: check.outputs.passed, op: eq, value: false}",
        "  - id: publish",
        "    operation: emit",
        "    inputs:",
        "      draft: model.outputs.draft",
        "      template: template.outputs.template",
        "      passed: check.outputs.passed",
    ])
    return "\n".join(lines) + "\n"


def _by_id(events, step_id, event_type):
    found = [
        event for event in events
        if event["step_id"] == step_id and event["event_type"] == event_type
    ]
    assert len(found) == 1, (step_id, event_type, events)
    return found[0]


def test_evidence_snapshot_is_stable_after_a_node_changes(tmp_path, capsys):
    """§8 and the §9 evidence-snapshot check.

    The recorded content hashes are what the graph held when
    ``fetch_context`` ran. Updating a node afterwards does not rewrite
    them. The difference is the new hash against the stored one. A node
    the walk did not return is not part of the snapshot.
    """
    kg, projection = _graph(tmp_path)
    capsys.readouterr()
    ws, root = _ws(tmp_path, "ws")
    procedure = _pg(
        """
        name: generate-test-spec
        version: "0.1"
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope:
                ids:
                  - REQ-LOWER
          - id: context
            operation: fetch_context
            inputs:
              requirements: select.outputs.requirements
        """
    )
    requirements = select_requirements(kg, projection, {"ids": ["REQ-LOWER"]})
    context = fetch_context(kg, projection, requirements)
    req_key = requirements[0]["key"]
    context_keys = context["requirements"][0]["nodes"]
    assert len(context_keys) == 4
    frame = _key(projection, "Frame", "FR")
    signal = _key(projection, "Signal", "SIG")
    assert frame["key"] not in context_keys
    before = {
        node["key"]: kg.get_node_content_hash(node["engine_id"])
        for node in projection["nodes"]
        if node["key"] in context_keys
    }
    assert all(isinstance(value, str) and len(value) == 64 for value in before.values())

    recorded = run_procedure(
        procedure,
        GraphOperations(kg, projection),
        ws,
        root,
        kg=kg,
        projection=projection,
        run_id="run-evidence",
    )
    printed = capsys.readouterr().out
    assert recorded.status == "ok"
    assert recorded.replayable is True
    assert recorded.replay_note == (
        "replayable only as far as the stored evidence snapshot; no model was called"
    )
    assert format_evidence(recorded.evidence) == (
        "evidence n_requirements=1 n_nodes=4 n_hashed=4 n_missing=0"
    )
    assert format_evidence(recorded.evidence) in printed
    assert (
        "procedure_events n_steps=2 n_skipped=0 n_refine_rounds=0 "
        "n_failed_checks=0 n_node_links=6"
    ) in printed
    assert "invoke_model=n/a n_calls=0 model=n/a duration_ms=n/a token_counts=n/a" in printed
    assert f"replayable=true note={recorded.replay_note}" in printed

    select_event = _by_id(recorded.events, "select", "procedure_step")
    fetch_event = _by_id(recorded.events, "context", "procedure_step")
    assert select_event["procedure_version"] == "0.1"
    assert fetch_event["procedure_version"] == "0.1"
    assert select_event["operation"] == "select_requirements"
    assert fetch_event["operation"] == "fetch_context"
    assert select_event["input_hash"] == sha256_canonical({"scope": {"ids": ["REQ-LOWER"]}})
    assert select_event["output_hash"] == sha256_canonical({"requirements": requirements})
    assert fetch_event["input_hash"] == sha256_canonical({"requirements": requirements})
    assert fetch_event["output_hash"] == sha256_canonical({"context": context})
    assert select_event["links"] == [
        {"relation": "read", "kind": "kg_node", "id": req_key},
    ]
    assert [link["id"] for link in fetch_event["links"]] == sorted([req_key, *context_keys])
    assert {link["relation"] for link in fetch_event["links"]} == {"read"}
    assert {link["kind"] for link in fetch_event["links"]} == {"kg_node"}

    group = recorded.evidence["requirements"][0]
    assert group["key"] == req_key
    assert [entry["key"] for entry in group["nodes"]] == context_keys
    assert [entry["key"] for entry in recorded.evidence["nodes"]] == context_keys
    for entry in recorded.evidence["nodes"]:
        assert entry["content_hash"] == before[entry["key"]]
    assert frame["key"] not in {entry["key"] for entry in recorded.evidence["nodes"]}

    raw_before = {
        digest: ws.get_record(digest) for digest in recorded.explanation_hashes
    }
    kg.update_node(signal["engine_id"], "SIG changed after the run")
    raw_after = {
        digest: ws.get_record(digest) for digest in recorded.explanation_hashes
    }
    assert raw_after == raw_before

    reloaded = load_recorded(ws, "run-evidence")
    assert reloaded.evidence["nodes"] == recorded.evidence["nodes"]
    for entry in reloaded.evidence["nodes"]:
        assert entry["content_hash"] == before[entry["key"]]
    diff = evidence_diff(kg, projection, reloaded.evidence)
    assert diff["n_recorded"] == 4
    assert diff["n_changed"] == 1
    assert diff["n_unchanged"] == 3
    assert diff["n_missing"] == 0
    assert diff["changed"] == [{
        "key": signal["key"],
        "recorded": before[signal["key"]],
        "current": kg.get_node_content_hash(signal["engine_id"]),
    }]
    assert diff["changed"][0]["current"] != diff["changed"][0]["recorded"]
    assert format_diff(diff) == (
        "evidence_diff n_recorded=4 n_changed=1 n_unchanged=3 n_missing=0"
    )
    assert format_diff(diff) in capsys.readouterr().out

    audit_types = [event.event_type for event in ws.events_for_run("run-evidence")]
    assert audit_types == [
        "run_started", "procedure_step", "procedure_step", "procedure_run", "run_completed",
    ]
    signal_events = ws.events_linked_to("kg_node", signal["key"])
    assert len(signal_events) == 1
    assert signal_events[0].subject == "run-evidence"
    assert ("read", "kg_node", signal["key"]) in signal_events[0].links
    requirement_events = [
        event for event in ws.events_linked_to("kg_node", req_key)
        if event.subject == "run-evidence"
    ]
    assert len(requirement_events) == 2
    assert ws.events_linked_to("kg_node", frame["key"]) == []
    stored = ws.get_run("run-evidence")
    assert stored.kind == "experiment"
    assert stored.status == "completed"
    assert list(stored.explanation_hashes) == list(recorded.explanation_hashes)


def test_empty_context_and_a_graphless_run_print_na(tmp_path, capsys):
    """A requirement with no statements stores an empty snapshot.

    Hashes are ``n/a`` because there is nothing to hash. A run that never
    reads the graph says so, and does not print a zero score.
    """
    kg, projection = _graph(tmp_path)
    capsys.readouterr()
    ws, root = _ws(tmp_path, "ws")
    empty = run_procedure(
        _pg(
            """
            name: generate-test-spec
            version: "0.1"
            steps:
              - id: select
                operation: select_requirements
                inputs:
                  scope:
                    ids:
                      - REQ-BARE
              - id: context
                operation: fetch_context
                inputs:
                  requirements: select.outputs.requirements
            """
        ),
        GraphOperations(kg, projection),
        ws,
        root,
        kg=kg,
        projection=projection,
        run_id="run-empty",
    )
    assert empty.evidence["n_requirements"] == 1
    assert empty.evidence["n_nodes"] == 0
    assert empty.evidence["n_hashed"] == "n/a"
    assert empty.evidence["n_missing"] == "n/a"
    assert empty.evidence["nodes"] == []
    assert empty.replayable is True
    assert empty.replay_note == (
        "replayable only as far as the stored evidence snapshot; no model was called"
    )
    assert format_evidence(empty.evidence) == (
        "evidence=n/a n_requirements=1 n_nodes=0 n_hashed=n/a n_missing=n/a"
    )
    diff = evidence_diff(kg, projection, empty.evidence)
    assert diff["n_recorded"] == 0
    assert diff["n_changed"] == "n/a"
    assert diff["n_unchanged"] == "n/a"
    assert diff["n_missing"] == "n/a"
    assert diff["changed"] == []
    assert format_diff(diff) == (
        "evidence_diff=n/a n_recorded=0 n_changed=n/a n_unchanged=n/a n_missing=n/a"
    )
    bare = _by_id(empty.events, "context", "procedure_step")
    assert bare["output_hash"] != "n/a"
    assert len(bare["output_hash"]) == 64

    graphless = run_procedure(
        _pg(
            """
            name: generate-test-spec
            version: "0.1"
            steps:
              - id: select
                operation: select_requirements
                inputs:
                  scope:
                    - REQ-1
            """
        ),
        StubOperations(),
        ws,
        root,
        run_id="run-graphless",
    )
    assert graphless.evidence is None
    assert graphless.replayable is True
    assert graphless.replay_note == (
        "replayable only as far as the deterministic record; "
        "no model was called and no evidence snapshot was stored"
    )
    assert format_evidence(None) == (
        "evidence=n/a n_requirements=0 n_nodes=0 n_hashed=n/a n_missing=n/a"
    )
    printed = capsys.readouterr().out
    assert format_evidence(empty.evidence) in printed
    assert format_evidence(None) in printed
    assert f"replayable=true note={graphless.replay_note}" in printed


def test_skipped_step_failed_checks_and_refine_rounds_are_events(tmp_path, capsys):
    """A passing run skips refine. A failing stub records each round.

    The skipped step has no input hash and no output hash. Each failed
    check is its own event. Each refine execution is a refine-round
    event. Emit does not run once validation has failed, so it has no
    event. The same stub twice hashes the same inputs.
    """
    ws, root = _ws(tmp_path, "ws")
    procedure = load_procedure(_loop())
    first_ops = StubOperations()
    first = run_procedure(
        procedure, first_ops, ws, root, run_id="run-pass",
    )
    second_ops = StubOperations()
    second = run_procedure(
        procedure, second_ops, ws, root, run_id="run-pass-2",
    )
    assert first.status == "ok"
    assert [event["event_type"] for event in first.events] == [
        "procedure_step",
        "procedure_step",
        "procedure_step",
        "procedure_step",
        "procedure_step",
        "procedure_step",
        "procedure_step_skipped",
        "procedure_step",
    ]
    skipped = _by_id(first.events, "revise", "procedure_step_skipped")
    assert skipped["operation"] == "refine"
    assert skipped["procedure_version"] == "0.1"
    assert skipped["iteration"] == 1
    assert skipped["reason"] == "guard"
    assert skipped["guard_ref"] == "check.outputs.passed"
    assert skipped["guard_op"] == "eq"
    assert skipped["input_hash"] == "n/a"
    assert skipped["output_hash"] == "n/a"
    assert skipped["input_hash"] != sha256_canonical(None)
    assert skipped["links"] == []
    assert not any(event["event_type"] == "procedure_refine_round" for event in first.events)
    assert not any(event["event_type"] == "procedure_check_failed" for event in first.events)
    assert _by_id(first.events, "publish", "procedure_step")["operation"] == "emit"
    model = _by_id(first.events, "model", "procedure_step")
    instructions = next(
        inputs["instructions"]
        for operation, inputs in first_ops.calls
        if operation == "invoke_model"
    )
    assert isinstance(instructions, str)
    assert model["model"] == "n/a"
    assert model["token_counts"] == "n/a"
    assert model["duration_ms"] == "n/a"
    assert model["prompt_hash"] == hashlib.sha256(instructions.encode("utf-8")).hexdigest()
    assert model["input_hash"] == second.events[4]["input_hash"]
    assert model["prompt_hash"] == _by_id(second.events, "model", "procedure_step")["prompt_hash"]
    assert first.replayable is True
    assert first.replay_note == (
        "replayable only as far as a deterministic model stub; "
        "no evidence snapshot was stored"
    )
    printed = capsys.readouterr().out
    assert (
        "procedure_events n_steps=8 n_skipped=1 n_refine_rounds=0 "
        "n_failed_checks=0 n_node_links=0"
    ) in printed
    assert "invoke_model n_calls=1 model=n/a duration_ms=n/a token_counts=n/a" in printed

    def _fail(_inputs):
        return {"issues": ["missing stimulus"], "passed": False}

    failed = run_procedure(
        procedure,
        StubOperations({"validate_output": _fail}),
        ws,
        root,
        run_id="run-fail",
    )
    assert failed.status == "failed-validation"
    assert failed.rounds == DEFAULT_MAX_ROUNDS
    assert ws.get_run("run-fail").status == "failed"
    step_ids = [
        event["step_id"] for event in failed.events
        if event["event_type"] != "procedure_check_failed"
    ]
    expected = ["select", "context", "template"]
    for _round in range(DEFAULT_MAX_ROUNDS):
        expected.extend(["compose", "model", "check", "revise"])
    expected.extend(["compose", "model", "check"])
    assert step_ids == expected
    assert not any(event["step_id"] == "publish" for event in failed.events)
    failed_checks = [
        event for event in failed.events if event["event_type"] == "procedure_check_failed"
    ]
    assert [event["iteration"] for event in failed_checks] == list(
        range(1, DEFAULT_MAX_ROUNDS + 2)
    )
    for event in failed_checks:
        assert event["step_id"] == "check"
        assert event["operation"] == "validate_output"
        assert event["procedure_version"] == "0.1"
        assert event["check"] == "missing stimulus"
        assert event["case"] == "n/a"
        assert event["value"] == "n/a"
        assert event["input_hash"] == "n/a"
        assert event["output_hash"] == "n/a"
    refine_rounds = [
        event for event in failed.events if event["event_type"] == "procedure_refine_round"
    ]
    assert [event["iteration"] for event in refine_rounds] == list(
        range(1, DEFAULT_MAX_ROUNDS + 1)
    )
    for event in refine_rounds:
        assert event["step_id"] == "revise"
        assert event["operation"] == "refine"
        assert event["input_hash"] != "n/a"
        assert event["output_hash"] != "n/a"
    pairs = list(zip(failed.events, failed.events[1:]))
    assert any(
        left["event_type"] == "procedure_step"
        and left["step_id"] == "check"
        and right["event_type"] == "procedure_check_failed"
        for left, right in pairs
    )
    assert (
        f"procedure_events n_steps={len(expected)} n_skipped=0 "
        f"n_refine_rounds={DEFAULT_MAX_ROUNDS} "
        f"n_failed_checks={DEFAULT_MAX_ROUNDS + 1} n_node_links=0"
    ) in capsys.readouterr().out


def test_a_structured_failure_and_a_real_model_are_recorded(tmp_path, capsys):
    """One issue is one event. A supplied model receipt is stored as given.

    Token counts and duration are not measured when the call does not
    report them. A named model is not replayable, and the record says
    the output can differ on a second run.
    """
    ws, root = _ws(tmp_path, "ws")
    procedure = _pg(
        """
        name: generate-test-spec
        version: "0.2"
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope:
                - REQ-1
          - id: context
            operation: fetch_context
            inputs:
              requirements: select.outputs.requirements
          - id: template
            operation: choose_template
            inputs:
              requirements: select.outputs.requirements
          - id: compose
            operation: compose_instructions
            inputs:
              requirements: select.outputs.requirements
              context: context.outputs.context
              template: template.outputs.template
          - id: model
            operation: invoke_model
            inputs:
              instructions: compose.outputs.instructions
          - id: check
            operation: validate_output
            inputs:
              draft: model.outputs.draft
              requirements: select.outputs.requirements
              context: context.outputs.context
        """
    )

    def _issues(_inputs):
        return {
            "issues": [
                {"case": "TC-1", "check": "coverage", "value": "REQ-9"},
                {"case": "TC-2", "check": "grounding", "value": "SIG-missing"},
            ],
            "passed": False,
        }

    failed = run_procedure(
        procedure,
        StubOperations({"validate_output": _issues}),
        ws,
        root,
        run_id="run-issues",
    )
    assert failed.status == "failed-validation"
    checks = [
        event for event in failed.events if event["event_type"] == "procedure_check_failed"
    ]
    assert [(event["case"], event["check"], event["value"]) for event in checks] == [
        ("TC-1", "coverage", "REQ-9"),
        ("TC-2", "grounding", "SIG-missing"),
    ]
    assert all(event["iteration"] == 1 for event in checks)

    receipt = {
        "model": "local-model",
        "prompt_hash": "supplied-prompt-hash",
        "token_counts": {"prompt": 11, "completion": 7},
        "duration_ms": 42,
    }
    named = run_procedure(
        procedure,
        StubOperations(),
        ws,
        root,
        run_id="run-model",
        model_receipt=receipt,
    )
    model = _by_id(named.events, "model", "procedure_step")
    assert model["model"] == "local-model"
    assert model["prompt_hash"] == "supplied-prompt-hash"
    assert model["token_counts"] == {"prompt": 11, "completion": 7}
    assert model["duration_ms"] == 42
    assert named.replayable is False
    assert named.replay_note == (
        "a real model was called; the output can differ on a second run"
    )
    reloaded = load_recorded(ws, "run-model")
    assert reloaded.replayable is False
    assert reloaded.replay_note == named.replay_note
    assert _by_id(reloaded.events, "model", "procedure_step")["token_counts"] == (
        {"prompt": 11, "completion": 7}
    )
    printed = capsys.readouterr().out
    assert "invoke_model n_calls=1 model=local-model duration_ms=42 " in printed
    assert "token_counts completion=7 prompt=11" in printed
    assert "replayable=false note=a real model was called; the output can differ on a second run" in printed
    assert json.loads(ws.get_record(named.explanation_hashes[-1]))["replayable"] is False
