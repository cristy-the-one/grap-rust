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
