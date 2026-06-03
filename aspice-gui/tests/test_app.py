import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["GRAP_WORKSPACE"] = tempfile.mkdtemp(prefix="gui-test-ws-")
os.environ["GRAP_ROLE"] = "operator"

from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402

client = TestClient(app)


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
