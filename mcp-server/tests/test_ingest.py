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
