from grap_mcp.entity_index import EntityIndex, SCHEMA_VERSION

def test_migrations_set_version_and_are_idempotent(tmp_path):
    db = str(tmp_path / "ei.db")
    with EntityIndex(db) as ei:
        assert ei.schema_version() == SCHEMA_VERSION
    with EntityIndex(db) as ei:
        assert ei.schema_version() == SCHEMA_VERSION

def test_register_and_resolve_case_insensitive(tmp_path):
    with EntityIndex(str(tmp_path / "ei.db")) as ei:
        ei.register("REQ-123", 5, kind="requirement")
        ei.register("Brake_Pedal", 9)
        assert ei.resolve("req-123") == [5]
        assert ei.resolve("  REQ-123 ") == [5]
        assert ei.resolve("missing") == []

def test_alias_maps_to_multiple_nodes_and_aliases_for(tmp_path):
    with EntityIndex(str(tmp_path / "ei.db")) as ei:
        ei.register("brake", 1)
        ei.register("brake", 2)
        assert sorted(ei.resolve("brake")) == [1, 2]
        ei.register("pedal", 1)
        assert sorted(ei.aliases_for(1)) == ["brake", "pedal"]

def test_register_many_and_remove_node(tmp_path):
    with EntityIndex(str(tmp_path / "ei.db")) as ei:
        ei.register_many([("a", 1, None), ("b", 1, None), ("a", 2, None)])
        assert ei.count() == 3
        removed = ei.remove_node(1)
        assert removed == 2
        assert ei.resolve("a") == [2]
        assert ei.aliases_for(1) == []


import grap_mcp.server as s

class _LabelKG:
    def __init__(self, labels):  # {node_id: label or None}
        self._labels = labels
    def node_id_space(self):
        return max(self._labels) + 1 if self._labels else 0
    def is_node_deleted(self, nid):
        return nid not in self._labels
    def get_node_label(self, nid):
        return self._labels.get(nid)

def test_entity_tools_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setitem(s._state, "kg", _LabelKG({0: "REQ-1", 1: "SIG-2", 2: None}))
    monkeypatch.setitem(s._state, "kg_path", str(tmp_path))
    r = s.reindex_entities()
    assert r["status"] == "ok" and r["indexed"] == 2
    out = s.resolve_entity("req-1")
    assert out["status"] == "ok" and out["node_ids"] == [0]
    assert s.register_alias("brake-by-wire", 0)["status"] == "ok"
    assert 0 in s.resolve_entity("brake-by-wire")["node_ids"]
    al = s.aliases_for_node(0)
    assert al["status"] == "ok" and "REQ-1" in al["aliases"] and "brake-by-wire" in al["aliases"]
