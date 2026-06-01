from grap_mcp.procedure_graph import ProcedureGraph, ProcedureStep, project_to_grap


class FakeKG:
    def __init__(self, label_to_id=None):
        self._next = 100
        self.edges = []
        self._label_to_id = label_to_id or {}

    def add_node(self, text, label=None):
        nid = self._next
        self._next += 1
        return nid

    def add_edge(self, source, target, strength=1.0, properties=None):
        self.edges.append((source, target, properties or {}))

    # Resolver hook used by project_to_grap's default resolver.
    def resolve_reference(self, ref_id):
        return self._label_to_id.get(ref_id)


def _pg(steps):
    return ProcedureGraph(name="t", description="", version="0.1.0", steps=steps)


def test_resolved_reference_creates_reference_edge():
    kg = FakeKG(label_to_id={"kg:exists": 7})
    pg = _pg([ProcedureStep(id="a", operation="op", references=["kg:exists"])])
    result = project_to_grap(pg, kg)
    assert result["references_resolved"] == 1
    assert result["unresolved_references"] == []
    assert any(p.get("type") == "references" and p.get("ref") == "kg:exists"
               for (_s, _t, p) in kg.edges)


def test_unresolved_reference_is_reported_not_fatal():
    kg = FakeKG(label_to_id={})
    pg = _pg([ProcedureStep(id="a", operation="op", references=["kg:missing"])])
    result = project_to_grap(pg, kg)
    assert result["references_resolved"] == 0
    assert result["unresolved_references"] == ["kg:missing"]


def test_dataflow_edges_still_created():
    kg = FakeKG()
    pg = _pg([
        ProcedureStep(id="a", operation="op", outputs={"x": "list"}),
        ProcedureStep(id="b", operation="op", inputs={"y": "a.outputs.x"}),
    ])
    result = project_to_grap(pg, kg)
    assert result["data_flow_edges"] == 1
