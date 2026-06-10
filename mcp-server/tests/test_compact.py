import grap_mcp.server as s


class _CompactKG:
    def __init__(self):
        self.saved = False
        self.vacuumed = False

    def vacuum_blobs(self):
        self.vacuumed = True
        return {"bytes_before": 9000, "bytes_after": 1000, "bytes_reclaimed": 8000}

    def vacuum_deleted_nodes(self):
        pass

    def save_to_dir(self, path):
        self.saved = True


def test_compact_graph(tmp_path, monkeypatch):
    kg = _CompactKG()
    monkeypatch.setitem(s._state, "kg", kg)
    monkeypatch.setitem(s._state, "kg_path", str(tmp_path))
    out = s.compact_graph()
    assert out["status"] == "ok"
    assert out["bytes_reclaimed"] == 8000
    assert kg.vacuumed and kg.saved


def test_compact_graph_no_kg(monkeypatch):
    monkeypatch.setitem(s._state, "kg", None)
    out = s.compact_graph()
    assert out["status"] == "error"
