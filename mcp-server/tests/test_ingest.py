from grap_mcp.ingest import chunk_text, ingest_paths


class FakeKG:
    def __init__(self):
        self._n = 0
        self.nodes = []

    def add_node(self, text, label=None):
        self._n += 1
        self.nodes.append((text, label))
        return self._n


def test_chunk_splits_on_blank_lines_and_drops_empty():
    text = "Para one.\n\nPara two.\n\n\n   \n\nPara three."
    chunks = chunk_text(text)
    assert chunks == ["Para one.", "Para two.", "Para three."]


def test_chunk_caps_long_paragraph():
    chunks = chunk_text("x" * 50 + " " + "y" * 50, max_chars=60)
    assert len(chunks) == 2
    assert all(len(c) <= 60 for c in chunks)


def test_ingest_paths_reads_md_files(tmp_path):
    (tmp_path / "a.md").write_text("Alpha requirement.\n\nSecond chunk.", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Beta note.", encoding="utf-8")
    (tmp_path / "ignore.png").write_text("nope", encoding="utf-8")
    kg = FakeKG()
    res = ingest_paths(kg, str(tmp_path))
    assert res["files"] == 2
    assert res["nodes_added"] == 3
    assert all(lbl.startswith("doc:") for (_t, lbl) in kg.nodes)


import os


def test_ingest_feeds_reasoner(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", "operator")
    import importlib
    import grap_mcp.server as s
    importlib.reload(s)

    onto = os.path.join(os.path.dirname(__file__), "..", "..",
                        "docs", "examples", "minimal-aspice-ontology.yaml")
    pg = tmp_path / "p.pg.yaml"
    pg.write_text(
        'name: P\nversion: 0.1.0\nsteps:\n  - id: a\n    operation: op\n'
        '    references: ["kg:aspice.process_outcome.SYS.5.BP3"]\n', encoding="utf-8")

    kgp = str(tmp_path / "kg")
    assert s.create_knowledge_graph(kgp)["status"] == "ok"
    assert s.load_ontology(onto)["status"] == "ok"
    assert s.load_procedure_graph(str(pg))["status"] == "ok"
    empty = s.detect_gaps()["gaps_found"]

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "req.md").write_text(
        "The verification method is specified for each requirement. "
        "Acceptance criteria are defined. The test environment is identified.",
        encoding="utf-8")
    assert s.ingest_documents(str(docs))["status"] == "ok"
    after = s.detect_gaps()["gaps_found"]

    assert empty >= 1
    assert after < empty  # ingested evidence covers characteristics -> fewer gaps
