from grap_mcp.procedure_graph import ProcedureGraph, ProcedureStep
from grap_mcp.validation import validate_pg


def _pg(steps):
    return ProcedureGraph(name="t", description="", version="0.1.0", steps=steps)


def test_valid_graph_has_no_errors():
    pg = _pg([
        ProcedureStep(id="a", operation="op a", outputs={"x": "list"}),
        ProcedureStep(id="b", operation="op b", inputs={"y": "a.outputs.x"}),
    ])
    report = validate_pg(pg)
    assert report["ok"] is True
    assert report["errors"] == []


def test_duplicate_step_ids_is_error():
    pg = _pg([
        ProcedureStep(id="a", operation="op"),
        ProcedureStep(id="a", operation="op2"),
    ])
    report = validate_pg(pg)
    assert report["ok"] is False
    assert any(e["code"] == "duplicate_step_id" for e in report["errors"])


def test_dataflow_ref_to_unknown_step_is_error():
    pg = _pg([ProcedureStep(id="b", operation="op", inputs={"y": "ghost.outputs.x"})])
    report = validate_pg(pg)
    assert any(e["code"] == "unknown_dataflow_step" for e in report["errors"])


def test_dataflow_ref_to_undeclared_output_is_error():
    pg = _pg([
        ProcedureStep(id="a", operation="op", outputs={"x": "list"}),
        ProcedureStep(id="b", operation="op", inputs={"y": "a.outputs.MISSING"}),
    ])
    report = validate_pg(pg)
    assert any(e["code"] == "unknown_dataflow_output" for e in report["errors"])


def test_empty_operation_is_warning():
    pg = _pg([ProcedureStep(id="a", operation="")])
    report = validate_pg(pg)
    assert report["ok"] is True
    assert any(w["code"] == "empty_operation" for w in report["warnings"])


def test_unresolved_kg_reference_is_warning_when_kg_present():
    pg = _pg([ProcedureStep(id="a", operation="op", references=["kg:does.not.exist"])])
    report = validate_pg(pg, resolver=lambda ref: None)
    assert any(w["code"] == "unresolved_kg_reference" for w in report["warnings"])


def test_resolved_kg_reference_produces_no_warning():
    pg = _pg([ProcedureStep(id="a", operation="op", references=["kg:exists"])])
    report = validate_pg(pg, resolver=lambda ref: 42)
    assert not any(w["code"] == "unresolved_kg_reference" for w in report["warnings"])
