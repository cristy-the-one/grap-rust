from grap_mcp.ontology import Ontology, ProcessOutcome, MappingHint
from grap_mcp.procedure_graph import ProcedureGraph, ProcedureStep
from grap_mcp.reasoner import scoped_outcomes, detect_gaps


class Hit:
    def __init__(self, node, fused_score, text):
        self.node = node
        self.fused_score = fused_score
        self.text = text


class FakeKG:
    """Returns canned lexical hits per query substring; optional structural facts."""
    def __init__(self, lexical=None, structural=None):
        self._lexical = lexical or {}   # keyword -> [Hit]
        self._structural = structural or set()  # {(outcome_id, characteristic)}

    def search_and_traverse(self, query, **kwargs):
        for key, hits in self._lexical.items():
            if key in query:
                return hits
        return []

    def has_characteristic(self, outcome_id, characteristic):
        return (outcome_id, characteristic) in self._structural


def _onto():
    return Ontology(
        version="0.1.0",
        process_outcomes=[ProcessOutcome("SYS.5.BP3", "Verify", "d",
                                         ["verification_method_specified", "acceptance_criteria_defined"])],
        mapping_hints=[MappingHint("SYS.5.BP3", ["verify", "criteria"], [])],
    )


def test_scoped_outcomes_uses_pg_references():
    onto = _onto()
    pg = ProcedureGraph(name="p", description="", version="1",
                        steps=[ProcedureStep(id="s", operation="o",
                                             references=["kg:aspice.process_outcome.SYS.5.BP3"])])
    scoped = scoped_outcomes(onto, pg)
    assert [o.id for o in scoped] == ["SYS.5.BP3"]


def test_scoped_outcomes_defaults_to_all_when_pg_has_no_refs():
    onto = _onto()
    assert len(scoped_outcomes(onto, None)) == 1


def test_no_gaps_when_all_characteristics_satisfied_structurally():
    onto = _onto()
    kg = FakeKG(structural={("SYS.5.BP3", "verification_method_specified"),
                            ("SYS.5.BP3", "acceptance_criteria_defined")})
    assert detect_gaps(kg, onto) == []


def test_gap_when_characteristic_unsatisfied():
    onto = _onto()
    # Only one characteristic has a strong lexical hit; the other has nothing.
    kg = FakeKG(lexical={"verification_method_specified": [Hit(7, 0.9, "verify method here")]})
    gaps = detect_gaps(kg, onto)
    missing = {g["missing_characteristic"] for g in gaps}
    assert missing == {"acceptance_criteria_defined"}
    assert gaps[0]["outcome_id"] == "SYS.5.BP3"


def test_projected_ontology_nodes_are_ignored_as_evidence():
    onto = _onto()
    # Both characteristics only "match" their own projected ontology scaffolding,
    # which must NOT count as evidence -> both are gaps.
    kg = FakeKG(lexical={
        "verification_method_specified": [Hit(1, 0.95, "[characteristic] verification_method_specified")],
        "acceptance_criteria_defined": [Hit(2, 0.95, "[ASPICE outcome] Verify: d")],
    })
    gaps = detect_gaps(kg, onto)
    assert {g["missing_characteristic"] for g in gaps} == {
        "verification_method_specified", "acceptance_criteria_defined"}


def test_lexical_hit_below_threshold_is_still_a_gap():
    onto = _onto()
    kg = FakeKG(lexical={"acceptance_criteria_defined": [Hit(1, 0.05, "weak")],
                         "verification_method_specified": [Hit(2, 0.9, "strong")]})
    gaps = detect_gaps(kg, onto)
    assert {g["missing_characteristic"] for g in gaps} == {"acceptance_criteria_defined"}


from grap_mcp.reasoner import explain_gap


def test_high_threshold_turns_satisfied_into_gap():
    onto = _onto()
    kg = FakeKG(lexical={
        "verification_method_specified": [Hit(1, 0.9, "verify method")],
        "acceptance_criteria_defined": [Hit(2, 0.9, "acceptance criteria")],
    })
    assert detect_gaps(kg, onto) == []
    gaps = detect_gaps(kg, onto, weights={"lexical_threshold": 0.99})
    assert {g["missing_characteristic"] for g in gaps} == {
        "verification_method_specified", "acceptance_criteria_defined"}


def test_gaps_sorted_by_confidence_desc():
    onto = _onto()
    kg = FakeKG(lexical={"verification_method_specified": [Hit(1, 0.5, "weak")]})
    gaps = detect_gaps(kg, onto, weights={"lexical_threshold": 0.99})
    confidences = [g["confidence"] for g in gaps]
    assert confidences == sorted(confidences, reverse=True)
    assert gaps[0]["missing_characteristic"] == "acceptance_criteria_defined"


def test_explain_gap_has_sections_and_raw_signals():
    gap = {
        "outcome_id": "SYS.5.BP3",
        "outcome_name": "Verify",
        "missing_characteristic": "acceptance_criteria_defined",
        "signals": {"structural": [], "lexical": [
            {"kind": "lexical", "node": 1, "score": 0.05, "snippet": "weak", "query": "acceptance criteria defined"}
        ]},
        "confidence": 0.95,
    }
    exp = explain_gap(gap, ontology_version_hash="abc123", expected="Criteria defined.")
    assert exp["target"] == "SYS.5.BP3:acceptance_criteria_defined"
    assert exp["sections"]["structured"]["outcome"] == "SYS.5.BP3"
    assert exp["sections"]["structured"]["expected"] == "Criteria defined."
    assert exp["sections"]["statistical"]["queries"] == ["acceptance criteria defined"]
    assert exp["sections"]["statistical"]["top_hits"][0]["node"] == 1
    assert isinstance(exp["sections"]["rendered"], str) and exp["sections"]["rendered"]
    assert exp["raw_signals"]["ontology_version"] == "abc123"
    assert exp["raw_signals"]["characteristic"] == "acceptance_criteria_defined"
