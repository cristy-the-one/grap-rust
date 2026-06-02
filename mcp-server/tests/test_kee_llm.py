"""Phase 2: provider-agnostic LLM layer — stub contract + HTTP request shaping."""

import io
import json

import grap_mcp.kee.llm as llm


def test_stub_default_and_records_calls():
    p = llm.StubProvider(default='{"ok": true}')
    assert p.complete("sys", "user") == '{"ok": true}'
    assert p.calls == [("sys", "user")]


def test_stub_responses_in_order_then_repeats_last():
    p = llm.StubProvider(responses=["a", "b"])
    assert [p.complete("s", "u") for _ in range(3)] == ["a", "b", "b"]


def test_stub_handler():
    p = llm.StubProvider(handler=lambda s, u: u.upper())
    assert p.complete("s", "hello") == "HELLO"


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def test_openai_compat_request_shaping(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp(json.dumps(
            {"choices": [{"message": {"content": "hi there"}}]}).encode("utf-8"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)

    p = llm.OpenAICompatProvider(base_url="http://host:1234/v1", model="m1",
                                 api_key="secret")
    out = p.complete("be terse", "say hi")

    assert out == "hi there"
    assert captured["url"] == "http://host:1234/v1/chat/completions"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "m1"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "say hi"},
    ]
    assert captured["body"]["temperature"] == 0.0


def test_openai_compat_bad_shape_raises(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(b'{"nope": 1}'))
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    try:
        p.complete("s", "u")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "unexpected LLM response" in str(e)


def test_get_provider_stub_via_env(monkeypatch):
    monkeypatch.setenv("GRAP_LLM_STUB", "1")
    assert isinstance(llm.get_provider(), llm.StubProvider)
    monkeypatch.delenv("GRAP_LLM_STUB", raising=False)
    assert isinstance(llm.get_provider(), llm.OpenAICompatProvider)


def test_get_provider_model_override(monkeypatch):
    monkeypatch.delenv("GRAP_LLM_STUB", raising=False)
    p = llm.get_provider(model="qwen3:8b")
    assert isinstance(p, llm.OpenAICompatProvider)
    assert p.model == "qwen3:8b"


def test_list_models_parses_openai_shape(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(json.dumps(
                            {"data": [{"id": "llama3.1:8b"}, {"id": "qwen3:8b"},
                                      {"nope": 1}]}).encode("utf-8")))
    assert llm.list_models(base_url="http://x/v1") == ["llama3.1:8b", "qwen3:8b"]
