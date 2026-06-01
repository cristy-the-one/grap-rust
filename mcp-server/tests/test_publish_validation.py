from grap_mcp.procedure_graph import ProcedureGraph, ProcedureStep, load_from_string, load_from_file
from grap_mcp.ontology import Ontology, ProcessOutcome, MappingHint
from grap_mcp.publish_validation import validate_for_publish


def _onto():
    return Ontology(version="0.1.0",
                    process_outcomes=[ProcessOutcome("SYS.5.BP3", "Verify", "d", ["x"])],
                    mapping_hints=[MappingHint("SYS.5.BP3", [], [])])


def _pg(steps):
    return ProcedureGraph(name="t", description="", version="0.1.0", steps=steps)


def test_clean_pg_is_ok():
    pg = _pg([ProcedureStep(id="a", operation="op",
                            references=["kg:aspice.process_outcome.SYS.5.BP3"])])
    report = validate_for_publish(pg, kg=None, ontology=_onto())
    assert report["ok"] is True
    assert report["errors"] == []


def test_unknown_process_outcome_is_error():
    pg = _pg([ProcedureStep(id="a", operation="op",
                            references=["kg:aspice.process_outcome.BOGUS"])])
    report = validate_for_publish(pg, kg=None, ontology=_onto())
    assert report["ok"] is False
    assert any(e["code"] == "unknown_process_outcome" for e in report["errors"])


def test_structural_error_blocks_ok():
    pg = _pg([ProcedureStep(id="b", operation="op", inputs={"y": "ghost.outputs.x"})])
    report = validate_for_publish(pg, kg=None, ontology=_onto())
    assert report["ok"] is False
    assert any(e["code"] == "unknown_dataflow_step" for e in report["errors"])


def test_no_ontology_is_warning_not_error():
    pg = _pg([ProcedureStep(id="a", operation="op",
                            references=["kg:aspice.process_outcome.BOGUS"])])
    report = validate_for_publish(pg, kg=None, ontology=None)
    assert report["ok"] is True
    assert any(w["code"] == "ontology_not_loaded" for w in report["warnings"])


def test_load_from_string_matches_fields(tmp_path):
    text = "name: P\nversion: 1.2.3\nsteps:\n  - id: a\n    operation: do\n"
    pg = load_from_string(text)
    assert pg.name == "P" and pg.version == "1.2.3"
    assert pg.steps[0].id == "a" and pg.steps[0].operation == "do"
    p = tmp_path / "x.pg.yaml"
    p.write_text(text, encoding="utf-8")
    assert load_from_file(str(p)).name == "P"
