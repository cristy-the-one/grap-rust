"""Governed configuration store: prioritization weights + retention policy.

Persisted as <ws_root>/config.json. Mutations are driven by MCP tools that require
a rationale and audit each change; this module only handles load/save + defaults.
"""

import json
from pathlib import Path
from typing import Any, Dict


def default_config() -> Dict[str, Any]:
    """The starting governed config."""
    return {
        "weights": {},
        "retention": {"production_days": 365, "experiment_days": 30},
        "role": "operator",
        # Dark-factory adoption policy (see grap_mcp/governance.py). Disabled
        # until an admin enables it via set_policy — that flip is the designed
        # rare human act. Older configs lack this key; readers must merge via
        # governance.merged_policy().
        "adoption_policy": {
            "enabled": False,
            "max_outcomes_added": 5,
            "max_outcomes_removed": 0,
            "max_characteristics_removed": 0,
            "min_source_chunks": 1,
        },
        "last_changed": None,
    }


def _path(ws_root: str) -> Path:
    return Path(ws_root) / "config.json"


def load_config(ws_root: str) -> Dict[str, Any]:
    """Read <ws_root>/config.json, or default_config() if absent/corrupt."""
    path = _path(ws_root)
    if not path.exists():
        return default_config()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default_config()


def save_config(ws_root: str, config: Dict[str, Any]) -> None:
    """Write <ws_root>/config.json (pretty JSON)."""
    path = _path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
