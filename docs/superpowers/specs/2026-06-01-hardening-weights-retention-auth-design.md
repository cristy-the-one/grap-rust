# Hardening: Reasoner Weights + Retention Enforcement + Role-Based Auth — Design Spec

> Three hardening items in one delivery / one branch
> (`hardening-weights-retention-auth`). Implement via `superpowers:writing-plans`.
> Phased: A (reasoner weights, Python) → B (Rust deletion + enforce_retention) →
> C (pygrap bindings, rebuild) → D (MCP wiring + auth).

## Architecture Decisions (approved during brainstorming)

1. **Reasoner weights**: the reasoner reads `text_weight`, `graph_weight`
   (search fusion) and `lexical_threshold` (satisfaction cutoff) from the run's
   frozen activation weights → governed config → defaults; gaps are ordered by
   confidence.
2. **Retention**: hard-delete via an admin `enforce_retention()` tool backed by a
   Rust `Workspace.enforce_retention` that purges expired runs + their orphaned
   explanation records.
3. **Auth**: three roles (`viewer` < `operator` < `admin`); current role in
   `_state` from env `GRAP_ROLE` (default `operator`); a `set_role` tool;
   mutating tools gated inline. `set_role` is an unauthenticated POC simulation.

## Context (current state)

- `reasoner.py`: `detect_gaps(kg, ontology, pg=None)`; `_lexical_signals` hardcodes
  `text_weight=0.7, graph_weight=0.3` and uses module constant
  `LEXICAL_THRESHOLD = 0.2`; `detect_gaps` returns gaps in outcome/characteristic
  order (not confidence-sorted).
- `server.py`: `_run_gap_detection(scope, kind, extra_summary)` resolves the current
  production activation and calls `reasoner.detect_gaps(kg, ontology, pg)`; config
  store (`grap_mcp/config.py`) holds `weights` + `retention` (`production_days`,
  `experiment_days`); `activate` already freezes weights.
- Rust `Workspace`: `RunStore` (records have `started_at`, `explanation_hashes`),
  `VersionStore` (PG/Ontology/Explanation/Validation), `AuditLog`, run/temporal/
  reconstruct methods. **No delete methods anywhere.** Run `started_at` is NOT
  exposed on the pygrap `RunRecord` wrapper; the pygrap `Activation` wrapper does
  NOT expose `frozen_weights`.
- No auth/role concept exists. `list_capabilities` domains: knowledge, pg_lifecycle,
  execution_reasoning, experimentation, governance, validation, audit. A drift test
  asserts taxonomy == registered tools.

---

## Part A — Reasoner weights (`grap_mcp/reasoner.py`)

- Add `DEFAULT_WEIGHTS = {"text_weight": 0.7, "graph_weight": 0.3, "lexical_threshold": 0.2}`
  (keep `LEXICAL_THRESHOLD = 0.2` for back-compat references, or reference
  `DEFAULT_WEIGHTS["lexical_threshold"]`).
- `detect_gaps(kg, ontology, pg=None, weights=None)`: `w = {**DEFAULT_WEIGHTS, **(weights or {})}`.
  Thread `w` into `_lexical_signals` (use `w["text_weight"]`, `w["graph_weight"]` in
  `search_and_traverse`) and into the satisfaction check
  (`satisfied = bool(structural) or satisfaction >= w["lexical_threshold"]`).
- Sort the returned gaps by `confidence` **descending** (stable).
- A call with `weights=None` reproduces today's behavior exactly (defaults), so
  existing reasoner tests pass unchanged.

---

## Part B — Rust deletion + enforce_retention (`src/lifecycle/`)

- `run.rs`: `RunStore::delete(&self, run_id: &str) -> Result<bool>` (remove the JSON
  file; `false` if absent).
- `version.rs`: `VersionStore::delete(&self, hash: &ContentHash) -> Result<bool>`
  (remove object blob + meta; `false` if absent).
- `mod.rs`: new `RetentionReport { runs_deleted: usize, explanations_deleted: usize, run_ids: Vec<String> }`
  (`Debug, Clone, PartialEq, Eq`); re-export.
- `workspace.rs`:
  ```rust
  pub fn enforce_retention(&self, production_cutoff_ms: u64, experiment_cutoff_ms: u64)
      -> Result<RetentionReport, LifecycleError>
  ```
  - List all runs. A run is expired when
    `started_at < (production_cutoff_ms if kind==Production else experiment_cutoff_ms)`.
  - Compute the set of explanation hashes referenced by **surviving** (non-expired)
    runs.
  - For each expired run: `runs.delete(run_id)`; for each of its
    `explanation_hashes` not in the surviving set, `versions.delete(hash)`
    (count once per distinct hash).
  - Append a `retention_enforced` `AuditEvent` (details: counts).
  - Return the `RetentionReport`.
- `lib.rs`: re-export `RetentionReport`.
- Tests (construct `RunRecord`s with explicit `started_at` via `RunStore::put`):
  - expired production run is deleted; its sole-referenced explanation is deleted;
  - a recent run is kept; an explanation referenced by a surviving run is **not**
    deleted even if also referenced by an expired run;
  - report counts match; `delete` returns `false` for unknown ids/hashes.

---

## Part C — pygrap bindings (rebuild)

**File:** `crates/pygrap/src/lifecycle.rs`

- Add `frozen_weights: std::collections::BTreeMap<String, f64>` to the `Activation`
  pyclass wrapper (`get_all`), populated from `lc::Activation.frozen_weights`
  (PyO3 maps `BTreeMap<String,f64>` → dict).
- New `RetentionReport` pyclass (`get_all`: `runs_deleted`, `explanations_deleted`,
  `run_ids`) + `From<lc::RetentionReport>`.
- `Workspace.enforce_retention(production_cutoff_ms, experiment_cutoff_ms) -> RetentionReport`.
- Register `RetentionReport` in `crates/pygrap/src/lib.rs`.
- Rebuild + reinstall; smoke: `enforce_retention` on an empty workspace returns
  zeros; an `Activation` exposes `frozen_weights`.

---

## Part D — MCP wiring + auth (`grap_mcp/server.py`)

### D1. Weights into runs
In `_run_gap_detection`: resolve
```python
if activation is not None:
    weights = dict(activation.frozen_weights)
else:
    weights = config_store.load_config(_resolve_ws_root())["weights"]
```
Pass `weights` to `reasoner.detect_gaps(kg, ontology, pg, weights=weights)`; add
`summary["weights"] = json.dumps(weights)` for reproducibility.

### D2. enforce_retention tool (admin)
```python
@mcp.tool()
def enforce_retention() -> dict:
    # admin-gated; cutoffs from config retention days
```
`now_ms = int(time.time()*1000)`;
`prod_cutoff = now_ms - retention["production_days"]*86_400_000`;
`exp_cutoff = now_ms - retention["experiment_days"]*86_400_000`;
`rep = ws.enforce_retention(prod_cutoff, exp_cutoff)`; return
`{status, runs_deleted, explanations_deleted, run_ids}`.

### D3. Auth
- New module `grap_mcp/auth.py` (testable, no MCP dependency):
  ```python
  ROLES = {"viewer": 0, "operator": 1, "admin": 2}
  def role_allows(current: str, required: str) -> bool:
      return ROLES.get(current, -1) >= ROLES[required]
  ```
- `server.py`: add `"role"` to `_state`, initialised from
  `os.environ.get("GRAP_ROLE", "operator")`. Helper:
  ```python
  def _require(required: str):
      current = _state.get("role", "operator")
      if not auth.role_allows(current, required):
          return {"status": "error", "message": f"requires '{required}' role (current: '{current}')"}
      return None
  ```
- Gate the top of each mutating tool: `denied = _require("operator"); \n if denied: return denied`
  for `load_knowledge_graph`, `load_ontology`, `load_procedure_graph`, `publish_pg`,
  `promote_pg`, `activate`, `set_production`, `run_procedure`, `detect_gaps`,
  `run_experiment`, `adopt_experiment`. Use `_require("admin")` for `set_weight`,
  `set_retention`, `enforce_retention`.
- New tools `set_role(role)` (validates role in `ROLES`; sets `_state["role"]`;
  unrestricted — POC simulation) and `get_role()` (returns current role + the role
  list). Reads (`get_*`, `list_*`, `query_*`, `validate_procedure_graph`,
  `diff_procedure_graphs`, `get_explanation`, `get_quality_metrics`,
  `get_validation`, `get_run`) are not gated (viewer-accessible).

### D4. Capabilities + docs
- Add `auth` domain: `["set_role", "get_role"]`; add `enforce_retention` to
  `governance`. Update `list_capabilities` + `CAPABILITIES.md`. Drift test follows.

---

## Testing Strategy

- **Python (pytest):**
  - `reasoner`: with `weights={"lexical_threshold": 0.99}` a previously-satisfied
    characteristic becomes a gap (threshold raised); `weights=None` matches current
    behavior; gaps are sorted by `confidence` descending.
  - `auth`: `role_allows` truth table (viewer/operator/admin × required); a
    `set_role`/`_require`-style check (via the `auth` module) allows/denies.
  - Existing reasoner/ontology/config/experiments/capabilities tests stay green
    (drift test updated for the new domain + tools).
- **Rust:** `enforce_retention` deletion + orphan-safety + report counts;
  `RunStore::delete` / `VersionStore::delete` round-trips.
- **pygrap:** rebuild; smoke `enforce_retention` (zeros on empty) + `Activation.frozen_weights`.
- **End-to-end MCP:** as admin `set_weight("lexical_threshold", 0.99, "tune")`,
  publish→promote→`activate` (freezes weights), `detect_gaps` → more gaps than with
  defaults (threshold effect) and run summary records the weights; `set_role("viewer")`
  then `publish_pg` → denied; `set_role("operator")` → `detect_gaps` allowed but
  `set_weight` denied; `set_role("admin")` → `enforce_retention()` runs and audits.

## Build Notes

Parts B–C require a pygrap rebuild (`maturin build --release` + force-reinstall into
user site-packages). Parts A and D are pure Python.

## Out of Scope (explicit)

Real authentication/identity (roles are a local simulation via `set_role`);
scheduled/automatic retention (manual `enforce_retention` only); per-request auth
tokens; changing gap detection beyond the three weights.

## Acceptance Criteria

- Frozen/governed weights measurably change gap-detection results (threshold +
  fusion), and gaps are returned ordered by confidence; runs record the weights used.
- `enforce_retention` hard-deletes runs older than their kind's window and their
  orphaned explanation records, never deleting explanations still referenced by
  surviving runs, and audits the action.
- Mutating capabilities are role-gated (operator for lifecycle/execution, admin for
  governance + retention); reads are open; `set_role`/`get_role` manage the current
  role; the default role comes from `GRAP_ROLE` (`operator`).
