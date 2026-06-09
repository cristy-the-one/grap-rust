"""Server-level tests for the four KG analytics tools.

Uses the same _FakeKG + monkeypatch.setitem(s._state, ...) pattern as
test_graph_neighbors_wire.py.  The default role in _state is "operator" which
satisfies the _require("operator") gate each analytics tool uses.
"""
import grap_mcp.server as s


class _FakeKG:
    def blast_radius(self, node_id, relations=None, max_depth=10):
        return [1, 2]

    def find_cycles(self):
        return [[3, 4]]

    def find_orphans(self, entry_points=None):
        return [9]

    def health(self):
        return {
            "live_nodes": 3,
            "edges": 1,
            "cycle_count": 0,
            "isolated_nodes": 0,
            "orphan_count": 0,
        }


def _kg(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _FakeKG())
    monkeypatch.setitem(s._state, "role", "operator")


def test_get_blast_radius(monkeypatch):
    _kg(monkeypatch)
    out = s.get_blast_radius(5)
    assert out["status"] == "ok" and out["impacted"] == [1, 2]


def test_find_cycles(monkeypatch):
    _kg(monkeypatch)
    out = s.find_cycles()
    assert out["status"] == "ok" and out["cycle_count"] == 1 and out["cycles"] == [[3, 4]]


def test_find_orphans(monkeypatch):
    _kg(monkeypatch)
    out = s.find_orphans()
    assert out["status"] == "ok" and out["orphan_count"] == 1 and out["orphans"] == [9]


def test_kg_health(monkeypatch):
    _kg(monkeypatch)
    out = s.kg_health()
    assert out["status"] == "ok" and out["live_nodes"] == 3


# --- no-KG guard tests ---

def test_get_blast_radius_no_kg(monkeypatch):
    monkeypatch.setitem(s._state, "kg", None)
    monkeypatch.setitem(s._state, "role", "operator")
    out = s.get_blast_radius(5)
    assert out["status"] == "error"


def test_find_cycles_no_kg(monkeypatch):
    monkeypatch.setitem(s._state, "kg", None)
    monkeypatch.setitem(s._state, "role", "operator")
    out = s.find_cycles()
    assert out["status"] == "error"


def test_find_orphans_no_kg(monkeypatch):
    monkeypatch.setitem(s._state, "kg", None)
    monkeypatch.setitem(s._state, "role", "operator")
    out = s.find_orphans()
    assert out["status"] == "error"


def test_kg_health_no_kg(monkeypatch):
    monkeypatch.setitem(s._state, "kg", None)
    monkeypatch.setitem(s._state, "role", "operator")
    out = s.kg_health()
    assert out["status"] == "error"
