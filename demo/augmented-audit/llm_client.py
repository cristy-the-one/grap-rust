"""Thin OpenAI-compatible chat client for the augmented-audit demo.

Speaks the /chat/completions protocol with stdlib urllib only (no SDK), the
same wire format Ollama, llama.cpp's server and vLLM all expose. Backend is
chosen entirely by environment, matching the pilot driver:

    GRAP_LLM_BASE_URL   default http://127.0.0.1:8080/v1  (Bonsai llama-server)
    GRAP_LLM_MODEL      default ternary-bonsai-27b
    GRAP_LLM_EXTRA_BODY optional JSON merged into every request body

Supports tool-calling (`tools=`) so the same client backs both the PG
authoring tool (single-turn) and the agentic demo (tool round-trips). The
Bonsai server must be launched with --jinja for native tool_calls.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Optional

BASE_URL = os.environ.get("GRAP_LLM_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
MODEL = os.environ.get("GRAP_LLM_MODEL", "ternary-bonsai-27b")

# Bonsai is a reasoning model: with thinking on it emits everything into
# `reasoning_content` and leaves `content` empty. Disable it by default (the
# pilot driver does the same); an explicit GRAP_LLM_EXTRA_BODY overrides.
_DEFAULT_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}
_EXTRA = json.loads(os.environ.get("GRAP_LLM_EXTRA_BODY", "") or "null")
if _EXTRA is None:
    _EXTRA = _DEFAULT_EXTRA


def chat(messages: List[Dict[str, Any]], tools: Optional[List[dict]] = None,
         temperature: float = 0.2, max_tokens: int = 1200,
         timeout: int = 300) -> Dict[str, Any]:
    """One /chat/completions turn. Returns the assistant `message` dict
    (which may carry `tool_calls`). Raises on transport/HTTP error."""
    body: Dict[str, Any] = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_EXTRA,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]


def complete(system: str, user: str, **kw) -> str:
    """Single-turn text completion convenience wrapper. Falls back to
    reasoning_content if a thinking model left content empty."""
    msg = chat([{"role": "system", "content": system},
                {"role": "user", "content": user}], **kw)
    return ((msg.get("content") or msg.get("reasoning_content") or "")).strip()
