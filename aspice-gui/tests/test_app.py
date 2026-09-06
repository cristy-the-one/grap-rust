import json
import os
import sys
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GRAP_WORKSPACE"] = tempfile.mkdtemp(prefix="gui-test-ws-")
os.environ["GRAP_ROLE"] = "operator"

from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402

client = TestClient(app)


def test_streamed_extraction_keeps_checked_source_evidence(tmp_path, monkeypatch):
    import yaml
    from grap_mcp.kee import llm, embeddings
    source = "The Council issues Order 17."
    (tmp_path / "act.txt").write_text(source, encoding="utf-8")
    provider = llm.StubProvider(default=json.dumps({"outcomes": [], "relations": [
        {"from": "Council", "type": "issues", "to": "Order 17", "evidence": source},
        {"from": "Ghost", "type": "approves", "to": "Order 17", "evidence": "invented quote"}]}))
    monkeypatch.setattr(llm, "get_provider", lambda **kw: provider)
    monkeypatch.setattr(embeddings, "get_embedder", lambda: embeddings.HashingEmbedder())
    r = client.post("/api/extract/stream", json={"path": str(tmp_path)})
    result = [json.loads(line) for line in r.text.splitlines()][-1]["extracted"]
    assert result["extraction_complete"]
    saved = yaml.safe_load(Path(result["out_path"]).read_text(encoding="utf-8"))
    assert len(saved["relations"]) == 1
    assert saved["relations"][0]["from"] == "Council"
    evidence = saved["extraction_evidence"]["act#0"]
    assert evidence["relations"][0]["evidence"]["quote"] == source
    assert "source_text" not in evidence


@pytest.mark.parametrize("truncated", [False, True])
def test_wizard_does_not_adopt_incomplete_stream(tmp_path, monkeypatch, truncated):
    from backend import bridge
    from grap_mcp.kee import llm, embeddings
    (tmp_path / "plan.txt").write_text("Plan shall record coverage.", encoding="utf-8")

    class BrokenProvider(llm.StubProvider):
        def stream(self, system, user):
            if truncated:
                yield '{"outcomes":[],"relations":[]}'
                raise RuntimeError("finish_reason='length'")
            yield "{}"  # malformed schema must also prevent adoption

    monkeypatch.setattr(llm, "get_provider", lambda **kw: BrokenProvider())
    monkeypatch.setattr(embeddings, "get_embedder", lambda: embeddings.HashingEmbedder())
    operations = []
    call = bridge.call

    def record(operation, **kwargs):
        operations.append(operation)
        return call(operation, **kwargs)

    monkeypatch.setattr(bridge, "call", record)
    r = client.post("/api/wizard/extract/stream", json={"path": str(tmp_path)})
    events = [json.loads(line) for line in r.text.splitlines()]
    assert events[-1]["type"] == "error"
    assert "incomplete" in events[-1]["message"]
    assert "load_ontology" not in operations


def test_health():
    assert client.get("/api/health").json()["status"] == "ok"


def test_bootstrap_populates_kg_and_finds_gaps():
    r = client.post("/api/wizard/bootstrap").json()
    assert r["status"] == "ok"
    kg_step = r["steps"][0]["knowledge_graph"]
    assert kg_step["status"] == "ok"
    ingest_step = r["steps"][1]["ingest"]
    assert ingest_step["nodes_added"] >= 1
    assert r["gaps"]["status"] == "ok"
    assert r["gaps"]["gaps_found"] >= 1


def test_wizard_extract_runs_full_loop_offline():
    # offline + deterministic: stub LLM (no fragments) + hashing embedder
    os.environ["GRAP_LLM_STUB"] = "1"
    os.environ["GRAP_EMBED_BACKEND"] = "hashing"
    try:
        r = client.post("/api/wizard/extract", json={}).json()
    finally:
        os.environ.pop("GRAP_LLM_STUB", None)
        os.environ.pop("GRAP_EMBED_BACKEND", None)
    assert r["status"] == "ok"
    assert [list(s.keys())[0] for s in r["steps"]] == [
        "knowledge_graph", "extract", "adopt_ontology", "ingest_evidence"]
    # one corpus doc is held out as the "unmet" slice; the rest become evidence
    assert "verification.md" not in r["evidence_files"]
    assert r["gaps"]["status"] == "ok"
    assert "process_outcomes" in r["extracted"]


def test_extract_stream_emits_events_and_extracted():
    from backend import bridge
    os.environ["GRAP_LLM_STUB"] = "1"
    os.environ["GRAP_EMBED_BACKEND"] = "hashing"
    try:
        r = client.post("/api/extract/stream",
                        json={"path": bridge.default_examples()["sample_knowledge"]})
    finally:
        os.environ.pop("GRAP_LLM_STUB", None)
        os.environ.pop("GRAP_EMBED_BACKEND", None)
    events = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    types = [e["type"] for e in events]
    assert "extract_begin" in types
    assert "chunk_start" in types and "chunk_done" in types
    assert types[-1] == "extracted"
    extracted = events[-1]["extracted"]
    assert "process_outcomes" in extracted and "relations" in extracted


def test_legal_graph_from_relations_file(tmp_path):
    relfile = tmp_path / "relations.json"
    relfile.write_text(json.dumps([
        {"from": "Guvernul", "type": "emite", "to": "Ordinul 55"},
        {"from": "Ordinul 55", "type": "privind", "to": "Aprobare formulare"},
        {"from": "Bad", "type": "", "to": ""},          # skipped (incomplete)
    ]), encoding="utf-8")
    r = client.post("/api/legal-graph/from-relations",
                    json={"path": str(relfile), "top": 50}).json()
    assert r["status"] == "ok"
    assert r["counts"]["relations"] == 2                # the incomplete one dropped
    assert {e["type"] for e in r["edges"]} == {"emite", "privind"}
    assert r["counts"]["shown_nodes"] >= 3


def test_graph_neighbors_expand_uses_real_ids(tmp_path):
    # build a tiny legal graph (act 2 cites act 1), then expand a node live
    corpus = tmp_path / "acts"
    corpus.mkdir()
    (corpus / "1.json").write_text(json.dumps(
        {"id": "1", "tip_act": "LEGE", "numar": "166", "data_vigoare": "1999-11-09",
         "titlu": "Lege A", "text_complet": "x"}), encoding="utf-8")
    (corpus / "2.json").write_text(json.dumps(
        {"id": "2", "tip_act": "HOTARARE", "numar": "671", "data_vigoare": "2000-08-17",
         "titlu": "HG B", "text_complet": "In temeiul LEGEA nr. 166 din 9 noiembrie 1999"}),
        encoding="utf-8")
    b = client.post("/api/legal-graph/build",
                    json={"path": str(corpus), "max_files": 10, "with_relations": False}).json()
    assert b["status"] == "ok" and b["edges"]
    e = b["edges"][0]                                   # a 'cites' edge
    nb = client.post("/api/graph/neighbors",
                     json={"node_id": e["source"], "types": ["cites"]}).json()
    assert nb["status"] == "ok"
    assert e["target"] in {n["id"] for n in nb["neighbors"]}
    assert all(x["type"] == "cites" for x in nb["edges"])


def test_graph_neighbors_pagination_cursor_fetches_next_page(tmp_path):
    # a hub with >100 neighbors must page: first call returns next_cursor,
    # second call with that cursor returns the remaining (disjoint) neighbors
    relfile = tmp_path / "relations.json"
    relfile.write_text(json.dumps(
        [{"from": "Hub", "type": "rel", "to": f"Spoke {i}"} for i in range(120)]),
        encoding="utf-8")
    r = client.post("/api/legal-graph/from-relations",
                    json={"path": str(relfile), "top": 200}).json()
    assert r["status"] == "ok" and r["counts"]["relations"] == 120
    hub = r["edges"][0]["source"]

    p1 = client.post("/api/graph/neighbors",
                     json={"node_id": hub, "types": ["rel"]}).json()
    assert p1["status"] == "ok"
    assert p1["total"] == 120 and p1["returned"] == 100
    assert p1["next_cursor"]

    p2 = client.post("/api/graph/neighbors",
                     json={"node_id": hub, "types": ["rel"],
                           "cursor": p1["next_cursor"]}).json()
    assert p2["status"] == "ok"
    assert p2["returned"] == 20 and p2["next_cursor"] is None
    ids1 = {n["id"] for n in p1["neighbors"]}
    ids2 = {n["id"] for n in p2["neighbors"]}
    assert len(ids1) == 100 and len(ids2) == 20 and not (ids1 & ids2)

    lim = client.post("/api/graph/neighbors",
                      json={"node_id": hub, "types": ["rel"], "limit": 10}).json()
    assert lim["returned"] == 10 and lim["next_cursor"]


def test_query_events_pagination_cursor_fetches_next_page(tmp_path):
    # record two events of the same type, then page through them one at a time
    for i in range(2):
        relfile = tmp_path / f"relations-{i}.json"
        relfile.write_text(json.dumps(
            [{"from": f"A{i}", "type": "rel", "to": f"B{i}"}]), encoding="utf-8")
        assert client.post("/api/legal-graph/from-relations",
                           json={"path": str(relfile)}).json()["status"] == "ok"
    q = {"event_type": "relations_graph_loaded", "limit": 1}
    p1 = client.post("/api/events/query", json=q).json()
    assert p1["returned"] == 1 and p1["total"] >= 2
    assert p1["next_cursor"]
    p2 = client.post("/api/events/query",
                     json={**q, "cursor": p1["next_cursor"]}).json()
    assert p2["returned"] == 1
    assert p2["events"] != p1["events"]


def test_project_relations_endpoint_adds_edges():
    rels = [{"from": "Agency", "type": "concedes", "to": "Company"},
            {"from": "Agency", "type": "concedes", "to": "Company"},   # dup edge ok
            {"from": "Law 1", "type": "amends", "to": "Law 2"}]
    r = client.post("/api/relations/project", json={"relations": rels}).json()
    assert r["status"] == "ok"
    assert r["edges_added"] == 3
    assert r["nodes_added"] == 4            # Agency, Company, Law 1, Law 2
    assert r["edge_count"] >= 3 and r["node_count"] >= 4


def test_wizard_extract_stream_emits_progress_events():
    os.environ["GRAP_LLM_STUB"] = "1"
    os.environ["GRAP_EMBED_BACKEND"] = "hashing"
    try:
        r = client.post("/api/wizard/extract/stream", json={})
    finally:
        os.environ.pop("GRAP_LLM_STUB", None)
        os.environ.pop("GRAP_EMBED_BACKEND", None)
    events = [json.loads(ln) for ln in r.text.splitlines() if ln.strip()]
    types = [e["type"] for e in events]
    assert "extract_begin" in types
    assert "chunk_start" in types and "chunk_done" in types
    assert types[-1] == "done"
    result = events[-1]["result"]
    assert result["status"] == "ok"
    assert "verification.md" not in result["evidence_files"]
