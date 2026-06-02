# grap-mcp-server — Capability Surface

POC MCP capability taxonomy. Auth is **deferred** (no authentication/authorization yet).
Every mutating capability emits a Workspace audit event (see `get_audit_log`).

## Knowledge
- **load_knowledge_graph(path)** → `{status, node_count}`. Load an on-disk grap KG. Emits `kg_loaded`.
- **create_knowledge_graph(path)** → `{status, node_count}`. Create a fresh on-disk KG. Emits `kg_created`.
- **ingest_documents(path)** → `{status, files, nodes_added}`. Chunk + add .md/.txt docs under `path`
  into the loaded KG as real evidence. Emits `documents_ingested`.
- **load_ontology(path)** → `{status, version_hash, outcomes_projected, characteristics_projected}`.
  Parse an ASPICE ontology YAML, project it into the KG, and version it. Emits `ontology_loaded`.

## Procedure Graph Lifecycle
- **load_procedure_graph(path)** → `{status, name, version, step_count, projection}`. Emits `pg_loaded`.
- **publish_pg(level="draft", rationale=None)** → `{status, version_hash, level}`. Emits `version_published`.
- **promote_pg(version_hash, rationale)** → `{status, level}`. Emits `version_promoted`.
- **activate(activation_id, version_hash, weights=None, set_as_production=False, kg_version=None)**
  → `{status, activation_id, is_production}`. Emits `activation_created` (+ `production_flag_moved`).
- **set_production(activation_id)** → `{status, production_id}`. Emits `production_flag_moved`.
- **get_production()** → current production activation for the loaded logic.
- **list_versions()** → published PG versions.

## Execution & Reasoning
- **run_procedure(scope="all")** → `{status, gaps_found, gap_summary, ...}`. Runs gap detection when
  an ontology is loaded. Emits `procedure_run`.
- **detect_gaps(scope="all")** → `{status, gaps, gaps_found}`. Hybrid ASPICE gap detection. Emits
  `gap_detection_run`.
- **get_explanation(target)** → `{status, explanation}`. Sectioned explanation for a gap target
  (`<outcome_id>:<characteristic>`).

## Experimentation & Quarantine
- **run_experiment(scope="all", hypothesis=None)** → `{status, run_id, kind:"experiment", gaps_found}`.
  Runs gap detection as a quarantined experiment with full audit parity (run record + explanations).
- **get_quality_metrics()** → production-only quality view; experiments reported only as
  `experiments_excluded` and never counted in `production_runs`.
- **adopt_experiment(run_id, activation_id, rationale)** → promotes the experiment's PG version
  into production through the validation-gated `promote_pg` + `activate` path.

## Configuration Governance
- **get_config()** → `{status, weights, retention, last_changed}`.
- **set_weight(name, value, rationale)** → set a prioritization weight (rationale required; audited).
- **set_retention(key, days, rationale)** → set a retention-policy value in days (rationale required;
  audited).
- **enforce_retention()** (admin) → hard-deletes runs older than the configured windows
  (`production_days`/`experiment_days`) + their orphaned explanation records; audited.

## Roles & Access
- **get_role()** → current role + available roles (`viewer` < `operator` < `admin`).
- **set_role(role)** → set the current role (POC simulation; no identity backend).

> Mutating capabilities require **operator** (lifecycle/execution) or **admin**
> (governance + retention). Reads are open to **viewer**. Default role comes from
> the `GRAP_ROLE` env var (`operator`).

> Weight changes are audited (`config_changed`). `activate` freezes the governed weights when none are
> passed and surfaces `frozen_weights` + `weights_last_changed`.

## Authoring & Validation
- **validate_procedure_graph(path)** → `{status, ok, errors, warnings}` (publish-time rules; non-blocking).
- **diff_procedure_graphs(path_a, path_b)** → `{status, diff}`.
- **get_validation(version_hash)** → cached validation report for a published PG version.

> `publish_pg` runs validation (non-blocking) and stores the report; `promote_pg` is
> **refused when the report has errors**. Reports are cached by PG content hash and
> invalidated when the loaded ontology version changes.

## Audit & Introspection
- **get_audit_log()** → workspace audit events.
- **get_status()** → loaded KG/PG/lifecycle state.
- **list_capabilities()** → this taxonomy.
- **list_runs(kind=None)** → execution runs (filter by production|experiment).
- **get_run(run_id)** → a single run record with frozen versions + explanation hashes.
- **reconstruct_run(run_id)** → reproduction completeness report (names missing artifacts).
- **query_events(event_type=None, run_id=None)** → Temporal Graph events (filtered, or for a run).
