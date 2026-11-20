"""RUN-04: id resolution before grounding, and a provider error that stays quiet.

Nothing in this file is from a customer. The graph is built in the test.
"""

import json
import sys
from pathlib import Path

import pygrap

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import generate_test_spec  # noqa: E402

from grap_mcp.kee.llm import StubProvider  # noqa: E402
from grap_mcp.kee.working_graph import _relation_properties  # noqa: E402
from grap_mcp.procedure_model import run_generate_test_spec  # noqa: E402
from grap_mcp.readers import SourceItem  # noqa: E402
from grap_mcp.schema.project import project_items  # noqa: E402


_REQUIREMENT = "The lamp shall be off."
_SHARED = "SHARED"
_ABSENT = "NOT-IN-GRAPH"
_LENGTH = (
    "LLM completion is incomplete (finish_reason='length'); "
    "quoted reply MUST-NOT-LEAK"
)


def _item():
    return SourceItem(
        source_id="fix",
        release=None,
        item_id="REQ-1",
        kind_hint="requirement",
        text=_REQUIREMENT,
        heading_path=[],
        attributes={},
        links=[],
        file="fix.txt",
        sha256="0" * 64,
    )


def _case(uses):
    return {
        "id": "TC-1",
        "verifies": ["REQ-1"],
        "preconditions": "power on",
        "stimulus": "press",
        "expected_observable_result": "lamp off",
        "pass_criterion": "lamp is off",
        "variant": "",
        "kind": "positive",
        "uses": uses,
        "checks": [],
    }


def _graph(tmp_path, capsys):
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = project_items([_item()], kg)
    capsys.readouterr()
    return kg, projection


def _edge(kg, source, target, relation):
    props = dict(_relation_properties(relation))
    props["origin"] = "format"
    props["confidence"] = 1.0
    kg.add_edge(source, target, 1.0, props)


def _two_non_signals(kg, projection):
    """Two Element nodes, same native id, both one step from the requirement."""
    requirement = next(
        node for node in projection["nodes"] if node["type"] == "Requirement"
    )
    statement = kg.add_node(text="the lamp is mentioned", label="Statement")
    _edge(kg, requirement["engine_id"], statement, "has_statement")
    nodes = []
    for source in ("left", "right"):
        engine_id = kg.add_node(text=_SHARED, label="Element")
        key = f"Element | {source} | {_SHARED}"
        projection["nodes"].append({
            "key": key,
            "type": "Element",
            "scope": source,
            "native_id": _SHARED,
            "kind": "",
            "label": "Element",
            "engine_id": engine_id,
            "source_id": source,
        })
        _edge(kg, statement, engine_id, "about")
        nodes.append(key)
    return nodes


def _ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return pygrap.Workspace.open(str(root)), root


def _issues(workspace_dir: Path, run_id: str) -> list[tuple]:
    workspace = pygrap.Workspace.open(str(workspace_dir))
    found = []
    for event in workspace.events_for_run(run_id):
        if event.event_type != "procedure_check_failed":
            continue
        digest = next(
            ident for _relation, kind, ident in event.links if kind == "explanation"
        )
        payload = json.loads(workspace.get_record(digest))
        found.append((
            payload.get("iteration"),
            payload.get("case"),
            payload.get("check"),
            payload.get("value"),
        ))
    return found


def _run(tmp_path, capsys, *, uses, run_id, extra_nodes=False):
    kg, projection = _graph(tmp_path, capsys)
    if extra_nodes:
        _two_non_signals(kg, projection)
    workspace, root = _ws(tmp_path)
    store = tmp_path / "store"
    provider = StubProvider(responses=[json.dumps({
        "title": "Lamp",
        "cases": [_case(uses)],
    })])
    result = run_generate_test_spec(
        kg, projection, workspace, root, store,
        run_id=run_id,
        scope={"ids": ["REQ-1"]},
        provider=provider,
    )
    printed = capsys.readouterr().out
    return result, printed, root


def _last_issues(workspace_dir: Path, run_id: str) -> list[tuple]:
    found = _issues(workspace_dir, run_id)
    assert found, found
    last = max(item[0] for item in found)
    return [item for item in found if item[0] == last]


def test_ambiguous_non_signal_native_id_stays_and_fails_grounding(tmp_path, capsys):
    """Two non-Signal context nodes share a native id. uses keeps it. One grounding issue."""
    result, printed, root = _run(
        tmp_path, capsys, uses=[_SHARED], run_id="run-ambiguous", extra_nodes=True,
    )
    assert "n_context=2" in printed, printed
    assert result.recorded.status == "failed-validation"
    assert result.drafts[-1]["cases"][0]["uses"] == [_SHARED]
    last = _last_issues(root, "run-ambiguous")
    assert last == [(last[0][0], "TC-1", "grounding", _SHARED)]
    resolved = [line for line in printed.splitlines() if line.startswith("resolved_ids ")]
    assert resolved, printed
    assert resolved == ["resolved_ids uses=0 verifies=0"] * len(resolved)


def test_id_in_no_context_stays_and_fails_grounding(tmp_path, capsys):
    """An id that matches no context node is left as written and fails grounding."""
    result, printed, root = _run(
        tmp_path, capsys, uses=[_ABSENT], run_id="run-absent",
    )
    assert "n_context=0" in printed, printed
    assert result.recorded.status == "failed-validation"
    assert result.drafts[-1]["cases"][0]["uses"] == [_ABSENT]
    last = _last_issues(root, "run-absent")
    assert last == [(last[0][0], "TC-1", "grounding", _ABSENT)]
    resolved = [line for line in printed.splitlines() if line.startswith("resolved_ids ")]
    assert resolved, printed
    assert resolved == ["resolved_ids uses=0 verifies=0"] * len(resolved)


class _LengthProvider(StubProvider):
    def complete(self, system, user):
        raise RuntimeError(_LENGTH)


def test_provider_length_error_exits_1_without_the_error_text(tmp_path, monkeypatch, capsys):
    """A provider RuntimeError naming finish_reason='length' exits 1. The text is not printed."""
    import build_automotive_graph as graph_cmd

    graph = tmp_path / "graph-out"
    kg = pygrap.IndexedKnowledgeGraph(str(graph / "graph"))
    projection = project_items([_item()], kg)
    graph_cmd.save_graph_and_projection(graph / "graph", projection, kg)
    capsys.readouterr()
    reply = tmp_path / "reply.json"
    reply.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(generate_test_spec, "StubProvider", _LengthProvider)
    code = generate_test_spec.main([
        "--graph", str(graph),
        "--workspace", str(tmp_path / "ws"),
        "--run-id", "run-length",
        "--scope-ids", "REQ-1",
        "--provider", "stub",
        "--stub-reply", str(reply),
        "--out", str(tmp_path / "out"),
    ])
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert code == 1
    assert "model_error finish_reason=length" in captured.out
    assert "Traceback" not in combined
    assert "MUST-NOT-LEAK" not in combined
    assert "finish_reason='length'" not in combined
    assert _REQUIREMENT not in combined
