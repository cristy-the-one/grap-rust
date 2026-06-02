"""Thin bridge to grap_mcp.server — same logic as the MCP tools, no stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MCP_ROOT = _REPO_ROOT / "mcp-server"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

import grap_mcp.server as srv  # noqa: E402


def repo_root() -> Path:
    return _REPO_ROOT


def default_examples() -> dict[str, str]:
    root = _REPO_ROOT
    return {
        "ontology": str(root / "docs/examples/minimal-aspice-ontology.yaml"),
        "procedure_graph": str(root / "docs/examples/simple-aspice-gap-check.pg.yaml"),
    }


def ensure_workspace(path: str | None = None) -> str:
    ws = path or str(_REPO_ROOT / "aspice-gui" / "grap-workspace")
    os.environ["GRAP_WORKSPACE"] = ws
    Path(ws).mkdir(parents=True, exist_ok=True)
    srv._state["ws"] = None
    srv._state["ws_root"] = None
    return ws


def call(name: str, **kwargs: Any) -> dict:
    fn = getattr(srv, name, None)
    if fn is None or not callable(fn):
        return {"status": "error", "message": f"unknown capability: {name}"}
    try:
        result = fn(**kwargs)
        if isinstance(result, dict):
            return result
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def status() -> dict:
    return call("get_status")


def capabilities() -> dict:
    return call("list_capabilities")