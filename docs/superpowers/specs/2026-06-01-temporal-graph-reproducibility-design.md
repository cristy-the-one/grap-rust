# Auditable Execution + Temporal Graph + Reproducibility — Design Spec

> Covers **Task 03** (auditable execution model + Temporal Graph) and **Task 09**
> (reproducibility primitives) in one delivery / one branch
> (`temporal-graph-reproducibility`). Implement via `superpowers:writing-plans`.
> Phased: A (explanation versions) → B (run records) → C (relational event graph) →
> D (workspace API + reconstruction) → E (pygrap + MCP wiring).

## Architecture Decisions (approved during brainstorming)

1. **Durable stores live in the Rust `Workspace`** (the single authority for
   audit/lifecycle state), exposed via pygrap. The Python reasoner/MCP layer
   persists runs and explanations during execution.
2. **Temporal Graph = a relational event graph**: `AuditEvent` gains typed
   `links` to entities (run, activation, version, KG node, PG step); a query
   layer traverses/filters them. Not a second grap instance.
3. **Experiment quarantine = a `kind` flag only.** Run records carry
   `kind = production | experiment`; experiment runs are excluded from
   "last known good" / quality views. Full quarantine + promotion path stays Task 07.

## Context (current state)

- `src/lifecycle/`: `Workspace` over `VersionStore` (`VersionKind::{Pg, Ontology}`),
  `PublishStore`, `ActivationStore`, `AuditLog` (`AuditEvent { ts, event_type,
  actor, subject, details, rationale }`). `Workspace` already has `publish_pg`,
  `publish_ontology`, `promote`, `activate` (freezes `pg_version` + `frozen_weights`
  + `kg_version`), `set_production`, `record_event`, `read_audit`, `list_versions`.
- pygrap exposes `Workspace`, `Version`, `Activation`, `AuditEvent` (+ `record_event`,
  `publish_ontology`).
- Python reasoner (`grap_mcp/reasoner.py`) produces gap `explain_gap(...)` dicts;
  `server.py` currently stores them only in `_state["explanations"]` (in-memory).
- `AuditEvent` JSONL log at `<ws>/audit/events.jsonl`.

---

## Part A — Explanation records via content-addressing

**File:** `src/lifecycle/version.rs`.

- Add a third variant to `VersionKind`: `Explanation`.
- No other change: immutable explanation records (raw signals + rendered snapshot,
  serialized as JSON) are stored through the existing `VersionStore::put`
  (content-addressed blob + meta), yielding a stable `ContentHash` for run records
  to reference. Dedupe comes for free.

---

## Part B — Run records (`src/lifecycle/run.rs`, new)

```rust
pub enum RunKind { Production, Experiment }
pub enum RunStatus { Running, Completed, Failed }

pub struct RunRecord {
    pub run_id: String,
    pub kind: RunKind,
    pub status: RunStatus,
    pub started_at: u64,                  // unix millis
    pub completed_at: Option<u64>,
    pub activation_id: Option<String>,
    pub pg_version: Option<ContentHash>,  // frozen — reproducibility (Task 09)
    pub kg_version: Option<ContentHash>,  // frozen — reproducibility (Task 09)
    pub scope: String,
    pub gaps_found: u32,
    pub explanation_hashes: Vec<ContentHash>,
    pub summary: BTreeMap<String, String>,
}
```

`RunStore` (filesystem at `<ws>/runs/`, mirrors `ActivationStore`):
- `open(base)`, `put(&RunRecord)`, `get(run_id)`, `list()`, `list_by_kind(RunKind)`.

`RunRecord` derives `PartialEq` only? It holds no `f64`, so `Eq` is fine. Derive
`Debug, Clone, PartialEq, Eq, Serialize, Deserialize`. Enums derive `Copy` too.

---

## Part C — Relational event graph (`src/lifecycle/audit.rs`)

- New type:
  ```rust
  pub struct EventLink { pub relation: String, pub kind: String, pub id: String }
  ```
  (e.g. `{relation:"produced", kind:"run", id:"run-7"}`,
  `{relation:"used", kind:"pg_version", id:"<hash>"}`,
  `{relation:"touched", kind:"kg_node", id:"42"}`).
- Extend `AuditEvent` with `#[serde(default)] pub links: Vec<EventLink>` so existing
  JSONL lines (without `links`) still deserialize.
- `AuditLog` query methods (the relational/traversal surface):
  - `query(&self, event_type: Option<&str>, since: Option<u64>, until: Option<u64>) -> Result<Vec<AuditEvent>>`
  - `events_for_run(&self, run_id: &str) -> Result<Vec<AuditEvent>>`
    (events whose `links` contain `{kind:"run", id:run_id}` OR whose `subject == run_id`).
  - `events_linked_to(&self, kind: &str, id: &str) -> Result<Vec<AuditEvent>>`
    (events with a link matching `kind`+`id`).

All read the single append-only log; no second store.

---

## Part D — Workspace API + reconstruction (`src/lifecycle/workspace.rs`)

New methods on `Workspace`:

- `start_run(run_id, kind, activation_id, pg_version, kg_version, scope) -> RunRecord`
  — writes a `Running` record; appends a `run_started` event linked to the
  activation / pg_version / kg_version.
- `record_explanation(content: &[u8], metadata) -> Version` — stores a
  `VersionKind::Explanation` blob.
- `complete_run(run_id, status, gaps_found, explanation_hashes, summary) -> RunRecord`
  — updates the record (`completed_at`, status, gaps, explanation hashes); appends a
  `run_completed` event linked to the run + each explanation.
- `get_run(run_id)`, `list_runs(kind: Option<RunKind>)`.
- Temporal queries delegating to `AuditLog`: `temporal_query`, `events_for_run`,
  `events_linked_to`.
- `reconstruct(run_id) -> ReconstructionReport`:
  ```rust
  pub struct ReconstructionReport {
      pub run_id: String,
      pub complete: bool,
      pub pg_version_present: bool,
      pub kg_version_present: bool,
      pub explanations_present: usize,
      pub explanations_total: usize,
      pub missing: Vec<String>,   // human-readable, e.g. "pg_version <hash> missing"
  }
  ```
  Resolves the run's frozen `pg_version`/`kg_version`/explanation hashes against the
  `VersionStore`; `complete` is true only when every referenced artifact is present.
  Missing artifacts are reported (Task 09: "reproduction incomplete — artifact X
  missing"), never silently ignored.

`RunStore` is opened by `Workspace::open` alongside the other stores.

---

## Part E — pygrap bindings + MCP wiring

### E1. pygrap (`crates/pygrap/src/lifecycle.rs`)
- New `#[pyclass(get_all)]` wrappers: `RunRecord` (run_id, kind, status, gaps_found,
  pg_version, kg_version, explanation_hashes), `ReconstructionReport` (all fields).
- Extend the `AuditEvent` wrapper with `links` (list of `{relation, kind, id}` dicts).
- New `Workspace` methods:
  - `start_run(run_id, kind="production", activation_id=None, pg_version=None, kg_version=None, scope="all") -> RunRecord`
  - `record_explanation(content, metadata=None) -> Version`
  - `complete_run(run_id, status="completed", gaps_found=0, explanation_hashes=None, summary=None) -> RunRecord`
  - `get_run(run_id) -> Optional[RunRecord]`, `list_runs(kind=None) -> [RunRecord]`
  - `reconstruct(run_id) -> ReconstructionReport`
  - `query_events(event_type=None, since=None, until=None) -> [AuditEvent]`
  - `events_for_run(run_id) -> [AuditEvent]`
  - `events_linked_to(kind, id) -> [AuditEvent]`

### E2. MCP wiring (`mcp-server/grap_mcp/server.py`)
- `detect_gaps(scope="all", kind="production")`:
  1. `run_id = f"run-{uuid4().hex[:8]}"`; resolve current production activation (if any)
     for `activation_id` / frozen versions, else use `_state["ontology_version"]`.
  2. `ws.start_run(run_id, kind, activation_id, pg_version, kg_version, scope)`.
  3. Run the reasoner; for each gap, build the explanation, persist it via
     `ws.record_explanation(json.dumps(exp))` → collect hashes; keep `_state["explanations"]`
     for the current run (by target).
  4. `ws.complete_run(run_id, "completed", len(gaps), hashes, summary)`.
  5. Return `{status, run_id, gaps, gaps_found}`.
- `run_procedure` similarly wraps a run (`start_run`/`complete_run`) around the
  existing gap detection.
- `get_explanation(target)`: serve from `_state["explanations"]`; if absent and a
  hash/explanation is requested, fall back to nothing (current-run scope is enough
  for the POC — durable retrieval is by run record).
- New tools: `list_runs(kind=None)`, `get_run(run_id)`, `reconstruct_run(run_id)`,
  `query_events(event_type=None)`.
- Update `list_capabilities` (`audit` domain gains `list_runs`, `get_run`,
  `reconstruct_run`, `query_events`) + `CAPABILITIES.md`. Update the capabilities
  drift test accordingly.

---

## Testing Strategy

- **Rust (`cargo test`)**:
  - `VersionKind::Explanation` round-trips through `VersionStore`.
  - `RunStore` put/get/list/list_by_kind.
  - `AuditEvent` with no `links` in JSON deserializes (serde default).
  - `AuditLog.events_for_run` / `events_linked_to` / filtered `query` return the
    right subsets.
  - `Workspace`: `start_run` → `record_explanation` → `complete_run` produces a
    completed run with explanation hashes + `run_started`/`run_completed` events.
  - `reconstruct`: complete when all artifacts present; reports `missing` when an
    explanation/version hash is absent.
- **pygrap**: rebuild; smoke — start/complete a run, reconstruct (complete), query
  events for the run.
- **Python (pytest)**: existing reasoner/ontology tests unchanged; capabilities
  drift test updated to include the 4 new tools.
- **End-to-end MCP**: load KG + ontology → `detect_gaps` creates a run + persisted
  explanations → `get_run` shows gaps + explanation hashes → `reconstruct_run`
  reports `complete: true` → `query_events`/`events_for_run` return the run's
  `run_started`/`gap_detection_run`/`run_completed` events.

## Build Notes

Parts A–D and E1 require a pygrap rebuild (`maturin build --release` + force-reinstall
into user site-packages). E2 is pure Python.

## Out of Scope (explicit)

Full experiment quarantine machinery + promotion path (Task 07 — only the `kind`
flag here); publish-time validation (Task 06); config-governance tooling (Task 08);
enforced retention/cleanup policies (configurable retention is acknowledged but not
implemented); migrating the relational event graph into an actual grap instance.

## Acceptance Criteria

**Task 03:**
- Every production and experimental run produces a durable `RunRecord` (medium-rich:
  timeline events + linked artifacts).
- Explanation records are immutable (content-addressed) and hold raw signals + the
  rendered snapshot.
- The Temporal Graph can answer "what happened in run X?" (`events_for_run`) and
  "what touched entity Y?" (`events_linked_to`), plus filtered timeline queries.
- Experimental runs are marked (`kind`) and excludable from production views.

**Task 09:**
- A run record + its frozen `pg_version`/`kg_version` determine exactly what logic,
  weights, and KG/ontology state were used.
- `reconstruct(run_id)` reports completeness and names any missing artifact rather
  than failing silently.
