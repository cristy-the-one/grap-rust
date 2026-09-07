# grap-mcp-server — Capability Surface

MCP capability taxonomy. Application role checks enforce the configured role;
identity authentication is not provided by those checks. Most lifecycle mutations
emit Workspace audit events (see `get_audit_log`); legal claim receipts and source
status are retained in their workspace-owned SQLite ledger.

## Knowledge

- **distill_ontology(path, model="grap-legal-gemma4", max_calls=8, min_documents=2)**:
  resumable automatic class/predicate discovery from KEE receipts. Returns source
  mentions, candidate classes, checked relation mappings, descriptive patterns and
  explicit abstentions. Operator only; caches belong to the workspace. Zero calls
  reads cached work. [Workflow and limitations](../docs/automatic-legal-vocabulary.md).

The full tier also includes a separate [Romanian legal statement path](../docs/legal-rules-2026-09.md):

- **extract_legal_claims(path, max_files=3, max_chars=8000, refresh=False, model=None, max_batches=None)**:
  bounded whole-document extraction of source statements with proposed kinds and
  grounded subjects. Explicit definitions are parsed directly; ordinary rules keep
  their full statement and context. Zero `max_files` scans coverage without extraction.
  The v8 extractor retains the literal-subject model contract and anchors phrases within
  each statement, with syntax checks for applicability and postposed subjects.
  [Semantic evaluation](../docs/legal-semantics-2026-09.md) distinguishes role accuracy
  from exact evidence preservation.
  Set `max_batches` to checkpoint validated batches and cap new labeling calls across
  the invocation. Raise `max_chars` explicitly for larger acts. Incomplete work is
  visible through source history and is excluded from current claims and snapshots.
  See [resumption and local context setup](../docs/legal-resume-2026-09.md).
- **create_legal_review**, **get_legal_review**, **process_legal_review**,
  **save_legal_review**: freeze a reproducible document sample, process one resumable
  batch per call, and store versioned human judgments and omissions against pinned
  full-source receipts. Read/export includes uncertainty and unreviewed counts;
  annotations never rewrite model claims. The [browser workbench](../docs/legal-review-workbench.md)
  uses these same operations. Mutations require the operator role.
- **get_legal_claims(kind=None, source_id=None, revision_id=None, include_stale=False,
  limit=20, offset=0)**: latest stored claims, or an exact historical source receipt.
- **get_legal_sources(source_id=None, limit=20, offset=0)**: source status and extraction history.
- **resolve_legal_term(term, source_id)**: explicit definitions within one source;
  dotted abbreviations are recognized and conflicting meanings remain ambiguous.
- **export_legal_claim_graph()**: a new Rust snapshot of qualified claim nodes linked
  to source receipts. It does not alter the active graph or adopt claims as legal truth.

- **load_knowledge_graph(path)** → `{status, node_count}`. Load an on-disk grap KG. Emits `kg_loaded`.
- **create_knowledge_graph(path)** → `{status, node_count}`. Create a fresh on-disk KG. Emits `kg_created`.
- **ingest_documents(path)** → `{status, files, nodes_added, embedded}`. Chunk + add .md/.txt docs
  under `path` into the loaded KG as real evidence (optionally with embeddings). Emits `documents_ingested`.
- **extract_ontology(path, out_path=None, sim=0.86)** → `{status, out_path, version_hash, process_outcomes,
  provenance, validation, concept_hashes, ...}`. Knowledge Extraction Engine: LLM proposes ontology fragments from
  documents (graph-augmented context by default), reconciled (now with core content-hash exact dedup support),
  written as a **proposed** candidate and versioned for human review. Emits `ontology_proposed`. See `grap_mcp/kee/`.
- **list_proposed_ontologies(limit, cursor)** → paginated `{version_hash, ts}` entries (audit-backed) for review.
- **get_proposed_ontology(version_hash)** → raw + parsed proposal (incl. its concept_hashes).
- **adopt_proposed_ontology(version_hash, rationale)** → adopt after review (audited, rationale required).
  Verifies the proposal's `concept_hashes` (core sha256 content hashes captured at extraction) and
  **refuses adoption on mismatch** (tamper-evidence); core-verifies the projected nodes after write.

- **evaluate_proposal(version_hash)** → the adoption-policy verdict without adopting:
  integrity (concept_hashes) / validity / churn vs the active ontology (incl. PG-referenced
  removals) / provenance floor, plus the structural diff.
- **rollback_ontology(to_version_hash, reason)** → re-activate a previous ontology version
  (verified, audited as `ontology_rolled_back`). The compensating action every automated
  adoption relies on.
- **diff_ontologies(version_hash, against=None)** → what a version changes relative to
  `against` (default: the active ontology).

### Dark-factory mode
When the governed adoption policy is enabled (`set_policy("enabled", True, rationale)`),
`extract_ontology` runs the policy checks itself: pass → **auto-adopts** (audited with
`adopted_by: policy`); fail → audited `adoption_policy_rejected` that surfaces in
**list_pending_interventions()** — the exception queue (proposals awaiting review,
policy/integrity rejections, parked extraction items, and latest-state alarms: failed
maintenance, integrity mismatches, gap regressions). Empty queue = the factory needs
nobody. See `docs/dark-factory-proposals.md`.

- **run_pipeline(config_path)** → the whole line as one idempotent unit
  (kg → incremental ingest → extract+gate → gap run with `gap_delta` → maintenance),
  driven by a declarative JSON config; `run_factory.py --watch` wraps it in a
  change-detecting corpus watcher for schedulers.
- **get_factory_status()** → policy state, queue depth, gap trend (+ spike alarm),
  `healthy` verdict; writes `<ws_root>/factory_pulse.json` for dashboards.
- **run_maintenance()** (admin) → compact + reindex + retention + integrity sweep in one
  audited call (`maintenance_run` ok/failed).
- **verify_graph(sample)** → core content-hash sweep over the current KG
  (`graph_verified checked=N;mismatch=M;unknown=U`).
- **Canary** (`set_policy("canary_enabled", True, ...)`): every auto-adoption is
  gap-checked as a quarantined experiment against the last production run; more than
  `canary_max_new_gaps` new gaps → automatic `rollback_ontology` + `canary_failed`
  alarm (clears on the next passing canary). Bootstrap adoptions skip the canary.
- **extract_ontology_nversion(path, models=[a, b])** → run the KEE with two local
  models sequentially, auto-accept only the **agreement set** (normal policy gate +
  canary apply), publish the disagreement residue as a `disputed` proposal
  (queue kind `model_disagreement`, clears when a later run agrees fully). Also
  available in `run_pipeline` via `extract.models`.

(`mcp-server/demo_kee.py` runs the whole extraction + review flow offline; see `KEE_WORKFLOW.md`
for the full four-graph improvement loop and local-LLM setup.)
- **load_ontology(path)** → `{status, version_hash, outcomes_projected, characteristics_projected}`.
  Parse an ASPICE ontology YAML, project it into the KG, and version it. Emits `ontology_loaded`.

## Procedure Graph Lifecycle
- **load_procedure_graph(path)** → `{status, name, version, step_count, projection}`. Emits `pg_loaded`.
- **draft_pg_patch(model=None)** → LLM-drafts an updated PG covering the active ontology's
  coverage delta, then machine-checks it (deterministic coverage rules + publish validation)
  and publishes it at level=draft with the validation report recorded. Waits in the queue
  (kind `pg_draft_pending`) for a one-click `promote_pg`; rejected drafts raise a
  self-clearing `pg_draft_failed` alarm. Auto-triggers after kept adoptions when
  `set_policy("pg_draft_enabled", True, ...)`. With `pg_autopromote_enabled`, **trivial
  (purely additive) drafts self-promote** through the same validation-gated path; anything
  touching existing process logic stays a human decision. `run_pipeline` accepts `pg_path`
  so the whole loop runs lights-out.
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
- **get_config()** → `{status, weights, retention, role, last_changed}`.
- **set_policy(key, value, rationale)** (admin) → set an adoption-policy field
  (`enabled`, `max_outcomes_added`, `max_outcomes_removed`, `max_characteristics_removed`,
  `min_source_chunks`); audited as `config_changed`.
- **set_weight(name, value, rationale)** → set a prioritization weight (rationale required; audited).
- **set_retention(key, days, rationale)** → set a retention-policy value in days (rationale required;
  audited).
- **enforce_retention()** (admin) → hard-deletes runs older than the configured windows
  (`production_days`/`experiment_days`) + their orphaned explanation records; audited.

## Roles & Access
- **get_role()** → current role + available roles (`viewer` < `operator` < `admin`).
- **set_role(role, rationale)** → set the current role (admin + rationale required; persisted + audited).

> Mutating capabilities require **operator** (lifecycle/execution) or **admin**
> (governance + retention). Reads are open to **viewer**. The persisted default is
> `operator`; `GRAP_ROLE` overrides for the process only (never persisted) — that
> env var is also the admin bootstrap path (`GRAP_ROLE=admin`).

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
- **diff_runs(run_id_a, run_id_b)** → gap-target delta between two runs
  (`new_gaps` / `resolved_gaps` / `persistent_gaps`) — the triage primitive: only new or
  regressed gaps deserve attention.
- **list_pending_interventions()** → the exception queue (see Dark-factory mode above).
- **reconstruct_run(run_id)** → reproduction completeness report (names missing artifacts).
- **query_events(event_type=None, run_id=None)** → Temporal Graph events (filtered, or for a run).

> List-returning tools paginate (`limit`, `cursor` → `total`/`next_cursor`). JSON is the
> default wire format; tools with a compact schema (`graph_neighbors`, `list_runs`,
> `query_events`, `detect_gaps`, `get_audit_log`) accept `wire_format="compact"` for the
> token-efficient `#MUNCH` encoding.
