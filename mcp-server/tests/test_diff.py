from grap_mcp.procedure_graph import ProcedureGraph, ProcedureStep
from grap_mcp.diff import diff_pg


def _pg(steps, version="0.1.0", name="t"):
    return ProcedureGraph(name=name, description="", version=version, steps=steps)


def test_added_and_removed_steps():
    a = _pg([ProcedureStep(id="x", operation="op")])
    b = _pg([ProcedureStep(id="y", operation="op")])
    d = diff_pg(a, b)
    assert d["steps_added"] == ["y"]
    assert d["steps_removed"] == ["x"]


def test_changed_step_lists_changed_fields():
    a = _pg([ProcedureStep(id="x", operation="old")])
    b = _pg([ProcedureStep(id="x", operation="new")])
    d = diff_pg(a, b)
    assert d["steps_added"] == []
    assert d["steps_removed"] == []
    assert d["steps_changed"] == [{"step_id": "x", "fields_changed": ["operation"]}]


def test_unchanged_step_is_not_reported():
    step = dict(id="x", operation="op", inputs={"a": 1})
    a = _pg([ProcedureStep(**step)])
    b = _pg([ProcedureStep(**step)])
    d = diff_pg(a, b)
    assert d["steps_changed"] == []


def test_metadata_change_is_reported():
    a = _pg([], version="0.1.0")
    b = _pg([], version="0.2.0")
    d = diff_pg(a, b)
    assert d["metadata_changed"]["version"] == ["0.1.0", "0.2.0"]
