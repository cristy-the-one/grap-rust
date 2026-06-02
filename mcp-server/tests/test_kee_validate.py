"""Phase 6: candidate ontology validation."""

from grap_mcp.kee.validate import validate_ontology


def test_valid_ontology_ok():
    onto = {"process_outcomes": [
        {"id": "verification", "name": "Verification", "required_characteristics": ["test_plan"]}]}
    rep = validate_ontology(onto)
    assert rep["ok"] is True
    assert rep["errors"] == []


def test_duplicate_ids_are_errors():
    onto = {"process_outcomes": [
        {"id": "x", "name": "X", "required_characteristics": ["a"]},
        {"id": "x", "name": "Y", "required_characteristics": ["b"]}]}
    rep = validate_ontology(onto)
    assert rep["ok"] is False
    assert any("duplicate outcome id: x" in e for e in rep["errors"])


def test_empty_characteristics_is_warning_not_error():
    onto = {"process_outcomes": [{"id": "x", "name": "X", "required_characteristics": []}]}
    rep = validate_ontology(onto)
    assert rep["ok"] is True
    assert any("no characteristics" in w for w in rep["warnings"])


def test_no_outcomes_warns():
    rep = validate_ontology({"process_outcomes": []})
    assert rep["ok"] is True
    assert any("no outcomes" in w for w in rep["warnings"])
