# Experimentation & Quarantine — Design Spec

> Covers **Task 07** (experimentation & quarantine) in one delivery / one branch
> (`experimentation-quarantine`). Implement via `superpowers:writing-plans`.
> Pure Python — no Rust/pygrap rebuild. Phased: A (experiments module) →
> B (declaration + shared run helper) → C (quarantine view + transition) →
> D (capabilities).

## Architecture Decisions (approved during brainstorming)

1. **Dedicated `run_experiment` capability** declares an experiment explicitly and
   records a hypothesis/label in the run `summary`.
2. **Production-only `get_quality_metrics`** is the quarantine boundary: it
   aggregates only production runs and reports experiments as excluded-but-counted.
3. **Pure Python.** Reuse the existing run `kind` + `summary` + `list_runs`
   filtering already in the Rust Workspace; no new Rust schema, no rebuild.

## Context (current state)

- Run records (Rust `RunRecord`, pygrap wrapper) expose: `run_id`, `kind`
  (`"production"|"experiment"`), `status`, `gaps_found`, `pg_version`,
  `kg_version`, `explanation_hashes`. The pygrap wrapper does **not** expose
  timestamps, so chronology is derived from the Temporal Graph event log.
- `Workspace` (via pygrap): `start_run(run_id, kind, activation_id, pg_version,
  kg_version, scope)`, `record_explanation(content, metadata)`,
  `complete_run(run_id, status, gaps_found, explanation_hashes, summary)`,
  `get_run(run_id)`, `list_runs(kind=None)`, `query_events(event_type, ...)`,
  `events_for_run`, `events_linked_to`, `reconstruct`.
- `server.py` `detect_gaps(scope, kind="production")` already wraps a run:
  `start_run` → `reasoner.detect_gaps` → persist explanations via
  `record_explanation` → `complete_run`. `run_procedure` also wraps a run.
- Lifecycle tools: `publish_pg`, `promote_pg` (validation-gated), `activate`
  (requires Promoted, supports `set_as_production`).
- `list_capabilities` domains: knowledge, pg_lifecycle, execution_reasoning,
  validation, audit. A capabilities drift test asserts taxonomy == registered tools.

---

## Part A — `grap_mcp/experiments.py` (new)

```python
def experiment_summary(scope: str, hypothesis: str | None) -> dict:
    """Run-summary map for an experiment run."""
    s = {"scope": scope, "experiment": "true"}
    if hypothesis:
        s["hypothesis"] = hypothesis
    return s


def quality_metrics(ws) -> dict:
    """Production-only quality view (the quarantine boundary).

    Experiments never enter the production aggregate; they are reported only as a
    count. The latest *completed* production run is found via the chronological
    `run_completed` event stream (run records expose no timestamp).
    """
```

`quality_metrics(ws)` logic:
- `prod = {r.run_id: r for r in ws.list_runs("production")}`
- `experiments_excluded = len(ws.list_runs("experiment"))`
- `completed = [e for e in ws.query_events("run_completed") if e.subject in prod]`
  (chronological log order).
- `last = completed[-1].subject if completed else None`
- `latest_gaps_found = prod[last].gaps_found if last else None`
- return:
  ```python
  {
    "production_runs": len(prod),
    "production_completed": len(completed),
    "last_production_run": last,
    "latest_gaps_found": latest_gaps_found,
    "experiments_excluded": experiments_excluded,
  }
  ```

---

## Part B — Shared run helper + `run_experiment` (`server.py`)

- Refactor `detect_gaps` to delegate to an internal helper:
  ```python
  def _run_gap_detection(scope: str, kind: str, extra_summary: dict | None = None) -> dict:
      """Run gap detection as a tracked run. Returns {status, run_id, kind, gaps, gaps_found} or an error dict."""
  ```
  The helper: requires KG + ontology; resolves the current production activation
  for `activation_id`/frozen versions (falling back to `ontology_version`);
  `start_run(run_id, kind=kind, ...)`; runs `reasoner.detect_gaps`; persists each
  explanation via `record_explanation` (so **experiments have the same explanation
  records as production**); `complete_run(run_id, "completed", gaps_found, hashes,
  summary={**{"scope": scope}, **(extra_summary or {})})`; stores
  `_state["explanations"]` for the current run; returns the result dict.
- `detect_gaps(scope="all")` → `_run_gap_detection(scope, "production")`.
- New tool **`run_experiment(scope="all", hypothesis=None)`** →
  `_run_gap_detection(scope, "experiment", experiment_summary(scope, hypothesis))`;
  return dict includes `"kind": "experiment"`.

---

## Part C — Quarantine view + transition (`server.py`)

- New tool **`get_quality_metrics()`** → `{"status": "ok", **quality_metrics(_get_workspace())}`.
- New tool **`adopt_experiment(run_id, activation_id, rationale)`** — the transition
  path, routed through the existing gated lifecycle:
  1. `run = ws.get_run(run_id)`; error if missing or `run.kind != "experiment"`.
  2. `pg_version = run.pg_version`; error if `None`.
  3. `promo = promote_pg(pg_version, rationale)`; if `promo["status"] != "ok"`,
     return it (validation gate / rationale enforcement applies).
  4. `act = activate(activation_id, pg_version, set_as_production=True)`; return
     `{"status": act["status"], "activation_id": activation_id, "version_hash": pg_version, "promoted": True}`
     (or the error from `activate`).
  No new lifecycle logic — reuses `promote_pg` + `activate`.

---

## Part D — Capability surface

- Add an `experimentation` domain to `list_capabilities`:
  `["run_experiment", "get_quality_metrics", "adopt_experiment"]`.
- Update `mcp-server/CAPABILITIES.md` with an "Experimentation & Quarantine"
  section. The drift test (taxonomy == registered tools) keeps them in sync.

---

## Testing Strategy

- **Python (pytest)** — `tests/test_experiments.py`, using a real
  `pygrap.Workspace` in a tmp dir (no KG required):
  - Create 2 production runs (`start_run`/`complete_run`, gaps 1 then 3) + 1
    experiment run; assert `quality_metrics` → `production_runs == 2`,
    `production_completed == 2`, `last_production_run` is the second production
    run, `latest_gaps_found == 3`, `experiments_excluded == 1`.
  - `quality_metrics` on an empty workspace → zeros and `last_production_run is None`.
  - `experiment_summary("all", "my hypothesis")` → `{"scope":"all","experiment":"true","hypothesis":"my hypothesis"}`; with `None` hypothesis omits the key.
- **Capabilities drift test** passes with the new domain + 3 tools.
- **End-to-end MCP:** load KG + ontology + PG; `run_experiment(hypothesis="...")`
  → experiment run with persisted explanations; `get_quality_metrics` shows
  `experiments_excluded >= 1` and does not count it in `production_runs`;
  `detect_gaps()` (production) increments `production_runs`; publish+`adopt_experiment`
  promotes+activates a valid experiment's PG version (and is refused for an invalid one).

## Out of Scope (explicit)

First-class Rust experiment schema (hypothesis lives in the run `summary` map);
separate experiment retention windows (acknowledged in Task 07, not enforced);
reasoner parameter sweeps (the reasoner is deterministic over KG+ontology — the
experiment value here is the declared, quarantined, fully-audited run); Task 08.

## Acceptance Criteria

- Experiments are declared explicitly (`run_experiment`), clearly marked
  (`kind="experiment"` + hypothesis in summary), and fully audited with the same
  run + explanation records as production runs.
- `get_quality_metrics` aggregates only production runs; experiment runs are
  provably excluded (reported solely as `experiments_excluded`).
- Production flag / last-known-good (activation-based) are unaffected by experiment
  runs.
- There is a working transition path (`adopt_experiment`) that promotes an
  experiment's PG version into production through the existing validation-gated
  publish/activate flow.
