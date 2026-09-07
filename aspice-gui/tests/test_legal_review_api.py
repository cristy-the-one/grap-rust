import json

from fastapi.testclient import TestClient
import pytest

from backend import bridge
from backend.main import app
from grap_mcp.application import GrapApplication
from grap_mcp.kee.llm import StubProvider


@pytest.fixture
def review_client(tmp_path, monkeypatch):
    source = tmp_path / "sources"
    source.mkdir()
    (source / "act.json").write_text(json.dumps({"id": "test", "titlu": "Norme", "text_complet": "Normele nu se aplică băncilor."}), encoding="utf-8")
    provider = StubProvider(default='{"units":{"u0":{"kind":"prohibition","subject":"Normele"}}}')
    monkeypatch.setattr(bridge, "application", GrapApplication(tmp_path / "workspace", role="operator", provider_factory=lambda **_: provider))
    return TestClient(app), source


def test_review_page_and_end_to_end_http_flow(review_client):
    client, source = review_client
    assert "Read the source" in client.get("/legal-review").text
    assert client.get("/assets/legal-review.js").status_code == 200
    campaign = client.post("/api/legal-review", json={"path": str(source), "sample_size": 1}).json()
    assert campaign["status"] == "ok"
    cid = campaign["campaign"]["campaign_id"]
    detail = client.post(f"/api/legal-review/{cid}/0001/process").json()
    claim = detail["receipt"]["claims"][0]
    assert claim["kind"] == "fact"
    response = client.post(f"/api/legal-review/{cid}/0001/save", json={
        "revision_id": detail["revision_id"], "expected_version": 0, "reviewer": "Test reviewer",
        "judgments": [{"claim_id": claim["claim_id"], "kind": "correct", "subject": "uncertain", "qualifications": "correct"}],
        "omissions": [], "complete": True}).json()
    assert response["status"] == "ok" and response["version"] == 1
    exported = client.get("/api/legal-review", params={"campaign_id": cid, "export": True}).json()
    assert exported["summary"]["claim_judgments"]["joint"]["uncertain"] == 1
    assert exported["items"][0]["source_text"] == "Normele nu se aplică băncilor."
    assert len(exported["items"][0]["history"]) == 1


def test_http_bounds_and_cross_workspace_isolation(review_client, tmp_path, monkeypatch):
    client, source = review_client
    assert client.post("/api/legal-review", json={"path": str(source), "sample_size": 0}).status_code == 422
    campaign = client.post("/api/legal-review", json={"path": str(source), "sample_size": 1}).json()
    cid = campaign["campaign"]["campaign_id"]
    monkeypatch.setattr(bridge, "application", GrapApplication(tmp_path / "other", role="operator"))
    assert client.post(f"/api/legal-review/{cid}/0001/process").json()["status"] == "error"
    assert client.get("/api/legal-review").json()["campaigns"] == []
