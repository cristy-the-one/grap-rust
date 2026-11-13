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


def test_ingest_paths_reads_law_json(tmp_path):
    # Law-ro pilot finding 13: the corpus was .json (titlu + text_complet) and
    # evidence ingestion silently matched zero files — gap detection then ran
    # against an empty KG for seven tuning cycles. Evidence ingestion must read
    # the same JSON text the KEE extraction side reads.
    import json
    (tmp_path / "act.json").write_text(json.dumps({
        "id": "145", "titlu": "Legea asigurarilor sociale",
        "text_complet": "Contributiile se colecteaza de Ministerul Finantelor.",
    }), encoding="utf-8")
    kg = FakeKG()
    res = ingest_paths(kg, str(tmp_path))
    assert res["files"] == 1 and res["nodes_added"] >= 1
    all_text = " ".join(t for (t, _l) in kg.nodes)
    assert "Ministerul Finantelor" in all_text
    assert "text_complet" not in all_text        # extracted, not raw JSON


def test_ingest_incremental_reads_law_json(tmp_path):
    import json
    from grap_mcp.incremental import ingest_incremental

    class RemovableKG(FakeKG):
        def remove_node(self, nid):
            pass

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "act.json").write_text(json.dumps({
        "id": "72", "titlu": "Ordonanta de urgenta",
        "text_complet": "Contractul-cadru va fi elaborat pana la 15 februarie 1999.",
    }), encoding="utf-8")
    kg = RemovableKG()
    res = ingest_incremental(kg, str(tmp_path / "docs"),
                             manifest_path=str(tmp_path / "manifest.json"))
    assert res["files"] == 1 and res["nodes_added"] >= 1
    assert any("Contractul-cadru" in t for (t, _l) in kg.nodes)
    # unchanged corpus re-ingest: manifest skips everything
    res2 = ingest_incremental(kg, str(tmp_path / "docs"),
                              manifest_path=str(tmp_path / "manifest.json"))
    assert res2["nodes_added"] == 0 and res2["skipped"] >= 1


import os


def test_ingest_feeds_reasoner(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", "operator")
    from grap_mcp.application import GrapApplication
    s = GrapApplication()

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


class CountingEmbKG:
    """KG that accepts embeddings, paired with an embed-call counter."""

    def __init__(self):
        self.nodes = []

    def add_node(self, text, label=None, embedding=None):
        self.nodes.append((text, label, embedding))
        return len(self.nodes)


class CountingEmbedder:
    def __init__(self):
        self.calls = 0
        self.texts = 0

    def embed(self, texts):
        texts = list(texts)
        self.calls += 1
        self.texts += len(texts)
        return [[float(len(t))] for t in texts]


def test_embedding_batches_span_files(tmp_path):
    """A local /embeddings round-trip costs ~2.2s regardless of size, so many
    small files must not mean one request per file."""
    for i in range(10):
        (tmp_path / f"doc{i}.md").write_text(f"Chunk for doc {i}.", encoding="utf-8")
    kg, emb = CountingEmbKG(), CountingEmbedder()
    res = ingest_paths(kg, str(tmp_path), embedder=emb)

    assert res["nodes_added"] == 10
    assert emb.texts == 10
    # 10 files, 1 chunk each -> must be ONE batched request, not ten.
    assert emb.calls == 1, f"expected 1 cross-file batch, got {emb.calls}"


def test_embedding_batch_size_is_bounded(tmp_path):
    """Batches stay capped so payload/peak memory do not grow with the corpus."""
    for i in range(10):
        (tmp_path / f"doc{i}.md").write_text(f"Chunk for doc {i}.", encoding="utf-8")
    kg, emb = CountingEmbKG(), CountingEmbedder()
    ingest_paths(kg, str(tmp_path), embedder=emb, batch_size=3)

    assert emb.texts == 10
    assert emb.calls == 4          # ceil(10/3)
    # embeddings still land on the right nodes, in order
    assert [n[2][0] for n in kg.nodes] == [float(len(n[0])) for n in kg.nodes]


# Same two-message file the DBC reader test parses. Signal order is file order.
DBC = """\
VERSION "REL-2026.11"

NS_ :

BS_:

BU_: Engine Gateway Abs

BO_ 256 EngineData: 8 Engine
 SG_ EngineTemp : 16|8@1- (1,-40) [-40|215] "C" Gateway
 SG_ EngineSpeed : 0|16@1+ (0.1,0) [0|8000] "rpm" Gateway,Abs

BO_ 512 VehicleStatus: 4 Gateway
 SG_ Ignition : 7|2@0+ (1,0) [0|3] "" Engine

CM_ BO_ 256 "Engine measurements";
CM_ SG_ 256 EngineSpeed "Crankshaft speed";

BA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;
BA_DEF_DEF_ "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 256 10;

VAL_ 512 Ignition 0 "Off" 1 "Run" 3 "Start" ;
"""


def _reader_node_text(item):
    """item_id, kind_hint, text, then one key=value attribute per line."""
    lines = [item.item_id, item.kind_hint, item.text]
    lines.extend(f"{key}={value}" for key, value in item.attributes.items())
    return "\n".join(lines)


def test_dbc_fixture_yields_items(tmp_path):
    """R-06: a .dbc file yields one node per item, and each node contains its name."""
    from grap_mcp.readers.dbc import read_dbc

    path = tmp_path / "vehicle.dbc"
    path.write_text(DBC, encoding="utf-8")
    items = read_dbc(path, corpus_root=tmp_path)
    kg = FakeKG()
    res = ingest_paths(kg, str(tmp_path))
    assert res["files"] == 1
    assert res["nodes_added"] == len(items)
    assert len(kg.nodes) == len(items)
    for item, (text, label) in zip(items, kg.nodes, strict=True):
        assert item.item_id in text
        assert label == item.kind_hint
        assert text == _reader_node_text(item)
