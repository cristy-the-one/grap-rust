# Publish & Validation Pipeline — Design Spec

> Covers **Task 06** (publish & validation pipeline) in one delivery / one branch
> (`publish-validation-pipeline`). Implement via `superpowers:writing-plans`.
> Phased: A (validation engine, Python) → B (Rust primitives) → C (pygrap) →
> D (publish/promote wiring + tools).

## Architecture Decisions (approved during brainstorming)

1. **Errors block promotion; warnings don't.** Promotion runs validation and is
   refused when the report has errors. Draft publish and on-save validation never
   block.
2. **Validation reports reuse content-addressed records + Temporal-Graph links.**
   A report is stored as a `VersionKind::Validation` record and linked to its PG
   version by a `validation_run` event; the cache is keyed by PG content hash.
3. **Publish-time validation checks PG validity + cross-references only** — no gap
   detection at publish (that is execution over KG content, not PG validity).

## Context (current state)

- `grap_mcp/validation.py::validate_pg(pg, resolver) -> {ok, errors, warnings}`:
  structural checks are errors; unresolved `kg:` refs are warnings.
- `grap_mcp/procedure_graph.py::_default_resolver(kg)`; `grap_mcp/ontology.py`
  (`Ontology.process_outcomes`, each `ProcessOutcome.id`).
- Rust `Workspace`: `publish_pg` (Draft/Promoted, rationale-gated on promote),
  `promote`, `activate` (requires Promoted), `record_explanation`
  (`VersionKind::Explanation`), `record_event`, relational Temporal Graph
  (`AuditEvent.links`, `events_linked_to`, `events_for_run`, `query`).
- pygrap exposes `Workspace` (publish_pg/promote/activate/record_explanation/
  record_event/events_linked_to/query_events/...), `Version`, `RunRecord`, etc.
- MCP `server.py`: `publish_pg`, `promote_pg`, `validate_procedure_graph`,
  `list_capabilities` (domains incl. `authoring`), capabilities drift test.

---

## Part A — Validation engine (`grap_mcp/publish_validation.py`, new)

```python
def validate_for_publish(pg, kg=None, ontology=None) -> dict:
    """Return {ok, errors, warnings} for publish-time validation."""
```

- Run `validate_pg(pg, resolver=_default_resolver(kg) if kg else None)`; copy its
  `errors` and `warnings`.
- **Ontology reference validity (errors):** for each step reference of the form
  `kg:aspice.process_outcome.<id>`, if `<id>` is not in
  `{o.id for o in ontology.process_outcomes}`, append an error
  `{"code": "unknown_process_outcome", "message": ..., "step_id": step.id}`.
- If `ontology is None`, append a single warning
  `{"code": "ontology_not_loaded", "message": "...", "step_id": None}` and skip the
  outcome-validity check (don't block on missing context).
- Return `{"ok": len(errors) == 0, "errors": errors, "warnings": warnings}`.

The caller stamps `report["ontology_version"] = <hash or None>` before persisting,
for cache invalidation.

`validation.py` is unchanged; `publish_validation.py` composes it.

---

## Part B — Rust Workspace primitives

**Files:** `src/lifecycle/version.rs`, `src/lifecycle/workspace.rs`,
`src/lifecycle/mod.rs` (no re-export changes needed beyond `VersionKind`, already
exported).

- `version.rs`: add `VersionKind::Validation`.
- `workspace.rs`:
  - `record_validation(&self, pg_hash: &str, content: &[u8], ok: bool, metadata: BTreeMap<String,String>) -> Result<Version>`:
    stores `content` as a `VersionKind::Validation` version; appends a
    `validation_run` `AuditEvent` with `subject = pg_hash` and links
    `[{used, pg_version, pg_hash}, {produced, validation, <record hash>}]` and
    detail `{"ok": ok.to_string()}`.
  - `get_record(&self, hash: &ContentHash) -> Result<Option<Vec<u8>>>`: returns the
    stored bytes for any version hash (PG, ontology, explanation, validation), or
    `None`.
- Tests: `record_validation` creates a `Validation` version and a linked
  `validation_run` event (`events_linked_to("pg_version", pg_hash)` finds it);
  `get_record` round-trips a stored PG version's bytes and returns `None` for an
  unknown hash.

`VersionKind::Validation` also requires updating the existing pygrap `Version`
`From` impl match (Part C) to map the new variant.

---

## Part C — pygrap bindings

**File:** `crates/pygrap/src/lifecycle.rs`

- Map `VersionKind::Validation => "validation"` in the `Version` `From` impl.
- New `Workspace` methods:
  - `record_validation(pg_hash, content, ok, metadata=None) -> Version`
  - `get_record(hash) -> Optional[str]` (UTF-8 decode of the stored bytes;
    validation/PG content is JSON/YAML text).
- No new pyclasses (returns `Version` / `str`). Rebuild + reinstall pygrap.

---

## Part D — Publish/promote wiring + tools (`grap_mcp/server.py`)

- **`publish_pg(level="draft", rationale=None)`**: after `ws.publish_pg(...)`,
  build `report = validate_for_publish(pg, kg, ontology)`,
  `report["ontology_version"] = _state.get("ontology_version")`,
  `ws.record_validation(version.hash, json.dumps(report), report["ok"])`. Return
  `{status:"ok", version_hash, level, validation: {ok, error_count, warning_count}}`.
  Never blocks.
- **`promote_pg(version_hash, rationale)`**:
  1. Resolve the report for `version_hash`:
     - Cache: `events_linked_to("pg_version", version_hash)` → latest event with
       `type == "validation_run"` → its `validation` link id → `get_record(id)` →
       parse. Use it only if `report.get("ontology_version") == _state.get("ontology_version")`.
     - Else (miss/stale): reload the PG via `get_record(version_hash)`, parse with
       `pg_loader` (write to a temp file or parse the YAML string), run
       `validate_for_publish`, stamp `ontology_version`, and `record_validation(...)`.
  2. If `report["errors"]`: return `{status:"error", message:"validation failed; cannot promote", validation: report}` and do **not** call `ws.promote`.
  3. Else `ws.promote(version_hash, rationale)`; return
     `{status:"ok", version_hash, level:"promoted", validation: {ok:true, warning_count}}`.
- **`get_validation(version_hash)`** (new tool): cache lookup as above; return
  `{status:"ok", validation: report}` or `{status:"error", message:"not validated yet"}`.
- **`validate_procedure_graph(path)`** (existing): re-implement on top of
  `validate_for_publish(pg, kg, ontology)` so on-save and publish rules match;
  still returns `{status:"ok", **report}` and never blocks.
- **Parsing a PG from a YAML string:** add `pg_loader.load_from_string(text)` to
  `procedure_graph.py` (refactor `load_from_file` to read the file then call it),
  so promote's cold path can parse `get_record(version_hash)` without a temp file.
- **Capabilities:** rename/extend the taxonomy — replace the `authoring` domain with
  a `validation` domain `["validate_procedure_graph", "diff_procedure_graphs", "get_validation"]`.
  Update `list_capabilities`, `CAPABILITIES.md`, and rely on the drift test to keep
  them in sync (it asserts taxonomy == registered tools).

---

## Testing Strategy

- **Python (pytest):**
  - `validate_for_publish`: clean PG (with matching ontology) → `ok` true;
    bad data-flow ref → structural error; `kg:aspice.process_outcome.BOGUS` →
    `unknown_process_outcome` error; `ontology=None` → `ontology_not_loaded`
    warning and no outcome errors.
  - `load_from_string` parses the same as `load_from_file`.
  - Capabilities drift test still passes with the new `validation` domain + tool.
- **Rust (`cargo test`):** `record_validation` + `events_linked_to`; `get_record`
  round-trip + unknown-hash `None`.
- **pygrap:** rebuild; smoke `record_validation` / `get_record`.
- **End-to-end MCP:** publish a PG referencing a bogus outcome → publish `ok` with
  `validation.ok == false`; `promote_pg` refused (no Promoted record, activate still
  rejects); load a corrected PG → publish → `promote_pg` succeeds → `activate`
  works; `query_events` shows a `validation_run` linked to the PG version.

## Build Notes

Parts B–C require a pygrap rebuild (`maturin build --release` + force-reinstall into
user site-packages). Parts A and D are pure Python.

## Out of Scope (explicit)

Incremental/partial re-validation (whole reports cached by hash); KG content-quality
validation / gap detection at publish; experiment quarantine (Task 07); config
governance (Task 08).

## Acceptance Criteria

- Publishing as Draft is always possible and never blocks; a validation report is
  produced and stored.
- Promoting runs (or reuses cached) validation and is **refused when there are
  errors**; on success the version becomes Promoted (and therefore activatable).
- Validation covers structural validity + `kg:` reference resolvability + ontology
  process-outcome reference validity.
- Every publish/promote validation produces a `validation_run` Temporal-Graph event
  linked to the PG version, and the report is retrievable via `get_validation`.
- On-save `validate_procedure_graph` and publish-time validation apply the same
  rules.
