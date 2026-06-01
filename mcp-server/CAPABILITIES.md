# grap-mcp-server — Capability Surface

POC MCP capability taxonomy. Auth is **deferred** (no authentication/authorization yet).
Every mutating capability emits a Workspace audit event (see `get_audit_log`).

## Knowledge
- **load_knowledge_graph(path)** → `{status, node_count}`. Load an on-disk grap KG. Emits `kg_loaded`.
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

## Authoring
- **validate_procedure_graph(path)** → `{status, ok, errors, warnings}`.
- **diff_procedure_graphs(path_a, path_b)** → `{status, diff}`.

## Audit & Introspection
- **get_audit_log()** → workspace audit events.
- **get_status()** → loaded KG/PG/lifecycle state.
- **list_capabilities()** → this taxonomy.
- **list_runs(kind=None)** → execution runs (filter by production|experiment).
- **get_run(run_id)** → a single run record with frozen versions + explanation hashes.
- **reconstruct_run(run_id)** → reproduction completeness report (names missing artifacts).
- **query_events(event_type=None, run_id=None)** → Temporal Graph events (filtered, or for a run).
