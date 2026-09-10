import io
import json

import pytest

from grap_mcp.kee import warmup
from grap_mcp.kee.llm import OpenAICompatProvider


def test_warmup_loads_without_prompts_and_checks_joint_residency(monkeypatch):
    requests = []
    responses = [{"done": True, "done_reason": "load"}, {"embeddings": []},
                 {"models": [{"name": "grap-legal-gemma4:latest"}, {"name": "bge-m3:latest"}]}]
    def request(req, timeout):
        requests.append(req)
        return io.BytesIO(json.dumps(responses.pop(0)).encode())
    monkeypatch.setattr(warmup.urllib.request, "urlopen", request)
    result = warmup.warm_models()
    assert result["ready"] is True
    assert json.loads(requests[0].data) == {"model": "grap-legal-gemma4", "keep_alive": 600}
    assert json.loads(requests[1].data) == {"model": "bge-m3", "keep_alive": 600, "input": []}
    assert requests[2].endswith("/api/ps")


def test_warmup_reports_eviction_instead_of_claiming_ready(monkeypatch):
    responses = [{"done": True}, {"embeddings": []}, {"models": [{"name": "bge-m3:latest"}]}]
    monkeypatch.setattr(warmup.urllib.request, "urlopen",
                        lambda *a, **k: io.BytesIO(json.dumps(responses.pop(0)).encode()))
    assert warmup.warm_models()["missing_models"] == ["grap-legal-gemma4"]


@pytest.mark.parametrize("seconds", [0, -1, 3601, True, 2.5])
def test_warmup_rejects_unbounded_or_invalid_residency(seconds):
    with pytest.raises(ValueError):
        warmup.warm_models(keep_alive_seconds=seconds)


def test_generation_config_records_final_overrides_without_messages_or_secrets():
    provider = OpenAICompatProvider(temperature=0, api_key="secret", extra_body={
        "temperature": 0.2, "top_p": 0.95, "seed": 42, "arbitrary_secret": "hidden"})
    config = provider.with_json_schema({"type": "object"}).generation_config()
    assert config["temperature"] == 0.2 and config["top_p"] == 0.95 and config["seed"] == 42
    assert "messages" not in config and "arbitrary_secret" not in config
    assert "secret" not in json.dumps(config)
