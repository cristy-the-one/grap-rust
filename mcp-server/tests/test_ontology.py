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


from grap_mcp.ontology import Ontology, ProcessOutcome, MappingHint, project_ontology_to_grap


class FakeKG:
    def __init__(self):
        self._next = 1
        self.nodes = {}   # id -> (text, label)
        self.edges = []   # (src, dst, props)

    def add_node(self, text, label=None):
        nid = self._next
        self._next += 1
        self.nodes[nid] = (text, label)
        return nid

    def add_edge(self, source, target, strength=1.0, props=None):
        self.edges.append((source, target, props or {}))


def test_projection_creates_labelled_outcome_and_characteristic_nodes():
    onto = Ontology(
        version="0.1.0",
        process_outcomes=[ProcessOutcome("SYS.5.BP3", "Define verification criteria", "desc",
                                         ["verification_method_specified"])],
        mapping_hints=[MappingHint("SYS.5.BP3", ["verify"], [])],
    )
    kg = FakeKG()
    result = project_ontology_to_grap(onto, kg)
    assert result["outcomes_projected"] == 1
    assert result["characteristics_projected"] == 1
    labels = {label for (_t, label) in kg.nodes.values()}
    assert "kg:aspice.process_outcome.SYS.5.BP3" in labels
    assert "kg:aspice.characteristic.SYS.5.BP3.verification_method_specified" in labels
    assert any(p.get("type") == "requires" for (_s, _d, p) in kg.edges)
