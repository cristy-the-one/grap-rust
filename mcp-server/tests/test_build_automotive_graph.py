"""PIPE-01: one command builds the automotive graph from source files.

The fixture run exits 0, writes projection.json, and a second run into the
same directory changes no count. An absent Item stops the run at the edges
step. Loading a graph that lost a node the projection still names refuses
with mismatched=1.
"""

import csv
import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

import pygrap
import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import build_automotive_graph as cmd  # noqa: E402

from grap_mcp.kee.graph_store import GraphStore  # noqa: E402
from grap_mcp.kee.resolution.review import (  # noqa: E402
    COLUMNS,
    ReviewCandidate,
    decision_in_force,
    export_review_list,
    import_review_list,
)
from grap_mcp.kee.resolution.tables import ResolutionTables  # noqa: E402
from grap_mcp.readers.arxml import read_arxml  # noqa: E402


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
_MENTION_SENTENCE = "Bus.Veh_Spd shall be fresh."
_MENTION_DBC = """\
VERSION "REL-2026.11"

NS_ :

BS_:

BU_: Node

BO_ 256 Bus: 8 Node
 SG_ Veh_Spd : 0|16@1+ (1,0) [0|1] "" Node
"""
_COUNT = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)=(?:n/a|\d+)")
_STEP7_FAILED = re.compile(
    r"^step 7 \(edges\) failed: structural_missing=\d+ \(n=\d+ statements\)$",
    re.M,
)


def _counts(text: str) -> list[tuple[str, str]]:
    return _COUNT.findall(text)


def _argv(tmp_path: Path, out: Path, *, reqif_only: bool = False) -> list[str]:
    # Distinct stems. The committed fixtures stay named vehicle.*; sharing a
    # stem would be a duplicate source id.
    dbc = tmp_path / "network.dbc"
    reqif = tmp_path / "requirements.reqif"
    dbc.write_text(DBC, encoding="utf-8")
    reqif.write_text(REQIF, encoding="utf-8")
    argv = ["--release", _RELEASE, "--out", str(out), "--provider", "stub"]
    if not reqif_only:
        argv[0:0] = ["--dbc", str(dbc), "--arxml", str(_ARXML)]
    argv[0:0] = ["--reqif", str(reqif)]
    return argv


def _mention_argv(tmp_path: Path, out: Path) -> list[str]:
    dbc = tmp_path / "bus.dbc"
    reqif = tmp_path / "spec.reqif"
    dbc.write_text(_MENTION_DBC, encoding="utf-8")
    reqif.write_text(
        REQIF.replace("The vehicle shall remain controllable.", _MENTION_SENTENCE),
        encoding="utf-8",
    )
    return [
        "--dbc", str(dbc),
        "--reqif", str(reqif),
        "--release", _RELEASE,
        "--out", str(out),
        "--provider", "stub",
    ]


def _link_decision_count(out: Path, mention_id: int | None = None) -> int:
    path = out / "graph_store.db"
    conn = sqlite3.connect(path)
    try:
        if mention_id is None:
            row = conn.execute("SELECT COUNT(*) FROM link_decisions").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM link_decisions WHERE mention_id = ?",
                (mention_id,),
            ).fetchone()
        return row[0]
    finally:
        conn.close()


def _statement_nodes(projection) -> list:
    return [node for node in projection["nodes"] if node.get("type") == "Statement"]


def _assert_statements_on_disk(kg, projection) -> None:
    found = _statement_nodes(projection)
    assert found
    for node in found:
        engine_id = node["engine_id"]
        assert kg.get_node_label(engine_id) == "Statement"
        assert kg.is_node_deleted(engine_id) is False


def _saved_counts(out: Path) -> tuple:
    kg, projection = cmd.load_graph_and_projection(out / "graph")
    _assert_statements_on_disk(kg, projection)
    counts = (
        kg.node_count(),
        kg.edge_count(),
        len(projection["nodes"]),
        _link_decision_count(out),
    )
    del kg, projection
    return counts


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
    first_counts = _saved_counts(out)

    assert cmd.main(argv) == 0
    second = capsys.readouterr().out
    assert _saved_counts(out) == first_counts
    assert _counts(first) == _counts(second)
    assert _counts(first)
    for sentence in _ITEM_TEXT:
        assert sentence not in first
        assert sentence not in second
    assert "structural_missing_keys" not in first
    assert "entity_missing_keys" not in first
    assert "gold/" not in first
    assert not any(line == "step 3 (save)" for line in first.splitlines())
    assert any(line.startswith("step 3 (save) nodes=") for line in first.splitlines())


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
    assert _STEP7_FAILED.search(printed)
    assert "structural_missing_keys" not in printed
    assert "entity_missing_keys" not in printed
    assert f"Item | requirements@{_RELEASE} | _parent" not in printed
    for sentence in _ITEM_TEXT:
        assert sentence not in printed


def test_one_missing_item_is_structural_missing_1(tmp_path, monkeypatch, capsys):
    """Exactly one missing Item stops the run. A threshold above 1 would pass."""
    real = cmd.project_items

    def drop_one_item(items, kg):
        projection = real(items, kg)
        dropped = False
        kept = []
        for node in projection["nodes"]:
            if (
                not dropped
                and node.get("type") == "Item"
                and node.get("native_id") == "_parent"
            ):
                dropped = True
                continue
            kept.append(node)
        assert dropped
        projection["nodes"] = kept
        return projection

    monkeypatch.setattr(cmd, "project_items", drop_one_item)
    out = tmp_path / "out"
    code = cmd.main(_argv(tmp_path, out, reqif_only=True))
    printed = capsys.readouterr().out
    assert code != 0
    assert re.search(
        r"^step 7 \(edges\) failed: structural_missing=1 \(n=\d+ statements\)$",
        printed,
        re.M,
    )


def test_malformed_dbc_prints_the_step_not_the_comment(tmp_path, capsys):
    """A read failure names the step, the exception type, and the path."""
    bad = tmp_path / "broken.dbc"
    bad.write_text("this is not a dbc ZEBRA_COMMENT_SENTENCE\n", encoding="utf-8")
    out = tmp_path / "out"
    code = cmd.main([
        "--dbc", str(bad),
        "--release", _RELEASE,
        "--out", str(out),
    ])
    printed = capsys.readouterr().out
    assert code != 0
    assert "ZEBRA_COMMENT_SENTENCE" not in printed
    assert re.search(
        rf"^step 1 \(read\) failed: UnsupportedDatabaseFormatError: {re.escape(str(bad))}$",
        printed,
        re.M,
    )


def test_a_failed_project_does_not_block_the_next_run(tmp_path, monkeypatch, capsys):
    """After one failed step, a later run of the same command exits 0."""
    out = tmp_path / "out"
    argv = _argv(tmp_path, out, reqif_only=True)
    assert cmd.main(argv) == 0
    capsys.readouterr()

    def boom(items, kg):
        raise RuntimeError("project broke for ZEBRA_PROJECT")

    monkeypatch.setattr(cmd, "project_items", boom)
    code = cmd.main(argv)
    printed = capsys.readouterr().out
    assert code != 0
    assert "ZEBRA_PROJECT" not in printed
    assert re.search(r"^step 2 \(project\) failed: RuntimeError$", printed, re.M)

    monkeypatch.undo()
    assert cmd.main(argv) == 0


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
    assert re.search(
        r"^step 3 \(save\) failed: projection does not match graph "
        r"mismatched=1 \(n=2 nodes\)$",
        printed,
        re.M,
    )


def test_live_node_under_a_different_label_is_mismatched(tmp_path):
    """A live node whose label is not the projection label is mismatched=1."""
    graph_dir = tmp_path / "graph"
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    requirement = kg.add_node(text="SYS-1", label="Requirement")
    projection = {
        "nodes": [
            {
                "key": "Requirement | src | SYS-1",
                "type": "Requirement",
                "label": "Signal",
                "engine_id": requirement,
                "native_id": "SYS-1",
                "scope": "src",
                "source_id": "src",
            },
        ],
        "edges": [],
        "counts": {"documents": 1},
    }
    cmd.save_graph_and_projection(graph_dir, projection, kg)
    with pytest.raises(cmd.ProjectionMismatch) as caught:
        cmd.load_graph_and_projection(graph_dir)
    assert caught.value.mismatched == 1
    assert kg.is_node_deleted(requirement) is False
    assert kg.get_node_label(requirement) == "Requirement"


def test_graph_is_saved_before_projection(tmp_path, monkeypatch):
    """projection.json is written after the graph save, on every save."""
    order = []
    real_projection = cmd.save_projection
    real_graph = pygrap.IndexedKnowledgeGraph.save

    def spy_projection(path, projection):
        order.append("projection")
        return real_projection(path, projection)

    def spy_graph(self):
        order.append("graph")
        return real_graph(self)

    monkeypatch.setattr(cmd, "save_projection", spy_projection)
    monkeypatch.setattr(pygrap.IndexedKnowledgeGraph, "save", spy_graph)
    out = tmp_path / "out"
    assert cmd.main(_argv(tmp_path, out, reqif_only=True)) == 0
    assert order == ["graph", "projection", "graph", "projection"]


def test_default_provider_is_stub_and_reqif_gets_the_release(tmp_path, monkeypatch):
    """--provider defaults to stub. A ReqIF item with no release gets --release."""
    parsed = cmd._parser().parse_args([
        "--release", "REL-TEST",
        "--out", "unused",
        "--dbc", "x.dbc",
    ])
    assert parsed.provider == "stub"
    seen = {}
    real = cmd.project_items

    def capture(items, kg):
        seen["items"] = list(items)
        return real(items, kg)

    monkeypatch.setattr(cmd, "project_items", capture)
    reqif = tmp_path / "requirements.reqif"
    reqif.write_text(REQIF, encoding="utf-8")
    out = tmp_path / "out"
    assert cmd.main([
        "--reqif", str(reqif),
        "--release", "REL-TEST",
        "--out", str(out),
    ]) == 0
    stamped = [item for item in seen["items"] if item.kind_hint != "unresolved"]
    assert stamped
    assert all(item.release == "REL-TEST" for item in stamped)


def test_duplicate_source_ids_are_refused(tmp_path, capsys):
    """Two inputs with the same stem stop at step 1 and name both paths."""
    left = tmp_path / "left" / "body.dbc"
    right = tmp_path / "right" / "body.dbc"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_text(DBC, encoding="utf-8")
    right.write_text(DBC, encoding="utf-8")
    out = tmp_path / "out"
    code = cmd.main([
        "--dbc", str(left),
        "--dbc", str(right),
        "--release", _RELEASE,
        "--out", str(out),
    ])
    printed = capsys.readouterr().out
    assert code != 0
    failed = re.search(r"^step 1 \(read\) failed:.*$", printed, re.M)
    assert failed
    assert str(left) in failed.group(0)
    assert str(right) in failed.group(0)


def test_link_decisions_do_not_grow_on_rerun(tmp_path):
    """A requirement that names a signal writes the same decision rows twice."""
    out = tmp_path / "out"
    argv = _mention_argv(tmp_path, out)
    assert cmd.main(argv) == 0
    first = _link_decision_count(out)
    assert first > 0
    assert cmd.main(argv) == 0
    assert _link_decision_count(out) == first


def test_human_different_is_kept_and_stage1_writes_nothing(tmp_path, capsys):
    """A latest human ``different`` stays, and the rerun appends no decision."""
    out = tmp_path / "out"
    argv = _mention_argv(tmp_path, out)
    assert cmd.main(argv) == 0
    capsys.readouterr()
    with GraphStore(str(out / "graph_store.db")) as store:
        tables = ResolutionTables(store)
        mention = tables.conn.execute(
            "SELECT mention_id FROM mentions WHERE text = ?",
            ("Bus.Veh_Spd",),
        ).fetchone()
        assert mention is not None
        mention_id = mention["mention_id"]
        decision = tables.conn.execute(
            "SELECT entity_key FROM link_decisions WHERE mention_id = ?",
            (mention_id,),
        ).fetchone()
        assert decision is not None
        entity_key = decision["entity_key"]
        sheet = tmp_path / "review.csv"
        export_review_list(tables, sheet, [ReviewCandidate(
            mention_id=mention_id,
            entity_key=entity_key,
            other_name="Bus.Veh_Spd",
            other_sentence="The signal on the bus.",
            other_source_id="bus",
            score=1.0,
        )])
        _header, rows = _read_sheet(sheet)
        assert len(rows) == 1
        rows[0]["label"] = "different"
        _write_sheet(sheet, rows)
        import_review_list(tables, sheet, decided_at="2026-11-17T13:18:00Z")
        in_force = decision_in_force(tables, mention_id, entity_key)
        assert in_force.decider == "human"
        assert in_force.verdict == "not_same"
        before = _link_decision_count(out, mention_id)
    assert cmd.main(argv) == 0
    with GraphStore(str(out / "graph_store.db")) as store:
        tables = ResolutionTables(store)
        assert _link_decision_count(out, mention_id) == before
        in_force = decision_in_force(tables, mention_id, entity_key)
        assert in_force.decider == "human"
        assert in_force.verdict == "not_same"
        latest = tables.conn.execute(
            """
            SELECT verdict, decider FROM link_decisions
            WHERE mention_id = ?
            ORDER BY decision_id DESC
            LIMIT 1
            """,
            (mention_id,),
        ).fetchone()
        assert latest["verdict"] == "not_same"
        assert latest["decider"] == "human"


def test_arxml_release_comes_from_the_schema(tmp_path, capsys):
    """The fixture ARXML line prints the release the reader recorded."""
    items, _report = read_arxml([_ARXML], source_id=_ARXML.stem)
    releases = {
        item.release
        for item in items
        if item.kind_hint != "unresolved" and item.release is not None
    }
    assert len(releases) == 1
    release = next(iter(releases))
    help_text = cmd._parser().format_help()
    assert "AUTOSAR schema" in help_text
    out = tmp_path / "out"
    assert cmd.main(_argv(tmp_path, out)) == 0
    printed = capsys.readouterr().out
    assert (
        f"arxml {_ARXML.name} release={release} (from the AUTOSAR schema)"
        in printed
    )


def _read_sheet(path: Path):
    with open(path, encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return list(rows[0].keys()) if rows else list(COLUMNS), rows


def _write_sheet(path: Path, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
