"""Provider-agnostic LLM access for the knowledge extraction engine.

The universal interface is the OpenAI-compatible `/chat/completions` HTTP
endpoint, which Ollama, vLLM, llama.cpp's server, and hosted APIs all expose.
We speak it with stdlib urllib only — no vendor SDK dependency — so swapping
the backend is purely a matter of GRAP_LLM_BASE_URL / GRAP_LLM_MODEL env vars.

`StubProvider` backs deterministic, offline tests and demo runs.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Protocol, runtime_checkable

DEFAULT_BASE_URL = "http://localhost:11434/v1"   # Ollama's OpenAI-compatible API
DEFAULT_MODEL = "llama3.1"


@runtime_checkable
class LLMProvider(Protocol):
    """Single-turn completion: a system + user prompt in, text out."""

    name: str

    def complete(self, system: str, user: str) -> str:
        ...


class StubProvider:
    """Deterministic provider for tests / offline demo.

    Pass `responses` (returned in order, last one repeats), a `handler`
    callable, or rely on `default`. Records `.calls` for assertions.
    """

    name = "stub"

    def __init__(self, responses: Optional[List[str]] = None,
                 handler: Optional[Callable[[str, str], str]] = None,
                 default: str = "{}") -> None:
        self._responses = list(responses) if responses else None
        self._handler = handler
        self._default = default
        self._i = 0
        self.calls: List[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self._handler is not None:
            return self._handler(system, user)
        if self._responses:
            r = self._responses[min(self._i, len(self._responses) - 1)]
            self._i += 1
            return r
        return self._default


class OpenAICompatProvider:
    """Talks to any OpenAI-compatible /chat/completions server via urllib."""

    name = "openai-compat"

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None,
                 api_key: Optional[str] = None, timeout: float = 120.0,
                 temperature: float = 0.0) -> None:
        self.base_url = (base_url or os.environ.get("GRAP_LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.environ.get("GRAP_LLM_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.environ.get("GRAP_LLM_API_KEY")
        self.timeout = timeout
        self.temperature = temperature

    def complete(self, system: str, user: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,   # low for reproducibility
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(
                f"LLM request to {url} failed: {exc}. Is the server (e.g. Ollama) "
                f"running and is model '{self.model}' available?") from exc
        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected LLM response shape: {body!r}") from exc


def list_models(base_url: Optional[str] = None, api_key: Optional[str] = None,
                timeout: float = 5.0) -> List[str]:
    """List model ids from an OpenAI-compatible `/models` endpoint (e.g. Ollama)."""
    base = (base_url or os.environ.get("GRAP_LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
    key = api_key or os.environ.get("GRAP_LLM_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(f"{base}/models", headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return [m["id"] for m in body.get("data", [])
            if isinstance(m, dict) and isinstance(m.get("id"), str)]


def get_provider(model: Optional[str] = None) -> LLMProvider:
    """Return the configured provider.

    `GRAP_LLM_STUB=1` forces the offline stub; otherwise an OpenAI-compatible
    HTTP provider pointed at GRAP_LLM_BASE_URL (default local Ollama). An
    explicit `model` overrides GRAP_LLM_MODEL (lets the UI pick a model).
    """
    if os.environ.get("GRAP_LLM_STUB"):
        return StubProvider()
    return OpenAICompatProvider(model=model or None)
