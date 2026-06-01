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


def test_lexical_hit_below_threshold_is_still_a_gap():
    onto = _onto()
    kg = FakeKG(lexical={"acceptance_criteria_defined": [Hit(1, 0.05, "weak")],
                         "verification_method_specified": [Hit(2, 0.9, "strong")]})
    gaps = detect_gaps(kg, onto)
    assert {g["missing_characteristic"] for g in gaps} == {"acceptance_criteria_defined"}
