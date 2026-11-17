"""LLM-01: complete() keeps token usage; procedure_model reads last_usage.

No network. urllib is replaced with a fake HTTP response.
"""

import io
import json

import grap_mcp.kee.llm as llm
from grap_mcp.procedure_model import _call, _token_counts


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def _http(content, usage=None):
    body = {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": content},
        }],
    }
    if usage is not None:
        body["usage"] = usage
    return _FakeResp(json.dumps(body).encode("utf-8"))


_DRAFT = {
    "title": "Lamp",
    "cases": [{
        "id": "TC-1",
        "verifies": ["REQ-1"],
        "preconditions": "power on",
        "stimulus": "press",
        "expected_observable_result": "lamp off",
        "pass_criterion": "lamp is off",
        "variant": "",
        "kind": "positive",
        "uses": [],
    }],
}


def test_complete_keeps_usage_from_a_fake_http_response(monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: _http(
            "hi there",
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        ),
    )
    provider = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert provider.complete("be terse", "say hi") == "hi there"
    assert provider.last_usage == {"prompt": 11, "completion": 7}


def test_complete_clears_usage_when_the_server_sends_none(monkeypatch):
    replies = [
        _http("first", {"prompt_tokens": 11, "completion_tokens": 7}),
        _http("second"),
    ]
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: replies.pop(0),
    )
    provider = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert provider.complete("s", "one") == "first"
    assert provider.last_usage == {"prompt": 11, "completion": 7}
    assert provider.complete("s", "two") == "second"
    assert provider.last_usage is None


def test_token_counts_reads_last_usage_not_usage_or_token_counts():
    class Provider:
        usage = {"prompt": 1, "completion": 2}
        token_counts = {"prompt": 3, "completion": 4}
        last_usage = {"prompt": 11, "completion": 7}

    assert _token_counts(Provider()) == {"prompt": 11, "completion": 7}

    class Stale:
        usage = {"prompt": 1, "completion": 2}
        token_counts = {"prompt": 3, "completion": 4}
        last_usage = None

    assert _token_counts(Stale()) == "n/a"


def test_procedure_model_reads_last_usage_from_the_completed_call(monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: _http(
            json.dumps(_DRAFT),
            {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        ),
    )
    provider = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    draft, receipt = _call("write the lamp test", provider, {"level": "software"})
    assert draft["title"] == "Lamp"
    assert receipt["token_counts"] == {"prompt": 11, "completion": 7}


def test_procedure_model_usage_is_na_when_the_server_sends_none(monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda req, timeout=None: _http(json.dumps(_DRAFT)),
    )
    provider = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    draft, receipt = _call("write the lamp test", provider, {"level": "software"})
    assert draft["title"] == "Lamp"
    assert receipt["token_counts"] == "n/a"
