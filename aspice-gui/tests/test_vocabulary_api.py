import hashlib
import json

from fastapi.testclient import TestClient

from backend import bridge
from backend.main import app
from grap_mcp.application import GrapApplication
from grap_mcp.kee.llm import StubProvider


def test_vocabulary_endpoint_budget_and_cache(tmp_path, monkeypatch):
    text = "Guvernul emite actul."
    record = {"source_text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest(),
              "outcomes": [{"name": "Guvernul", "evidence": {
                  "quote": text, "start": 0, "end": len(text), "status": "quoted"}}], "relations": []}
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps({"extraction_evidence": {"act#0": record}}), encoding="utf-8")
    def answer(system, user):
        item = json.loads(user)["items"][0]
        return json.dumps({"items": [{"id": item["id"], "status": "entity", "label": "Autoritate",
                                     "definition": "O autoritate publică.", "reason": "Guvernul"}]})
    provider = StubProvider(handler=answer)
    monkeypatch.setattr(bridge, "application", GrapApplication(tmp_path / "ws", role="operator", provider_factory=lambda **_: provider))
    client = TestClient(app)
    assert client.post("/api/ontology/distill", json={"path": str(path), "max_calls": 101}).status_code == 422
    pending = client.post("/api/ontology/distill", json={"path": str(path), "max_calls": 0}).json()
    assert pending["report"]["pending"] == 1 and not provider.calls
    ready = client.post("/api/ontology/distill", json={"path": str(path), "max_calls": 1}).json()
    assert ready["report"]["status"] == "complete"
    assert ready["report"]["counts"]["classes"] == 1
    assert "Discover / Resume" in client.get("/").text
