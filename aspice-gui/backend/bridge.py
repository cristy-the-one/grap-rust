"""Thin bridge to grap_mcp.application — same logic as the MCP tools, no stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MCP_ROOT = _REPO_ROOT / "mcp-server"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from grap_mcp.application import GrapApplication  # noqa: E402

application = GrapApplication()


def repo_root() -> Path:
    return _REPO_ROOT


def default_examples() -> dict[str, str]:
    root = _REPO_ROOT
    return {
        "ontology": str(root / "docs/examples/minimal-aspice-ontology.yaml"),
        "procedure_graph": str(root / "docs/examples/simple-aspice-gap-check.pg.yaml"),
        "sample_knowledge": str(root / "aspice-gui" / "sample-knowledge"),
    }


def ensure_workspace(path: str | None = None) -> str:
    # Prefer an explicit path, then an already-configured GRAP_WORKSPACE
    # (so callers/tests that set it stay isolated), then the GUI default.
    ws = path or os.environ.get("GRAP_WORKSPACE") or str(
        _REPO_ROOT / "aspice-gui" / "grap-workspace")
    global application
    application = GrapApplication(ws)
    Path(ws).mkdir(parents=True, exist_ok=True)
    return ws


def call(name: str, **kwargs: Any) -> dict:
    fn = getattr(application, name, None) if name in application.operation_names() else None
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