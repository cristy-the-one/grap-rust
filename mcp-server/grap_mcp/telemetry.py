"""Opt-in, local-first telemetry for the grap MCP.

Disabled unless GRAP_TELEMETRY=1. Tracks per-tool call counts and tokens
saved by compact wire encoding. In-process, with an optional atomic JSON
pulse at {GRAP_TELEMETRY_DIR or cwd}/_grap_pulse.json. Nothing leaves the box.
"""
import json
import os
import threading
import time

_lock = threading.Lock()
_start = time.time()
_total_calls = 0
_total_tokens_saved = 0
_by_tool: dict[str, dict[str, int]] = {}


def _enabled() -> bool:
    return os.environ.get("GRAP_TELEMETRY") == "1"


def reset() -> None:
    """Clear in-process counters (tests / on demand)."""
    global _total_calls, _total_tokens_saved, _by_tool, _start
    with _lock:
        _total_calls = 0
        _total_tokens_saved = 0
        _by_tool = {}
        _start = time.time()


def record(tool: str, tokens_saved: int = 0) -> None:
    """Record one instrumented tool call. No-op unless GRAP_TELEMETRY=1."""
    if not _enabled():
        return
    with _lock:
        global _total_calls, _total_tokens_saved
        _total_calls += 1
        _total_tokens_saved += max(0, int(tokens_saved))
        t = _by_tool.setdefault(tool, {"calls": 0, "tokens_saved": 0})
        t["calls"] += 1
        t["tokens_saved"] += max(0, int(tokens_saved))
        calls, total = _total_calls, _total_tokens_saved
    _write_pulse(tool, tokens_saved, calls, total)


def snapshot() -> dict:
    """Aggregate snapshot of telemetry counters."""
    with _lock:
        return {
            "enabled": _enabled(),
            "uptime_s": round(time.time() - _start, 2),
            "total_calls": _total_calls,
            "total_tokens_saved": _total_tokens_saved,
            "by_tool": {k: dict(v) for k, v in _by_tool.items()},
        }


def _write_pulse(tool: str, tokens_saved: int, calls: int, total: int) -> None:
    """Atomic JSON pulse for dashboards. Best-effort; never raises."""
    if not _enabled():
        return
    try:
        root = os.environ.get("GRAP_TELEMETRY_DIR") or os.getcwd()
        path = os.path.join(root, "_grap_pulse.json")
        data = {"last_tool": tool, "last_tokens_saved": int(tokens_saved),
                "total_calls": calls, "total_tokens_saved": total, "ts": time.time()}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        pass
