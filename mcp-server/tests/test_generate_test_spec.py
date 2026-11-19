"""RUN-01: scripts/generate_test_spec.py over run_generate_test_spec.

The graph is built in the test, the same way as test_procedure_model._graph,
then saved so the command can load it. These tests do not read gold/ or
real-data-law-ro/.
"""

import json
import subprocess
import sys
from pathlib import Path

import pygrap

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_automotive_graph as graph_cmd  # noqa: E402

from grap_mcp.readers import SourceItem  # noqa: E402
from grap_mcp.schema.project import project_items  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "generate_test_spec.py"
_REQUIREMENT = "The lamp shall be off."


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


def _case(**overrides):
    case = {
        "id": "TC-1",
        "verifies": ["REQ-1"],
        "preconditions": "power on at 20°C",
        "stimulus": "press",
        "expected_observable_result": "lamp off",
        "pass_criterion": "lamp is off",
        "kind": "positive",
        "uses": [],
        "variant": "",
        "checks": [],
    }
    case.update(overrides)
    return case


def _reply(**case_overrides):
    return {"title": "Lamp", "cases": [_case(**case_overrides)]}


def _graph_out(tmp_path, capsys):
    """One requirement, projected and saved beside graph/."""
    out = tmp_path / "graph-out"
    graph_dir = out / "graph"
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    projection = project_items([_item()], kg)
    graph_cmd.save_graph_and_projection(graph_dir, projection, kg)
    capsys.readouterr()
    return out


def _write_reply(path: Path, reply: dict) -> str:
    text = json.dumps(reply, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return text


def _run(graph, workspace, out, run_id, reply_path, scope_ids="REQ-1"):
    argv = [
        sys.executable,
        str(SCRIPT),
        "--graph",
        str(graph),
        "--workspace",
        str(workspace),
        "--run-id",
        run_id,
        "--provider",
        "stub",
        "--out",
        str(out),
    ]
    if scope_ids is not None:
        argv.extend(["--scope-ids", scope_ids])
    if reply_path is not None:
        argv.extend(["--stub-reply", str(reply_path)])
    return subprocess.run(argv, capture_output=True, text=True)


def _without_level(cases):
    return [{key: value for key, value in case.items() if key != "level"} for case in cases]


def test_stub_run_writes_specification_equal_to_the_reply_cases(tmp_path, capsys):
    """A stub run writes specification.json. Its cases are the reply's cases."""
    graph = _graph_out(tmp_path, capsys)
    reply = _reply()
    reply_text = _write_reply(tmp_path / "reply.json", reply)
    out = tmp_path / "out"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    completed = _run(graph, workspace, out, "run-ok", tmp_path / "reply.json")
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0
    assert _REQUIREMENT not in combined
    assert reply_text not in combined
    assert "20°C" not in combined
    spec_path = out / "specification.json"
    assert f"run-ok {spec_path}" in completed.stdout
    raw = spec_path.read_text(encoding="utf-8")
    written = json.loads(raw)
    assert raw == json.dumps(written, indent=2, ensure_ascii=False) + "\n"
    assert "20°C" in raw
    assert "\\u00b0" not in raw
    assert written["title"] == reply["title"]
    assert written["level"] == "software"
    assert _without_level(written["cases"]) == reply["cases"]
    assert [case["level"] for case in written["cases"]] == ["software"]
    emitted = list((out / "emitted").glob("*.json"))
    assert len(emitted) == 1


def test_a_reply_that_fails_validation_on_every_round_exits_1_and_writes_nothing(
    tmp_path, capsys,
):
    """One failing reply is repeated. The command exits 1 and writes no file."""
    graph = _graph_out(tmp_path, capsys)
    reply = _reply(stimulus="")
    reply_text = _write_reply(tmp_path / "reply.json", reply)
    out = tmp_path / "out"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    completed = _run(graph, workspace, out, "run-fail", tmp_path / "reply.json")
    combined = completed.stdout + completed.stderr
    assert completed.returncode == 1
    assert not (out / "specification.json").exists()
    written = [path for path in out.rglob("*") if path.is_file()] if out.exists() else []
    assert written == []
    assert _REQUIREMENT not in combined
    assert reply_text not in combined
    assert "lamp off" not in combined
    status = [
        line for line in completed.stdout.splitlines() if line.startswith("failed-validation")
    ]
    # One blank stimulus is one completeness issue on the last round.
    assert status == ["failed-validation n_issues=1"]
    assert "stimulus" not in status[0]


def test_provider_stub_without_stub_reply_is_a_usage_error(tmp_path):
    """--provider stub without --stub-reply is argparse's usage error."""
    out = tmp_path / "out"
    completed = _run(tmp_path / "graph-out", tmp_path / "ws", out, "run-usage", None)
    assert completed.returncode == 2
    assert "usage:" in completed.stderr
    assert "--stub-reply is required with --provider stub" in completed.stderr
    assert not (out / "specification.json").exists()
    assert _REQUIREMENT not in completed.stdout + completed.stderr
