"""Server-level test for the opt-in compact wire encoding on graph_neighbors.

Sets up a minimal fake KG in module state (auth defaults to the 'operator'
role, which graph_neighbors requires) and calls the tool function directly,
asserting the return type flips between dict (json default) and the #MUNCH
string (wire_format="compact"). Also pins the new signature parameter.
"""
import inspect

import grap_mcp.server as s


class _FakeKG:
    """Two acts (8, 9) cited by node 5 — enough for two non-empty tables."""

    def get_node_label(self, nid):
        return f"act-{nid}"

    def get_node_text(self, nid):
        return f"text for {nid}"

    def neighbors_by_relation(self, node_id, _attr, ty):
        if node_id == 5 and ty == "cites":
            return [(8, 1.0), (9, 1.0)]
        return []

    def predecessors_by_relation(self, node_id, _attr, ty):
        return []


def _with_fake_kg(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _FakeKG())
    monkeypatch.setitem(s._state, "role", "operator")


def test_signature_exposes_wire_format():
    params = inspect.signature(s.graph_neighbors).parameters
    assert "wire_format" in params
    assert params["wire_format"].default == "json"


def test_graph_neighbors_default_returns_dict(monkeypatch):
    _with_fake_kg(monkeypatch)
    out = s.graph_neighbors(5, types=["cites"])
    assert isinstance(out, dict)
    assert out["status"] == "ok"
    assert out["center"] == 5
    assert [n["id"] for n in out["neighbors"]] == [8, 9]
    assert out["edges"]


def test_graph_neighbors_compact_returns_munch_string(monkeypatch):
    _with_fake_kg(monkeypatch)
    out = s.graph_neighbors(5, types=["cites"], wire_format="compact")
    assert isinstance(out, str)
    assert out.startswith("#MUNCH/1 tool=graph_neighbors")
