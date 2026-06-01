from grap_mcp.ontology import load_ontology_from_file


def test_parses_example_ontology(tmp_path):
    p = tmp_path / "o.ontology.yaml"
    p.write_text(
        "process_outcomes:\n"
        "  - id: SYS.5.BP3\n"
        "    name: Define verification criteria\n"
        "    description: Criteria defined.\n"
        "    required_characteristics:\n"
        "      - verification_method_specified\n"
        "      - acceptance_criteria_defined\n"
        "mapping_hints:\n"
        "  - outcome: SYS.5.BP3\n"
        "    indicators:\n"
        "      - keywords: [verify, test]\n"
        "      - properties: [has_verification_method]\n",
        encoding="utf-8",
    )
    o = load_ontology_from_file(str(p))
    assert len(o.process_outcomes) == 1
    oc = o.outcome("SYS.5.BP3")
    assert oc.name == "Define verification criteria"
    assert oc.required_characteristics == ["verification_method_specified", "acceptance_criteria_defined"]
    hint = o.hint_for("SYS.5.BP3")
    assert "verify" in hint.keywords and "test" in hint.keywords
    assert hint.properties == ["has_verification_method"]


def test_missing_outcome_and_hint_return_none(tmp_path):
    p = tmp_path / "o.ontology.yaml"
    p.write_text("process_outcomes: []\nmapping_hints: []\n", encoding="utf-8")
    o = load_ontology_from_file(str(p))
    assert o.outcome("nope") is None
    assert o.hint_for("nope") is None
