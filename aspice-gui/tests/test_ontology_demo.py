"""Self-contained ontology demo: corpus, snapshot, endpoints."""

from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "demo" / "corpus" / "ro-law"


def test_rolaw_corpus_is_law_shaped_and_readable():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp-server"))
    import json
    from grap_mcp.kee.pipeline import read_chunks

    files = sorted(CORPUS.glob("*.json"))
    assert len(files) >= 3, "expect at least 3 ro-law sample files"
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        assert obj.get("id") and obj.get("titlu") and obj.get("text_complet")

    chunks = read_chunks(str(CORPUS))
    assert chunks, "read_chunks must yield chunks from the ro-law corpus"
    labels = [src for src, _ in chunks]
    assert any(l.split("#")[0] for l in labels)


def test_ontology_snapshot_maps_names_to_oids():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.ontology_view import ontology_snapshot

    export = {
        "status": "ok",
        "ontology": {
            "version": "v",
            "process_outcomes": [
                {"id": "operator_de_date", "name": "Operator de date",
                 "description": "...", "required_characteristics": ["securitate"]},
                {"id": "persoana_vizata", "name": "Persoana vizata",
                 "description": "...", "required_characteristics": []},
            ],
            "provenance": {"operator_de_date": ["L-2001-001#0"],
                           "persoana_vizata": ["L-2010-077#0", "L-2001-001#0"]},
            "relations": [{"from": "Operator de date", "type": "raspunde",
                           "to": "Persoana vizata", "sources": ["L-2010-077#0"]}],
        },
        "added": 3, "changed": 0, "removed": 0, "concepts": 2, "edges": 1,
        "relooks": 0, "parked": 0,
    }
    snap = ontology_snapshot(export)
    ids = {n["id"] for n in snap["nodes"]}
    assert ids == {"operator_de_date", "persoana_vizata"}
    op = next(n for n in snap["nodes"] if n["id"] == "operator_de_date")
    assert op["name"] == "Operator de date"
    assert op["characteristics"] == ["securitate"]
    assert op["provenance"] == ["L-2001-001#0"]
    assert snap["edges"] == [{"source": "operator_de_date", "type": "raspunde",
                              "target": "persoana_vizata", "sources": ["L-2010-077#0"]}]
    assert snap["stats"]["concepts"] == 2 and snap["stats"]["edges"] == 1


def test_ontology_snapshot_drops_unresolvable_edge():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.ontology_view import ontology_snapshot
    export = {"ontology": {"process_outcomes": [{"id": "a", "name": "A",
              "required_characteristics": []}], "provenance": {"a": []},
              "relations": [{"from": "A", "type": "x", "to": "Ghost", "sources": []}]}}
    snap = ontology_snapshot(export)
    assert snap["edges"] == []


def test_extend_endpoint_streams_and_persists(tmp_path, monkeypatch):
    import os, sys, tempfile, json as _json
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    # match test_app.py: aspice-gui/ on path for `backend.main`, mcp-server/ for grap_mcp
    sys.path.insert(0, str(root / "aspice-gui"))
    sys.path.insert(0, str(root / "mcp-server"))
    os.environ.setdefault("GRAP_WORKSPACE", tempfile.mkdtemp(prefix="gui-test-ws-"))

    import grap_mcp.kee.llm as llm
    import grap_mcp.kee.embeddings as emb
    from grap_mcp.kee.llm import StubProvider
    from grap_mcp.kee.embeddings import HashingEmbedder

    def _handler(system, user):
        if "operator" in user.lower():
            return _json.dumps({"outcomes": [{"name": "Operator de date",
                                              "characteristics": ["securitate"]}],
                                "relations": []})
        return "{}"

    monkeypatch.setattr(llm, "get_provider", lambda model=None: StubProvider(handler=_handler))
    monkeypatch.setattr(emb, "get_embedder", lambda *a, **k: HashingEmbedder())

    from fastapi.testclient import TestClient
    from backend.main import app
    client = TestClient(app)

    wd = str(tmp_path / "graph")
    docs = tmp_path / "docs"; docs.mkdir()
    # one document -> one chunk (read_chunks emits a chunk per non-empty field,
    # so omit `titlu` to keep `added == 1`)
    (docs / "a.json").write_text(_json.dumps(
        {"id": "L-1", "text_complet": "Operatorul de date raspunde."}),
        encoding="utf-8")

    with client.stream("POST", "/api/ontology/extend",
                       json={"workdir": wd, "corpus_path": str(docs), "sim": 0.9,
                             "chunk_strategy": "statements"}) as r:
        events = [_json.loads(line) for line in r.iter_lines() if line]
    phases = [e.get("phase") or e.get("type") for e in events]
    assert "diff" in phases and "done" in phases
    done = next(e for e in events if e.get("phase") == "done")
    assert done["added"] == 1
    assert done["chunking"]["chunk_strategy"] == "statements"
    assert "snapshot" in done and any(n["name"] == "Operator de date"
                                      for n in done["snapshot"]["nodes"])


def test_extend_honors_chunk_controls(tmp_path, monkeypatch):
    # chunk_chars and max_files must reach the extraction pipeline: two long
    # docs but max_files=1 + a huge chunk size -> exactly one chunk extracted
    import os, sys, tempfile, json as _json
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "aspice-gui"))
    sys.path.insert(0, str(root / "mcp-server"))
    os.environ.setdefault("GRAP_WORKSPACE", tempfile.mkdtemp(prefix="gui-test-ws-"))

    import grap_mcp.kee.llm as llm
    import grap_mcp.kee.embeddings as emb
    from grap_mcp.kee.llm import StubProvider
    from grap_mcp.kee.embeddings import HashingEmbedder
    monkeypatch.setattr(llm, "get_provider", lambda model=None: StubProvider(
        handler=lambda s, u: _json.dumps({"outcomes": [{"name": "Operator de date",
                                          "characteristics": []}], "relations": []})))
    monkeypatch.setattr(emb, "get_embedder", lambda *a, **k: HashingEmbedder())

    from fastapi.testclient import TestClient
    from backend.main import app
    client = TestClient(app)

    docs = tmp_path / "docs"; docs.mkdir()
    long_text = "Operatorul de date raspunde. " * 100          # ~2.9K chars
    (docs / "a.json").write_text(_json.dumps({"id": "L-1", "text_complet": long_text}),
                                 encoding="utf-8")
    (docs / "b.json").write_text(_json.dumps({"id": "L-2", "text_complet": long_text}),
                                 encoding="utf-8")

    with client.stream("POST", "/api/ontology/extend",
                       json={"workdir": str(tmp_path / "g"), "corpus_path": str(docs),
                             "sim": 0.9, "chunk_chars": 20_000, "max_files": 1}) as r:
        events = [_json.loads(line) for line in r.iter_lines() if line]
    done = next(e for e in events if e.get("phase") == "done")
    assert done["added"] == 1


def test_extend_endpoint_passes_concurrency(tmp_path, monkeypatch):
    # `concurrency` must reach the extraction pipeline (windowed parallel LLM
    # calls); spy on extend_graph_ontology to verify the passthrough.
    import os, sys, tempfile, json as _json
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "aspice-gui"))
    sys.path.insert(0, str(root / "mcp-server"))
    os.environ.setdefault("GRAP_WORKSPACE", tempfile.mkdtemp(prefix="gui-test-ws-"))

    import grap_mcp.kee.llm as llm
    import grap_mcp.kee.embeddings as emb
    import grap_mcp.kee.graph_extract as gx
    from grap_mcp.kee.llm import StubProvider
    from grap_mcp.kee.embeddings import HashingEmbedder
    monkeypatch.setattr(llm, "get_provider", lambda model=None: StubProvider(
        handler=lambda s, u: _json.dumps({"outcomes": [{"name": "Operator de date",
                                          "characteristics": []}], "relations": []})))
    monkeypatch.setattr(emb, "get_embedder", lambda *a, **k: HashingEmbedder())

    seen = {}
    real = gx.extend_graph_ontology

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(gx, "extend_graph_ontology", spy)

    from fastapi.testclient import TestClient
    from backend.main import app
    client = TestClient(app)

    docs = tmp_path / "docs"; docs.mkdir()
    for i in range(3):
        (docs / f"d{i}.json").write_text(_json.dumps(
            {"id": f"L-{i}", "text_complet": f"Operatorul de date {i}."}),
            encoding="utf-8")

    with client.stream("POST", "/api/ontology/extend",
                       json={"workdir": str(tmp_path / "g"), "corpus_path": str(docs),
                             "sim": 0.9, "concurrency": 3}) as r:
        events = [_json.loads(line) for line in r.iter_lines() if line]
    assert seen.get("concurrency") == 3
    done = next(e for e in events if e.get("phase") == "done")
    assert done["added"] == 3


def test_open_snapshot_export(tmp_path, monkeypatch):
    import sys, os, tempfile, json as _json
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "aspice-gui"))
    sys.path.insert(0, str(root / "mcp-server"))
    os.environ.setdefault("GRAP_WORKSPACE", tempfile.mkdtemp(prefix="demo-ws-"))

    import grap_mcp.kee.llm as llm
    import grap_mcp.kee.embeddings as emb
    from grap_mcp.kee.llm import StubProvider
    from grap_mcp.kee.embeddings import HashingEmbedder
    monkeypatch.setattr(llm, "get_provider", lambda model=None: StubProvider(
        handler=lambda s, u: _json.dumps({"outcomes": [{"name": "Operator de date",
                                          "characteristics": []}], "relations": []})))
    monkeypatch.setattr(emb, "get_embedder", lambda *a, **k: HashingEmbedder())

    from fastapi.testclient import TestClient
    from backend.main import app
    client = TestClient(app)

    wd = str(tmp_path / "graph")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.json").write_text(_json.dumps({"id": "L-1",
        "text_complet": "Operatorul de date."}), encoding="utf-8")
    with client.stream("POST", "/api/ontology/extend",
                       json={"workdir": wd, "corpus_path": str(docs), "sim": 0.9}) as r:
        list(r.iter_lines())

    opened = client.post("/api/ontology/open", json={"workdir": wd}).json()
    assert opened["status"] == "ok" and opened["concepts"] == 1

    snap = client.get("/api/ontology/snapshot", params={"workdir": wd}).json()
    assert any(n["name"] == "Operator de date" for n in snap["nodes"])

    out = tmp_path / "out.yaml"
    exp = client.get("/api/ontology/export",
                     params={"workdir": wd, "out_path": str(out)}).json()
    assert exp["status"] == "ok" and out.exists()


def test_index_serves_app_js():
    import sys, os, tempfile
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "aspice-gui"))
    sys.path.insert(0, str(root / "mcp-server"))
    os.environ.setdefault("GRAP_WORKSPACE", tempfile.mkdtemp(prefix="demo-ws-"))
    from fastapi.testclient import TestClient
    from backend.main import app
    client = TestClient(app)
    html = client.get("/").text
    assert "/assets/app.js" in html
