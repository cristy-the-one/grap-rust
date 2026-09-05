import json
from pathlib import Path
import grap_mcp.incremental as inc


class _FakeKG:
    def __init__(self):
        self._next = 0
        self.live = {}

    def add_node(self, text, label=None, embedding=None):
        nid = self._next
        self._next += 1
        self.live[nid] = text
        return nid

    def remove_node(self, nid):
        return self.live.pop(nid, None) is not None


def _write(p, text):
    p.write_text(text, encoding="utf-8")


def test_incremental_skips_unchanged_and_handles_edits(tmp_path):
    doc = tmp_path / "a.md"
    _write(doc, "Alpha block.\n\nBeta block.")
    man = tmp_path / "manifest.json"
    r1 = inc.ingest_incremental(_FakeKG(), str(tmp_path), manifest_path=str(man))
    assert r1["nodes_added"] == 2 and r1["nodes_removed"] == 0 and r1["skipped"] == 0
    r2 = inc.ingest_incremental(_FakeKG(), str(tmp_path), manifest_path=str(man))
    assert r2["nodes_added"] == 0 and r2["nodes_removed"] == 0 and r2["skipped"] == 2
    _write(doc, "Alpha block.\n\nBeta block EDITED.")
    r3 = inc.ingest_incremental(_FakeKG(), str(tmp_path), manifest_path=str(man))
    assert r3["nodes_added"] == 1 and r3["nodes_removed"] == 1 and r3["skipped"] == 1
    saved = json.loads(man.read_text())
    assert sum(len(chunks) for chunks in saved.values()) == 2


def test_watch_paths_one_iteration(tmp_path):
    doc = tmp_path / "a.md"
    _write(doc, "Alpha block.")
    man = tmp_path / "manifest.json"
    runs = inc.watch_paths(_FakeKG(), str(tmp_path), manifest_path=str(man), iterations=1)
    assert runs == 1


def test_incremental_tombstones_deleted_file(tmp_path):
    a = tmp_path / "a.md"; b = tmp_path / "b.md"
    a.write_text("Alpha only.", encoding="utf-8")
    b.write_text("Bravo only.", encoding="utf-8")
    man = tmp_path / "manifest.json"
    kg = _FakeKG()
    r1 = inc.ingest_incremental(kg, str(tmp_path), manifest_path=str(man))
    assert r1["nodes_added"] == 2
    live_before = len(kg.live)
    # delete file b from disk, re-ingest
    b.unlink()
    r2 = inc.ingest_incremental(kg, str(tmp_path), manifest_path=str(man))
    assert r2["nodes_removed"] == 1
    assert len(kg.live) == live_before - 1
    import json as _j
    saved = _j.loads(man.read_text())
    assert all("b.md" not in k for k in saved)  # b's manifest entry purged


from grap_mcp.application import GrapApplication
s = GrapApplication()


def test_reingest_documents_tool(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("Alpha block.\n\nBeta block.", encoding="utf-8")
    fake = _FakeKG()
    fake.save_to_dir = lambda p: None  # tolerate save call
    monkeypatch.setitem(s._state, "kg", fake)
    monkeypatch.setitem(s._state, "kg_path", str(tmp_path))
    out = s.reingest_documents(str(tmp_path))
    assert out["status"] == "ok"
    assert out["nodes_added"] == 2 and out["nodes_removed"] == 0
