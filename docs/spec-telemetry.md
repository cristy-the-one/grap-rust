# Concrete Telemetry: `CountingTelemetry` + MCP stats ledger (P2-G) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn grap's no-op `Telemetry` trait into something usable — a concrete atomic-counter impl in the core (plugging into the engine's existing sharded hooks) — and add an opt-in, local-first stats/savings ledger to the MCP that surfaces the token savings the wire-encoding work (A) already computes.

**Architecture:** Two small, additive, opt-in pieces:
- **Core `CountingTelemetry`** — a `Clone` struct of `Arc`-shared atomics implementing `Telemetry`; the caller keeps a clone to read `snapshot()` after handing one to `ShardedGraph::set_telemetry`. **No hot-path changes** — the engine already calls the `record_*` hooks on the sharded path; we only provide a concrete recorder. The non-sharded hot path is deliberately left untouched (zero-cost-when-unused stays true).
- **MCP stats ledger** — an env-gated (`GRAP_TELEMETRY=1`) in-process accumulator + optional atomic JSON pulse, fed by `encode_response`'s `tokens_saved`, surfaced via a `get_telemetry` tool. Pure Python skin; nothing phones home.

**Tech Stack:** Rust (`std::sync::atomic`, no new deps), Python (`grap_mcp`), `cargo test` + `pytest`.

**Scope discipline:** Do NOT wire telemetry into the non-sharded `IndexedKnowledgeGraph` traversal (would add per-query atomic dispatch to the hot path — against the "zero-cost when unused" design). Do NOT add a metrics-crate dependency (the trait exists precisely so the library stays dependency-free). The MCP ledger tracks what is actually instrumented (encoded-call savings) — it does not claim to count every one of the ~40 tools (FastMCP has no central dispatch to hook).

---

## File Structure
- **Modify** `src/lib.rs` (or a new `src/telemetry.rs` if the trait moves there) — add `CountingTelemetry` + `TelemetrySnapshot` next to the `Telemetry` trait, under the same feature gate the trait uses (if any).
- **Create** `mcp-server/grap_mcp/telemetry.py` — env-gated accumulator + pulse.
- **Modify** `mcp-server/grap_mcp/encoding/__init__.py` — feed `tokens_saved` into the accumulator.
- **Modify** `mcp-server/grap_mcp/server.py` — `get_telemetry` `@mcp.tool()` + capabilities registration.
- **Tests** unit test in core; `mcp-server/tests/test_telemetry.py`.

---

## Task 1: Core `CountingTelemetry`

**Files:** Modify `src/lib.rs` (place near the `Telemetry` trait + `NoopTelemetry`, ~line 190-212). Test: unit test in `src/lib.rs` (or wherever the trait's tests live).

### Step 0 — determine the feature gate
The `Telemetry` trait and the sharded telemetry wiring may be behind `#[cfg(feature = "telemetry")]` (Cargo.toml has `telemetry = []`). **Check** whether `pub trait Telemetry` and `src/shard.rs`'s `telemetry` field are `#[cfg(feature = "telemetry")]`-gated. If they are, gate `CountingTelemetry`/`TelemetrySnapshot` the same way and run the test with `--features telemetry`. If they are NOT gated (always compiled), don't gate the new types either. Report what you found.

- [ ] **Step 1: Failing test** — add to the test module (gate with `#[cfg(feature="telemetry")]` if applicable). It exercises the impl directly (no need for a full sharded query — the trait methods are the contract):
```rust
#[test]
fn counting_telemetry_accumulates_and_snapshots() {
    let t = CountingTelemetry::new();
    let handle = t.clone(); // clone shares the same atomics
    t.record_traversal_nodes_visited(3);
    t.record_traversal_nodes_visited(2);
    t.record_mask_prune(4);
    t.record_remote_fetch(true);
    t.record_remote_fetch(false);
    t.record_rebalance(7);
    let s = handle.snapshot(); // read through the clone — shared state
    assert_eq!(s.traversal_nodes_visited, 5);
    assert_eq!(s.mask_prunes, 4);
    assert_eq!(s.remote_fetch_hits, 1);
    assert_eq!(s.remote_fetch_misses, 1);
    assert_eq!(s.rebalance_nodes_moved, 7);
}
```
(`record_*` are `Telemetry` trait methods; ensure `Telemetry` is in scope in the test.)

- [ ] **Step 2: Run, verify FAIL** — `cargo test -p grap --lib counting_telemetry_accumulates_and_snapshots` (add `--features telemetry` if gated) → undefined.

- [ ] **Step 3: Implement** (near the `Telemetry` trait in `src/lib.rs`):
```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

#[derive(Debug, Default)]
struct TelemetryCounters {
    traversal_nodes_visited: AtomicU64,
    mask_prunes: AtomicU64,
    remote_fetch_hits: AtomicU64,
    remote_fetch_misses: AtomicU64,
    rebalance_nodes_moved: AtomicU64,
}

/// A concrete [`Telemetry`] that accumulates counters in shared atomics.
/// Cheap and thread-safe. Clone it: all clones share the same counters, so a
/// caller can hand one clone to `ShardedGraph::set_telemetry` and keep another
/// to read [`CountingTelemetry::snapshot`].
#[derive(Debug, Clone, Default)]
pub struct CountingTelemetry {
    inner: Arc<TelemetryCounters>,
}

/// A point-in-time copy of [`CountingTelemetry`]'s counters.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TelemetrySnapshot {
    /// Nodes visited during `search_and_traverse` calls.
    pub traversal_nodes_visited: u64,
    /// Edges rejected early by the 64-bit property mask.
    pub mask_prunes: u64,
    /// Remote shard fetches that hit.
    pub remote_fetch_hits: u64,
    /// Remote shard fetches that missed.
    pub remote_fetch_misses: u64,
    /// Nodes moved by rebalancing.
    pub rebalance_nodes_moved: u64,
}

impl CountingTelemetry {
    /// Create a fresh recorder with zeroed counters.
    pub fn new() -> Self {
        Self::default()
    }
    /// Read the current counter values.
    pub fn snapshot(&self) -> TelemetrySnapshot {
        TelemetrySnapshot {
            traversal_nodes_visited: self.inner.traversal_nodes_visited.load(Ordering::Relaxed),
            mask_prunes: self.inner.mask_prunes.load(Ordering::Relaxed),
            remote_fetch_hits: self.inner.remote_fetch_hits.load(Ordering::Relaxed),
            remote_fetch_misses: self.inner.remote_fetch_misses.load(Ordering::Relaxed),
            rebalance_nodes_moved: self.inner.rebalance_nodes_moved.load(Ordering::Relaxed),
        }
    }
}

impl Telemetry for CountingTelemetry {
    fn record_traversal_nodes_visited(&self, count: u64) {
        self.inner.traversal_nodes_visited.fetch_add(count, Ordering::Relaxed);
    }
    fn record_mask_prune(&self, pruned: u64) {
        self.inner.mask_prunes.fetch_add(pruned, Ordering::Relaxed);
    }
    fn record_remote_fetch(&self, hit: bool) {
        if hit {
            self.inner.remote_fetch_hits.fetch_add(1, Ordering::Relaxed);
        } else {
            self.inner.remote_fetch_misses.fetch_add(1, Ordering::Relaxed);
        }
    }
    fn record_rebalance(&self, nodes_moved: u64) {
        self.inner.rebalance_nodes_moved.fetch_add(nodes_moved, Ordering::Relaxed);
    }
}
```
   Re-export `CountingTelemetry`/`TelemetrySnapshot` wherever `Telemetry`/`NoopTelemetry` are re-exported (they're in `lib.rs` root, so likely already public). Match the exact `Telemetry` method signatures in this file (the failing test will catch a mismatch).

- [ ] **Step 4: Verify** — the unit test passes; `cargo test -p grap` (full; add `--features telemetry` if gated — also run WITHOUT the feature to confirm the crate still builds when it's off); `cargo build -p grap` warning-clean. Optionally add a second test that plugs `CountingTelemetry` into a small `ShardedGraph` via `set_telemetry(t.clone())`, runs a `search_and_traverse`, and asserts `t.snapshot().traversal_nodes_visited > 0` (verify the `ShardedGraph` constructor/API first; if the sharded test is fiddly, the direct-trait test above is sufficient).

- [ ] **Step 5: Commit**
```
git add src/lib.rs
git commit -m "feat(core): CountingTelemetry — concrete atomic-counter Telemetry impl"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: MCP stats accumulator + feed from `encode_response`

**Files:** Create `mcp-server/grap_mcp/telemetry.py`; modify `mcp-server/grap_mcp/encoding/__init__.py`. Test: `mcp-server/tests/test_telemetry.py`.

- [ ] **Step 1: Failing test** — `mcp-server/tests/test_telemetry.py`:
```python
import grap_mcp.telemetry as tel

def test_records_and_aggregates(monkeypatch):
    monkeypatch.setenv("GRAP_TELEMETRY", "1")
    tel.reset()  # test helper: clear in-process state
    tel.record("graph_neighbors", tokens_saved=120)
    tel.record("graph_neighbors", tokens_saved=80)
    tel.record("query_events", tokens_saved=0)
    snap = tel.snapshot()
    assert snap["total_tokens_saved"] == 200
    assert snap["total_calls"] == 3
    assert snap["by_tool"]["graph_neighbors"]["calls"] == 2
    assert snap["by_tool"]["graph_neighbors"]["tokens_saved"] == 200

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GRAP_TELEMETRY", raising=False)
    tel.reset()
    tel.record("graph_neighbors", tokens_saved=999)
    snap = tel.snapshot()
    assert snap["enabled"] is False
    assert snap["total_tokens_saved"] == 0  # no-op when disabled
```

- [ ] **Step 2: Run, verify FAIL** — `cd mcp-server && python -m pytest tests/test_telemetry.py -q` → module missing.

- [ ] **Step 3: Implement** `mcp-server/grap_mcp/telemetry.py`:
```python
"""Opt-in, local-first telemetry for the grap MCP.

Disabled unless GRAP_TELEMETRY=1. Tracks per-tool call counts and the
tokens saved by compact wire encoding. In-process; an optional atomic JSON
pulse is written to {GRAP_TELEMETRY_DIR or cwd}/_grap_pulse.json when enabled.
Nothing leaves the machine.
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
    """Clear in-process counters (used by tests and on demand)."""
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
    _write_pulse(tool, tokens_saved)


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


def _write_pulse(tool: str, tokens_saved: int) -> None:
    """Atomic JSON pulse for external dashboards. Best-effort; never raises."""
    if not _enabled():
        return
    try:
        root = os.environ.get("GRAP_TELEMETRY_DIR") or os.getcwd()
        path = os.path.join(root, "_grap_pulse.json")
        data = {
            "last_tool": tool,
            "last_tokens_saved": int(tokens_saved),
            "total_calls": _total_calls,
            "total_tokens_saved": _total_tokens_saved,
            "ts": time.time(),
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        pass  # telemetry must never break a tool call
```
   Then feed it from `encode_response` in `mcp-server/grap_mcp/encoding/__init__.py`: at the end of the COMPACT branch (after computing `tokens_saved`), call the recorder. Import lazily to avoid a hard dependency cycle:
```python
        try:
            from grap_mcp import telemetry as _tel
            _tel.record(tool_name, tokens_saved)
        except Exception:
            pass
        return payload, {"format": "compact", "tokens_saved": tokens_saved}
```
(Only the compact path records a meaningful `tokens_saved`; the JSON path may optionally `record(tool_name, 0)` if you want call counts there too — keep it to the compact path for now to stay honest about what's instrumented.)

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_telemetry.py tests/test_encoding.py -q` (encoding tests must still pass; the lazy import + try/except means encode_response is unaffected when telemetry is off).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/telemetry.py mcp-server/grap_mcp/encoding/__init__.py mcp-server/tests/test_telemetry.py
git commit -m "feat(mcp): opt-in telemetry accumulator fed by wire-encoding token savings"
```

---

## Task 3: `get_telemetry` MCP tool

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `mcp-server/tests/test_telemetry.py`.

- [ ] **Step 1: Failing test** — add to `test_telemetry.py` (mirror the `import grap_mcp.server as s` style of `test_analytics.py`):
```python
import grap_mcp.server as s
import grap_mcp.telemetry as tel

def test_get_telemetry_tool(monkeypatch):
    monkeypatch.setenv("GRAP_TELEMETRY", "1")
    tel.reset()
    tel.record("graph_neighbors", tokens_saved=50)
    out = s.get_telemetry()
    assert out["status"] == "ok"
    assert out["total_tokens_saved"] == 50
    assert out["total_calls"] == 1
```
(Check `get_telemetry`'s read-role expectation matches the default test role, mirroring how `test_analytics.py`/`graph_neighbors` handle `_require`.)

- [ ] **Step 2: Run, verify FAIL** — tool undefined.

- [ ] **Step 3: Implement** — add to `server.py` (mirror the read-tool pattern; this one does NOT need a KG, so skip the KG guard):
```python
@mcp.tool()
def get_telemetry() -> dict:
    """Opt-in local telemetry: per-tool call counts and tokens saved by
    compact wire encoding (enabled via GRAP_TELEMETRY=1)."""
    denied = _require("operator")  # match the read tier used by other read tools
    if denied:
        return denied
    try:
        from grap_mcp import telemetry as _tel
        snap = _tel.snapshot()
        snap["status"] = "ok"
        return snap
    except Exception as e:
        return {"status": "error", "message": str(e)}
```
   Register `get_telemetry` in `list_capabilities` (the introspection/audit group, alongside `get_status`) so `test_capabilities_cover_all_registered_tools` passes. Confirm it does not duplicate an existing `get_status`/stats tool — if `get_status` already exists, `get_telemetry` is complementary (savings/calls vs role/KG state); keep them distinct.

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_telemetry.py -q`; then `python -m pytest -q` (full suite incl. capabilities coverage).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_telemetry.py
git commit -m "feat(mcp): get_telemetry tool surfaces the savings/call ledger"
```

---

## Self-Review
- **Coverage:** core concrete impl (T1), MCP accumulator + feed (T2), MCP surface (T3). Matches the P2-G architecture (concrete Telemetry + savings ledger).
- **Type consistency:** `CountingTelemetry::{new,snapshot}`, `TelemetrySnapshot` fields, and the `Telemetry` method signatures consistent T1; `telemetry.record(tool, tokens_saved)`/`snapshot()` consistent T2–T3.
- **Opt-in / local-first:** core impl is zero-cost unless plugged in; MCP ledger is no-op unless `GRAP_TELEMETRY=1`; pulse stays on local disk; no new deps; non-sharded hot path untouched.
- **Honesty:** the MCP ledger instruments what's actually measured (compact-encoding savings + those calls), not a fictional all-tool counter.
