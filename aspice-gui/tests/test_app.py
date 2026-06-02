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
