"""PIPE-01: one command builds the automotive graph from source files.

The fixture run exits 0, writes projection.json, and a second run into the
same directory changes no count. An absent Item stops the run at the edges
step. Loading a graph that lost a node the projection still names refuses
with mismatched=1.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pygrap
import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_automotive_graph as cmd  # noqa: E402


def _fixture(module_name: str, filename: str, attribute: str) -> str:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, attribute)


DBC = _fixture("pipe01_dbc_fixture", "test_reader_dbc.py", "DBC")
REQIF = _fixture("pipe01_reqif_fixture", "test_reader_reqif.py", "REQIF")

_ARXML = Path(__file__).resolve().parent / "fixtures" / "arxml" / "system-4.2.arxml"
_RELEASE = "REL-2026.11"
_ITEM_TEXT = (
    "The vehicle shall remain controllable.",
    "The system shall modulate brake pressure.",
    "The system shall brake.",
)
_COUNT = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)=(?:n/a|\d+)")


def _counts(text: str) -> list[tuple[str, str]]:
    return _COUNT.findall(text)


def _argv(tmp_path: Path, out: Path, *, reqif_only: bool = False) -> list[str]:
    dbc = tmp_path / "vehicle.dbc"
    reqif = tmp_path / "vehicle.reqif"
    dbc.write_text(DBC, encoding="utf-8")
    reqif.write_text(REQIF, encoding="utf-8")
    argv = ["--release", _RELEASE, "--out", str(out), "--provider", "stub"]
    if not reqif_only:
        argv[0:0] = ["--dbc", str(dbc), "--arxml", str(_ARXML)]
    argv[0:0] = ["--reqif", str(reqif)]
    return argv


def test_fixture_run_exits_0_and_second_run_changes_no_count(tmp_path, capsys):
    """The fixture command exits 0, writes projection.json, and counts stay put."""
    out = tmp_path / "out"
    argv = _argv(tmp_path, out)
    assert cmd.main(argv) == 0
    first = capsys.readouterr().out
    projection = out / "projection.json"
    assert projection.is_file()
    loaded = cmd.load_projection(projection)
    assert loaded["nodes"]
    assert isinstance(loaded["edges"], list)
    assert isinstance(loaded["counts"], dict)

    assert cmd.main(argv) == 0
    second = capsys.readouterr().out
    assert _counts(first) == _counts(second)
    assert _counts(first)
    for sentence in _ITEM_TEXT:
        assert sentence not in first
        assert sentence not in second
    assert "structural_missing_keys" not in first
    assert "entity_missing_keys" not in first
    assert "gold/" not in first


def test_absent_item_stops_at_the_edges_step(tmp_path, monkeypatch, capsys):
    """An Item missing from the projection makes the command name the edges step."""
    real = cmd.project_items

    def drop_items(items, kg):
        projection = real(items, kg)
        projection["nodes"] = [
            node for node in projection["nodes"] if node.get("type") != "Item"
        ]
        return projection

    monkeypatch.setattr(cmd, "project_items", drop_items)
    out = tmp_path / "out"
    code = cmd.main(_argv(tmp_path, out, reqif_only=True))
    printed = capsys.readouterr().out
    assert code != 0
    assert "step 7" in printed
    assert "edges" in printed
    assert re.search(r"structural_missing=[1-9]\d*", printed)
    assert "n_statements=" in printed
    assert "structural_missing_keys" not in printed
    assert "entity_missing_keys" not in printed
    assert f"Item | vehicle@{_RELEASE} | _parent" not in printed
    for sentence in _ITEM_TEXT:
        assert sentence not in printed


def test_load_refuses_when_one_graph_node_was_deleted(tmp_path, capsys):
    """Save, delete one graph node, save the graph; loading refuses mismatched=1."""
    graph_dir = tmp_path / "graph"
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    requirement = kg.add_node(text="SYS-1", label="Requirement")
    signal = kg.add_node(text="VehSpd", label="Signal")
    projection = {
        "nodes": [
            {
                "key": "Requirement | src | SYS-1",
                "type": "Requirement",
                "label": "Requirement",
                "engine_id": requirement,
                "native_id": "SYS-1",
                "scope": "src",
                "source_id": "src",
            },
            {
                "key": "Signal | src | VehSpd",
                "type": "Signal",
                "label": "Signal",
                "engine_id": signal,
                "native_id": "VehSpd",
                "scope": "src",
                "source_id": "src",
            },
        ],
        "edges": [],
        "counts": {"documents": 1},
    }
    cmd.save_graph_and_projection(graph_dir, projection, kg)
    assert (tmp_path / "projection.json").is_file()
    kg.remove_node(requirement)
    kg.save()

    with pytest.raises(cmd.ProjectionMismatch) as caught:
        cmd.load_graph_and_projection(graph_dir)
    assert caught.value.mismatched == 1
    assert "projection does not match graph" in str(caught.value)

    code = cmd.main([
        "--dbc", str(tmp_path / "missing.dbc"),
        "--release", _RELEASE,
        "--out", str(tmp_path),
        "--provider", "stub",
    ])
    printed = capsys.readouterr().out
    assert code != 0
    assert "projection does not match graph" in printed
    assert "mismatched=1" in printed
