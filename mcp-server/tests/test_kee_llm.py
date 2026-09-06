"""Phase 2: provider-agnostic LLM layer — stub contract + HTTP request shaping."""

import io
import json
import pytest

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


def test_retries_transient_500_then_succeeds(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise llm.urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
        return _FakeResp(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert p.complete("s", "u") == "ok"
    assert calls["n"] == 2      # retried once, then succeeded


def test_retries_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise llm.urllib.error.URLError("timed out")
        return _FakeResp(json.dumps({"choices": [{"message": {"content": "done"}}]}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert p.complete("s", "u") == "done"
    assert calls["n"] == 3


def test_client_error_4xx_not_retried(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise llm.urllib.error.HTTPError(req.full_url, 400, "bad request", {}, None)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    try:
        p.complete("s", "u")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
    assert calls["n"] == 1      # 4xx is permanent — no retry


def test_exhausts_retries_then_raises(monkeypatch):
    monkeypatch.setenv("GRAP_LLM_RETRIES", "2")
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise llm.urllib.error.HTTPError(req.full_url, 503, "unavailable", {}, None)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    try:
        p.complete("s", "u")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "after 3 attempts" in str(e)
    assert calls["n"] == 3       # retries=2 -> 1 + 2 = 3 attempts


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


def test_stub_stream_reassembles_to_complete():
    p = llm.StubProvider(default='{"a": 1}')
    out = list(p.stream("s", "u"))
    assert "".join(out) == '{"a": 1}'
    assert len(out) >= 1


def test_openai_compat_stream_parses_sse_deltas(monkeypatch):
    sse = (
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n'
        'data: {"choices":[{"delta":{}}]}\n'
        'data: [DONE]\n'
    )
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(sse.encode("utf-8")))
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert "".join(p.stream("s", "u")) == "Hello"
    # the request asked the server to stream
    # (verified indirectly: deltas were parsed from SSE frames)


def test_stream_reports_truncation_after_partial_content(monkeypatch):
    sse = ('data: {"choices":[{"delta":{"content":"partial"}}]}\n'
           'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n'
           'data: [DONE]\n')
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(sse.encode()))
    pieces = llm.OpenAICompatProvider(base_url="http://x/v1", model="m").stream("s", "u")
    assert next(pieces) == "partial"
    with pytest.raises(RuntimeError, match="finish_reason='length'"):
        next(pieces)


def test_extra_body_env_merges_into_payload(monkeypatch):
    # llama.cpp reasoning models (e.g. Bonsai): disable thinking per request.
    monkeypatch.setenv("GRAP_LLM_EXTRA_BODY",
                       '{"chat_template_kwargs": {"enable_thinking": false}}')
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    payload = p._payload("sys", "usr", stream=True)
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["stream"] is True


def test_extra_body_absent_and_invalid(monkeypatch):
    monkeypatch.delenv("GRAP_LLM_EXTRA_BODY", raising=False)
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert "chat_template_kwargs" not in p._payload("s", "u", stream=False)

    monkeypatch.setenv("GRAP_LLM_EXTRA_BODY", "{not json")
    assert llm._extra_body_from_env() == {}
    monkeypatch.setenv("GRAP_LLM_EXTRA_BODY", '["a-list-not-a-dict"]')
    assert llm._extra_body_from_env() == {}


def test_timeout_env_override(monkeypatch):
    monkeypatch.delenv("GRAP_LLM_TIMEOUT", raising=False)
    assert llm.OpenAICompatProvider(base_url="http://x/v1", model="m").timeout == 120.0
    monkeypatch.setenv("GRAP_LLM_TIMEOUT", "300")
    assert llm.OpenAICompatProvider(base_url="http://x/v1", model="m").timeout == 300.0
    monkeypatch.setenv("GRAP_LLM_TIMEOUT", "nonsense")
    assert llm.OpenAICompatProvider(base_url="http://x/v1", model="m").timeout == 120.0
    # explicit ctor arg wins
    assert llm.OpenAICompatProvider(base_url="http://x/v1", model="m",
                                    timeout=17.0).timeout == 17.0


def test_explicit_extra_body_wins_over_env(monkeypatch):
    monkeypatch.setenv("GRAP_LLM_EXTRA_BODY", '{"top_k": 1}')
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m",
                                 extra_body={"top_k": 20})
    assert p._payload("s", "u", stream=False)["top_k"] == 20


def test_list_models_parses_openai_shape(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp(json.dumps(
                            {"data": [{"id": "llama3.1:8b"}, {"id": "qwen3:8b"},
                                      {"nope": 1}]}).encode("utf-8")))
    assert llm.list_models(base_url="http://x/v1") == ["llama3.1:8b", "qwen3:8b"]


def test_max_tokens_default_and_env_override(monkeypatch):
    monkeypatch.delenv("GRAP_LLM_MAX_TOKENS", raising=False)
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert p.max_tokens == 2048
    assert p._payload("s", "u", stream=False)["max_tokens"] == 2048

    monkeypatch.setenv("GRAP_LLM_MAX_TOKENS", "512")
    assert llm.OpenAICompatProvider(base_url="http://x/v1", model="m").max_tokens == 512

    monkeypatch.setenv("GRAP_LLM_MAX_TOKENS", "nonsense")
    assert llm.OpenAICompatProvider(base_url="http://x/v1", model="m").max_tokens == 2048


def test_max_tokens_zero_disables_the_cap(monkeypatch):
    monkeypatch.setenv("GRAP_LLM_MAX_TOKENS", "0")
    p = llm.OpenAICompatProvider(base_url="http://x/v1", model="m")
    assert "max_tokens" not in p._payload("s", "u", stream=False)
