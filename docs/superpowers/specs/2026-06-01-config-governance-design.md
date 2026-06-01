# Configuration Governance — Design Spec

> Covers **Task 08** (configuration governance) in one delivery / one branch
> (`config-governance`). Implement via `superpowers:writing-plans`. Pure Python —
> no Rust/pygrap rebuild. Phased: A (config module) → B/C/D (MCP tools + activate
> integration) → E (capabilities).

## Architecture Decisions (approved during brainstorming)

1. **Governed config is a Python `<ws>/config.json`** (current weights + retention
   policy + last_changed); changes are audited via the existing `record_event`.
   No rebuild.
2. **`activate` defaults to + surfaces the governed weights.** When no explicit
   weights are passed, it freezes the governed weights and reports them (+ when
   last changed).
3. **Surface covers weights + retention policy.** Both are settable + audited;
   retention is **not enforced** (enforcement out of scope).

## Context (current state)

- Activations freeze `frozen_weights: Map<String,f64>`; the MCP `activate` tool
  accepts `weights: dict = None` and calls `ws.activate(..., weights=weights or {}, ...)`.
- Weights do not drive the reasoner (deterministic over KG+ontology); they are
  frozen for reproducibility/governance.
- `ws.record_event(event_type, subject, details=None, rationale=None)` exists and
  appends to the Temporal Graph / audit log.
- The MCP server opens the workspace at `os.environ.get("GRAP_WORKSPACE", "./grap-workspace")`
  via `_get_workspace()`, but does not currently remember that root path.
- `list_capabilities` domains: knowledge, pg_lifecycle, execution_reasoning,
  experimentation, validation, audit. A drift test asserts taxonomy == registered tools.

---

## Part A — `grap_mcp/config.py` (new)

```python
def default_config() -> dict:
    return {"weights": {},
            "retention": {"production_days": 365, "experiment_days": 30},
            "last_changed": None}

def load_config(ws_root: str) -> dict:
    """Read <ws_root>/config.json, or default_config() if absent/unreadable."""

def save_config(ws_root: str, config: dict) -> None:
    """Write <ws_root>/config.json (pretty JSON)."""
```

- `load_config`: if `<ws_root>/config.json` exists, parse and return it; otherwise
  return `default_config()`. On parse error, return `default_config()` (POC: don't
  crash on a corrupt file).
- Pure filesystem; no pygrap import. The MCP layer supplies `ws_root`.

---

## Part B — Track workspace root (`server.py`)

- Add `"ws_root": None` to `_state`.
- In `_get_workspace()`, set `_state["ws_root"] = root` when opening, so config
  tools know where `config.json` lives.

---

## Part C — Governance MCP tools (`server.py`)

Import: `from . import config as config_store`. Use `import time` for timestamps.

- `get_config()` →
  ```python
  cfg = config_store.load_config(_resolve_ws_root())
  return {"status": "ok", "weights": cfg["weights"], "retention": cfg["retention"],
          "last_changed": cfg["last_changed"]}
  ```
  (`_resolve_ws_root()` returns `_state["ws_root"]`, opening the workspace first if
  needed so the root is set.)

- `set_weight(name: str, value: float, rationale: str)`:
  - If `not rationale`: `{"status": "error", "message": "rationale is required"}`.
  - If `value` is not a number (`isinstance(value, (int, float))` and not bool):
    `{"status": "error", "message": "value must be a number"}`.
  - Load config; `cfg["weights"][name] = float(value)`; `cfg["last_changed"] = int(time.time()*1000)`;
    save.
  - `_get_workspace().record_event("config_changed", f"weight:{name}", details={"value": str(value)}, rationale=rationale)`.
  - Return `{"status": "ok", "weights": cfg["weights"], "last_changed": cfg["last_changed"]}`.

- `set_retention(key: str, days: int, rationale: str)`:
  - Same rationale + numeric guards (`days` is an int).
  - `cfg["retention"][key] = int(days)`; stamp `last_changed`; save.
  - Audit `config_changed`, `subject=f"retention:{key}"`, `details={"days": str(days)}`, rationale.
  - Return `{"status": "ok", "retention": cfg["retention"], "last_changed": cfg["last_changed"]}`.

---

## Part D — Activation integration (`server.py`)

In the existing `activate` tool, before calling `ws.activate(...)`:

```python
cfg = config_store.load_config(_resolve_ws_root())
effective_weights = weights if weights is not None else cfg["weights"]
```

Pass `effective_weights` to `ws.activate(..., weights=effective_weights, ...)`. Extend
the success return with:

```python
"frozen_weights": effective_weights,
"weights_last_changed": cfg["last_changed"],
```

Explicit `weights` still override the governed set.

---

## Part E — Capability surface

- Add a `governance` domain to `list_capabilities`:
  `["get_config", "set_weight", "set_retention"]`.
- Add a "Configuration Governance" section to `mcp-server/CAPABILITIES.md`. The drift
  test keeps the taxonomy in sync.

---

## Testing Strategy

- **Python (pytest)** — `tests/test_config.py`:
  - `load_config` on an empty dir returns `default_config()` shape (weights `{}`,
    retention keys present, `last_changed is None`).
  - `save_config` then `load_config` round-trips a modified config.
  - `load_config` on a corrupt `config.json` returns defaults (no exception).
- **Capabilities drift test** passes with the new `governance` domain + 3 tools.
- **End-to-end MCP:** `set_weight("priority", 0.8, "tuning")` → `get_config` shows
  `weights == {"priority": 0.8}` and non-null `last_changed`; `set_weight("x", 1, "")`
  → error (rationale required); `set_retention("production_days", 180, "policy")`
  audited; load+publish+promote a valid PG, then `activate` **without** weights →
  response shows `frozen_weights == {"priority": 0.8}` and `weights_last_changed`
  set; `get_audit_log` contains `config_changed` events.

## Out of Scope (explicit)

Enforcing retention (cleanup/deletion); per-key change history beyond the audit log;
role-based authorization (deferred with Task 02); wiring weights into the reasoner
(it stays deterministic; weights are frozen for governance/reproducibility).

## Acceptance Criteria

- A small set of weights (and a retention policy) can be read and updated via MCP
  with **mandatory rationale**; empty rationale is refused.
- Every config change emits an auditable `config_changed` Temporal-Graph event with
  the rationale.
- `activate` freezes the governed weights when none are passed and surfaces the
  frozen weights + when they were last changed.
- Retention policy is configurable (settable + audited), even though enforcement is
  out of scope.
