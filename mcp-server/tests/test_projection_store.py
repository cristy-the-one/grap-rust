"""PJ-01: persist a projection beside its graph directory.

Project the DBC fixture and the requirements fixture, save, and load a
fresh object. On the requirements fixture, selection and context from a
reopened graph match the original and are not empty. A missing file names
the path. ``{}``, ``[]``, ``null``, and truncated JSON are refused and
name the path. A crash while writing leaves the previous file and no temp
file.
"""

import importlib.util
from pathlib import Path

import pygrap
import pytest

from grap_mcp.procedure_context import fetch_context, select_requirements
from grap_mcp.readers.dbc import read_dbc
from grap_mcp.schema.project import project_items
from grap_mcp.schema.projection_store import load_projection, save_projection


def _dbc_fixture() -> str:
    path = Path(__file__).with_name("test_reader_dbc.py")
    spec = importlib.util.spec_from_file_location("pj01_dbc_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DBC


def _project(tmp_path: Path):
    """The DBC fixture, projected into a graph directory."""
    graph_dir = tmp_path / "graph"
    dbc_path = tmp_path / "vehicle.dbc"
    dbc_path.write_text(_dbc_fixture(), encoding="utf-8")
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    projection = project_items(read_dbc(dbc_path, corpus_root=tmp_path), kg)
    kg.save()
    path = graph_dir.parent / "projection.json"
    return kg, projection, graph_dir, path


def _stored(projection):
    return {
        "nodes": projection["nodes"],
        "edges": projection["edges"],
        "counts": projection["counts"],
    }


def _requirement_items():
    """The procedure-context fixture: 9 Requirement items."""
    path = Path(__file__).with_name("test_procedure_context.py")
    spec = importlib.util.spec_from_file_location("pj01_procedure_context", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._items()


def _project_items(tmp_path: Path, items):
    graph_dir = tmp_path / "graph"
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    projection = project_items(items, kg)
    kg.save()
    path = graph_dir.parent / "projection.json"
    return kg, projection, graph_dir, path


def _answers(kg, projection, scope):
    requirements = select_requirements(kg, projection, scope)
    return requirements, fetch_context(kg, projection, requirements)


def test_loaded_projection_answers_the_same_as_the_original(tmp_path):
    """Save beside the graph directory and load a fresh object.

    select_requirements and fetch_context on that object match the
    projection that was saved. The file holds nodes, edges, and counts.
    """
    kg, projection, graph_dir, path = _project(tmp_path)
    assert graph_dir.is_dir()
    assert path == graph_dir.parent / "projection.json"
    assert projection["nodes"]
    assert projection["edges"]

    save_projection(path, projection)
    loaded = load_projection(path)
    again = load_projection(path)

    assert path.is_file()
    assert path.name == "projection.json"
    assert loaded is not projection
    assert loaded is not again
    assert loaded == again == _stored(projection)

    scope = {"source": projection["nodes"][0]["source_id"]}
    original_requirements = select_requirements(kg, projection, scope)
    loaded_requirements = select_requirements(kg, loaded, scope)
    assert loaded_requirements == original_requirements
    assert fetch_context(kg, loaded, loaded_requirements) == fetch_context(
        kg, projection, original_requirements,
    )


def test_reopened_requirements_match_parent_and_ids(tmp_path):
    """The requirements fixture survives save, load, and a reopened graph.

    Selection and context are the same as the original and are not empty,
    for a parent scope and an ids scope. A DBC-only graph has no
    Requirement nodes, so this is the comparison that notices a corrupted
    projection.
    """
    kg, projection, graph_dir, path = _project_items(tmp_path, _requirement_items())
    requirement_nodes = [
        node for node in projection["nodes"] if node["type"] == "Requirement"
    ]
    assert len(requirement_nodes) == 9

    save_projection(path, projection)
    loaded = load_projection(path)
    assert loaded is not projection
    reopened = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    assert reopened is not kg

    for scope in ({"parent": "REQ-PARENT"}, {"ids": ["REQ-LOWER", "REQ-UPPER"]}):
        original_requirements, original_context = _answers(kg, projection, scope)
        loaded_requirements, loaded_context = _answers(reopened, loaded, scope)
        assert loaded_requirements
        assert loaded_requirements == original_requirements
        assert "REQ-LOWER" in [row["item_id"] for row in loaded_requirements]
        assert loaded_context == original_context
        assert any(group["nodes"] for group in loaded_context["requirements"])


def test_missing_file_names_the_path(tmp_path):
    missing = tmp_path / "graph" / "projection.json"
    with pytest.raises(FileNotFoundError) as caught:
        load_projection(missing)
    assert str(caught.value) == f"projection file not found: {missing}"


@pytest.mark.parametrize("text", ["{}", "[]", "null"])
def test_wrong_shape_names_the_path(tmp_path, text):
    path = tmp_path / "projection.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_projection(path)
    assert str(path) in str(caught.value)


def test_truncated_file_is_refused(tmp_path):
    _kg, projection, _graph_dir, path = _project(tmp_path)
    save_projection(path, projection)
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: len(text) // 2], encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_projection(path)
    assert str(path) in str(caught.value)


def test_crash_during_save_leaves_the_old_file(tmp_path, monkeypatch):
    _kg, projection, _graph_dir, path = _project(tmp_path)
    save_projection(path, projection)
    before = path.read_bytes()

    def crash(_payload, handle, **_kwargs):
        handle.write("{")
        raise RuntimeError("crash")

    monkeypatch.setattr("grap_mcp.schema.projection_store.json.dump", crash)
    with pytest.raises(RuntimeError, match="crash"):
        save_projection(path, {"nodes": [], "edges": [], "counts": {}})
    assert path.read_bytes() == before
    assert list(path.parent.glob("*.tmp")) == []
    assert load_projection(path) == _stored(projection)
