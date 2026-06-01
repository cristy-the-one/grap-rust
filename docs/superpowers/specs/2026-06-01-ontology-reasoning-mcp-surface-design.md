# Ontology/Reasoning Layer + MCP Surface + GraphError Fix — Design Spec

> Covers three bundled items in **one delivery / one branch** (`ontology-reasoning-mcp-surface`):
> 1. Fix the latent `pygrap.GraphError` bug (it is not a real Python exception).
> 2. **Task 04** — grap-native ontology + lightweight hybrid reasoning (gap detection + explanations).
> 3. **Task 02** — organize the MCP capability surface (taxonomy, audit/error semantics, docs).
>
> Implement via `superpowers:writing-plans`. Phased order: A (GraphError fix) → B (ontology) →
> C (reasoner) → D (MCP surface). The GraphError fix is bundled, not a dependency.

## Architecture Decisions (approved during brainstorming)

1. **Reasoner layer = Python (`grap_mcp`)** over the pygrap KG surface. grap does the graph-native
   structural work; Python holds the thin inference + explanation + ontology-parsing logic. Fast to
   iterate, fully testable, no rebuild per rule change.
2. **Ontology = projected into the KG + versioned.** Parse the ontology YAML, project it into the
   grap KG as nodes/edges, and publish it as a `VersionKind::Ontology` artifact in the Workspace so
   activations can freeze an ontology version.
3. **Task 02 = organize + audit + docs; auth deferred.** Coherent capability taxonomy, every mutating
   tool emits a Workspace audit event, uniform error envelope, capability docs. No auth layer (YAGNI
   for a local POC).

## Context (current state after the lifecycle merge)

- `mcp-server/grap_mcp/`: `server.py` (MCP tools), `procedure_graph.py` (PG parse/project + `_default_resolver`),
  `validation.py`, `diff.py`.
- Rust `grap` crate has `src/lifecycle/` with `Workspace`, `VersionStore` (`VersionKind::{Pg, Ontology}`),
  `ActivationStore`, `AuditLog` (`AuditEvent`), exposed via `crates/pygrap/src/lifecycle.rs`.
- pygrap KG surface: `add_node`, `add_edge`, `get_node_text`, `get_node_label`,
  `neighbors_by_relation`, `predecessors_by_relation`, `traverse_by_relation`, `search_and_traverse`
  (signature: `query, max_seeds, required_properties, min_strength, max_depth, max_results,
  text_weight, graph_weight`), `search_and_traverse_hybrid`, `node_count`.
- Example data: `docs/examples/minimal-aspice-ontology.yaml` (process outcomes with
  `required_characteristics` + `mapping_hints` of keywords/properties), `docs/examples/simple-aspice-gap-check.pg.yaml`
  (references `kg:aspice.process_outcome.SYS.5.BP3`, `kg:aspice.ontology.v1`).
- Known bug: `pygrap.GraphError` is a plain `#[pyclass]`, not a `PyException`; raising it yields
  "exceptions must derive from BaseException". The lifecycle bindings currently raise `PyRuntimeError`
  as a stopgap.

---

## Part A — GraphError Fix (Rust / pygrap)

**Files:** `crates/pygrap/src/errors.rs`, `crates/pygrap/src/lib.rs`, `crates/pygrap/src/lifecycle.rs`.

- Replace the `#[pyclass] struct GraphError` with a real exception type:
  ```rust
  pyo3::create_exception!(pygrap, GraphError, pyo3::exceptions::PyException);
  ```
- Provide helpers preserving existing call sites:
  ```rust
  pub fn to_py_err(err: grap::GraphError) -> PyErr { GraphError::new_err(err.to_string()) }
  ```
- Registration in `lib.rs` stays `m.add("GraphError", m.py().get_type::<GraphError>())?;`
  (`create_exception!` produces a type usable with `get_type`).
- Update `lifecycle.rs::to_py` to raise `GraphError::new_err(...)` instead of `PyRuntimeError`.
- Any existing `GraphError::new(...)` call sites are updated to `GraphError::new_err(...)` (returns a
  `PyErr` directly). Audit call sites with grep during implementation.

**Acceptance:** `issubclass(pygrap.GraphError, Exception)` is true; raising it in a binding surfaces a
catchable `pygrap.GraphError` in Python.

---

## Part B — Ontology Representation (`grap_mcp/ontology.py`)

New module. No rebuild required.

### B1. Parse model

```python
class ProcessOutcome:
    id: str                          # "SYS.5.BP3"
    name: str
    description: str
    required_characteristics: list[str]

class MappingHint:
    outcome: str                     # outcome id
    keywords: list[str]
    properties: list[str]

class Ontology:
    version: str                     # from YAML or default
    process_outcomes: list[ProcessOutcome]
    mapping_hints: list[MappingHint]
    def hint_for(outcome_id) -> MappingHint | None
    def outcome(outcome_id) -> ProcessOutcome | None
```

`load_ontology_from_file(path) -> Ontology` parses the YAML shape in
`docs/examples/minimal-aspice-ontology.yaml` (`process_outcomes[]` with `required_characteristics`,
and `mapping_hints[]` whose `indicators` carry `keywords`/`properties`; flatten indicators into the
`MappingHint` lists).

### B2. Projection into the KG

`project_ontology_to_grap(ontology, kg) -> dict`:
- For each outcome: `node = kg.add_node(text=f"[ASPICE outcome] {o.name}: {o.description}", label=f"kg:aspice.process_outcome.{o.id}")`.
  The label is exactly the form PG `references` use, so the Task 05 resolver resolves them.
- For each required characteristic of an outcome: `cnode = kg.add_node(text=f"[characteristic] {char}", label=f"kg:aspice.characteristic.{o.id}.{char}")`,
  then `kg.add_edge(outcome_node, cnode, strength=1.0, props={"type": "requires", "characteristic": char})`.
- Return `{"outcomes_projected": int, "characteristics_projected": int}` and an
  `outcome_node_map: {outcome_id: node_id}` (returned for reuse by the reasoner).

### B3. MCP tool `load_ontology(path)`

- Parse → project into the loaded KG (error if no KG loaded) → publish as a `VersionKind::Ontology`
  version in the Workspace (`ws.publish_ontology(raw_yaml, metadata)` — see Part D for the new
  binding) → store `_state["ontology"] = ontology` and `_state["ontology_version"] = version.hash`.
- Emit audit event `ontology_loaded`.
- Returns `{"status": "ok", "version_hash", "outcomes_projected", "characteristics_projected"}`.

---

## Part C — Reasoner + Gap Detection + Explanation (`grap_mcp/reasoner.py`)

New module. Pure Python over the pygrap KG surface; injectable for testing.

### C1. Scope resolution

`scoped_outcomes(ontology, pg) -> list[ProcessOutcome]`:
- If `pg` references any `kg:aspice.process_outcome.<id>`, return those outcomes (intersected with the
  ontology).
- Else return all `ontology.process_outcomes`.

### C2. Hybrid characteristic satisfaction

`characteristic_signals(kg, ontology, outcome, characteristic) -> dict`:
- **structural signal:** scan for a KG node whose label is `kg:aspice.characteristic.<outcome>.<char>`
  that has incoming evidence, OR a property named in the outcome's mapping-hint `properties` present
  on a related node. Represented as a list of `{"kind": "structural", "detail": ...}`.
- **lexical signal:** run `kg.search_and_traverse(query=<characteristic + mapping-hint keywords>,
  max_seeds=5, ...)`; keep hits with `fused_score >= LEXICAL_THRESHOLD` (constant, default 0.2).
  Represented as `{"kind": "lexical", "node": id, "score": float, "snippet": str}`.
- A characteristic is **satisfied** when it has at least one structural signal, or a lexical signal
  above threshold. `confidence` = max signal score (structural = 1.0).

### C3. Gap detection

`detect_gaps(kg, ontology, pg=None) -> list[GapResult]`:
- For each in-scope outcome, for each required characteristic, compute signals.
- Unsatisfied characteristic → `GapResult`:
  ```python
  {
    "outcome_id": str, "outcome_name": str,
    "missing_characteristic": str,
    "signals": {"structural": [...], "lexical": [...]},
    "confidence": float,          # confidence that this IS a gap (1 - best satisfaction signal)
  }
  ```
- Returns the list (empty == no gaps).

### C4. Explanation generation

`explain_gap(gap, ontology_version_hash) -> dict` — sectioned:
```python
{
  "target": f"{outcome_id}:{characteristic}",
  "sections": {
     "structured": {"outcome": ..., "characteristic": ..., "expected": <description>},
     "statistical": {"queries": [...], "top_hits": [{node, score, snippet}], "threshold": float},
     "rendered": "<human-readable paragraph>",
  },
  "raw_signals": {"ontology_version": hash, "outcome_id": ..., "characteristic": ..., "signals": {...}},
}
```
Raw signals are sufficient to reproduce the decision (ontology version + queries + hits).

### C5. Integration in `server.py`

- Rewrite `detect_gaps(scope)` tool: require KG + ontology; run `reasoner.detect_gaps`; store
  `_state["explanations"] = {gap.target: explain_gap(...)}`; emit audit event `gap_detection_run`
  (details: outcomes checked, gaps found); return `{"status": "ok", "gaps": [...], "gaps_found": n}`.
- Rewrite `get_explanation(target)` tool: return the stored explanation for `target`, or a not-found
  envelope. (Durable explanation records are Task 03 — out of scope.)
- Update `run_procedure`: when KG + ontology present, call `reasoner.detect_gaps` and include
  `gaps_found` + a short gap summary in the result; emit audit event `procedure_run`.

---

## Part D — MCP Capability Surface (Task 02)

### D1. `Workspace.record_event` (Rust + pygrap)

- Rust `src/lifecycle/workspace.rs`: add
  ```rust
  pub fn record_event(&self, event_type: &str, subject: &str,
                      details: BTreeMap<String, String>, rationale: Option<String>)
                      -> Result<(), LifecycleError>
  ```
  appending an `AuditEvent` with `now_millis()`.
- pygrap `lifecycle.rs`: expose
  `record_event(&self, event_type, subject, details=None, rationale=None) -> PyResult<()>`.
- Add `Workspace.publish_ontology(content, metadata=None)` (Rust + pygrap): same as `publish_pg` but
  `VersionKind::Ontology`, audit `version_published` with `kind=ontology`. (Rust adds
  `publish_ontology`; reuse the existing publish path with a kind argument.)

### D2. Audit on mutating Python tools

Every mutating tool emits a Workspace audit event via `record_event` (lifecycle tools already audit
in Rust): `load_knowledge_graph` → `kg_loaded`; `load_procedure_graph` → `pg_loaded`;
`load_ontology` → `ontology_loaded`; `detect_gaps`/`run_procedure` → `gap_detection_run`/`procedure_run`.
Tools that mutate but have no workspace yet open one lazily via `_get_workspace()`.

### D3. Uniform error envelope

Every tool returns `{"status": "ok"|"error", ...}`. Audit/read tools that currently return bare dicts
(e.g. `get_status`, `get_production`) are left as-is where they already convey status, but error paths
always use `{"status": "error", "message": ...}`.

### D4. `list_capabilities()` tool

Returns the capability taxonomy:
```python
{
  "domains": {
     "knowledge": ["load_knowledge_graph", "load_ontology"],
     "pg_lifecycle": ["load_procedure_graph", "publish_pg", "promote_pg", "activate",
                      "set_production", "get_production", "list_versions"],
     "execution_reasoning": ["run_procedure", "detect_gaps", "get_explanation"],
     "authoring": ["validate_procedure_graph", "diff_procedure_graphs"],
     "audit": ["get_audit_log", "get_status", "list_capabilities"],
  }
}
```

### D5. Capability docs

`mcp-server/CAPABILITIES.md`: one section per domain; each tool with a one-line purpose, params,
return shape, and a short example. Note auth is deferred.

---

## Testing Strategy

- **Python (pytest):**
  - `ontology.py`: parse the example YAML; `project_ontology_to_grap` against a `FakeKG` creates the
    right nodes/edges and labels (`kg:aspice.process_outcome.SYS.5.BP3`).
  - `reasoner.py`: on a synthetic KG/ontology — (a) all characteristics satisfied → no gaps;
    (b) a missing characteristic → one gap with correct `outcome_id`/`missing_characteristic`;
    (c) partial → only the unsatisfied ones; (d) `explain_gap` returns the three sections + raw signals.
  - `scoped_outcomes` honors PG references.
  - `list_capabilities` returns all current tools grouped (guard against drift).
- **Rust (`cargo test`):** `Workspace::record_event` appends a readable event; `publish_ontology`
  stores a `VersionKind::Ontology` version.
- **pygrap:** rebuild via maturin; smoke — `issubclass(pygrap.GraphError, Exception)`,
  `record_event` + `read_audit` round-trip, `publish_ontology`.
- **End-to-end MCP:** load KG + ontology + PG → publish/promote/activate → `detect_gaps` finds a
  seeded gap → `get_explanation` returns its sections → `get_audit_log` shows `ontology_loaded` and
  `gap_detection_run`.

## Build Notes

Parts A and D1 require a pygrap rebuild (`maturin build --release` + force-reinstall into user
site-packages, per the lifecycle delivery). Parts B, C, D2–D5 are pure Python.

## Out of Scope (explicit)

Durable/immutable explanation records and the full Temporal Graph (Task 03); publish-time validation
engine beyond existing format checks (Task 06); experiment quarantine (Task 07); config governance
beyond frozen weights (Task 08); MCP authentication/authorization.

## Acceptance Criteria

**GraphError:** `pygrap.GraphError` is a `PyException` subclass and is raisable/catchable.

**Task 04:**
- `load_ontology` parses, projects outcome/characteristic nodes (+`requires` edges) into the KG, and
  versions the ontology in the Workspace.
- `detect_gaps` finds meaningful ASPICE gaps using combined structural + lexical signals and returns
  structured results.
- `get_explanation` returns a sectioned explanation (structured + statistical + rendered) with raw
  signals sufficient to reproduce the decision.
- Projected outcome nodes are labelled so existing PG `kg:` references resolve to them.

**Task 02:**
- Capabilities are grouped into documented domains (`list_capabilities` + `CAPABILITIES.md`).
- Every mutating tool emits a Workspace audit event.
- Tools share a uniform `{"status": ...}` envelope.
