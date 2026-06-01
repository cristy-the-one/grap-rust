# PG Format Completion + Versioning/Lifecycle — Design Spec

> Covers **Task 05 (Human-Friendly PG Representation)** and **Task 01 (Versioning &
> Lifecycle Framework)**. Implement via `superpowers:writing-plans` then execution.
> Sequencing: **Task 05 first** (pure Python, no rebuild), **then Task 01** (Rust +
> pygrap bindings + MCP rewiring).

## Goal

Finish the human-friendly Procedure Graph (PG) format work and add a first-class,
content-addressed versioning + activation + lifecycle layer for PG and KG/ontology,
backed by a filesystem store, with a minimal append-only audit log.

## Architecture Decisions (approved during brainstorming)

1. **Implementation layer = Rust core.** Versioning, activation, lifecycle, and audit
   become native Rust types in the `grap` crate, exposed through pygrap. The Rust
   engine currently exposes only `save_to_dir` / `load_from_dir` with no version
   concept.
2. **Storage = filesystem store.** Content-addressed PG/ontology version blobs
   (hash-named) + JSON activation/lifecycle records under a workspace directory.
   Not modeled inside the grap KG.
3. **Audit = minimal append-only log.** JSONL event log now; satisfies Task 01's
   audit acceptance criteria and is forward-compatible with the full Task 03
   Temporal Graph.

### Python/Rust split

- **Python (`grap_mcp`)** — PG YAML parsing, projection into grap, validation, diff,
  and the MCP tool surface for those. This is format work; the yaml lib and MCP
  tools already live here.
- **Rust core (`grap` crate)** — versioning, activation, lifecycle state machine,
  audit log, change detection. Exposed via pygrap.
- **The seam:** when a PG is published, Python hands the raw YAML bytes to the Rust
  `Workspace`, which content-hashes and stores them.

## Repository Layout (confirmed)

Cargo workspace: root `grap` lib (`src/`), `crates/pygrap` (PyO3 bindings),
`spikes/on_disk_index`. PG parser at `mcp-server/grap_mcp/procedure_graph.py`;
MCP server at `mcp-server/grap_mcp/server.py`.

pygrap already exposes: `add_node`, `add_edge`, `get_node_text`, `get_node_label`,
`update_node`, `merge_nodes`, `neighbors_by_relation`, `predecessors_by_relation`,
`traverse_by_relation`, `search_and_traverse_hybrid`, `node_count`,
`save_to_dir`/`load_from_dir`, `with_merge_policy`.

---

## Part A — Task 05: PG Format Work (Python, `grap_mcp`)

All changes in `mcp-server/grap_mcp/`. No Rust rebuild required.

### A1. KG reference resolution in `project_to_grap`

In `procedure_graph.py::project_to_grap`, replace the `pass` placeholder (Step 3)
with real resolution:

- For each `kg:<id>` string in a step's `references` list:
  - Resolve against the loaded KG. Resolution strategy (best-effort, POC):
    1. Try exact node-label match `kg:<id>` or `<id>`.
    2. Fall back to a text search (`search_and_traverse_hybrid` / available search
       API) and take the top hit above a confidence floor.
  - On hit: create an edge `step_node → kg_node` with props
    `{ "type": "references", "ref": "<id>" }`, increment `references_resolved`.
  - On miss: append `<id>` to an `unresolved_references` list.
- Return value gains: `references_resolved: int`, `unresolved_references: List[str]`.

Non-fatal: unresolved references never raise; they are reported.

### A2. `validate_pg(pg, kg=None) -> dict`

New function in a new module `mcp-server/grap_mcp/validation.py`. Returns
`{ "ok": bool, "errors": [...], "warnings": [...] }`. Never raises on validation
failure (per Task 06 "inform, don't block").

Checks:

- **Structural (errors):**
  - Step `id`s are unique and non-empty.
  - Every data-flow input of the form `"<step-id>.outputs.<name>"` references an
    existing step `id` whose `outputs` declares `<name>`.
  - `operation` is present (warning if empty).
- **Cross-KG (warnings, only when `kg` is provided):**
  - Each `kg:` reference resolves (reuses the A1 resolution helper). Unresolved →
    warning with the reference id.

Each error/warning is a dict `{ "code": str, "message": str, "step_id": str|None }`.

### A3. `diff_pg(pg_a, pg_b) -> dict`

New function in `mcp-server/grap_mcp/diff.py`. Operates on two parsed
`ProcedureGraph` objects. Returns:

```json
{
  "steps_added": ["<step-id>", ...],
  "steps_removed": ["<step-id>", ...],
  "steps_changed": [
    { "step_id": "...", "fields_changed": ["operation", "inputs", ...] }
  ],
  "metadata_changed": { "version": ["0.1.0", "0.2.0"], ... }
}
```

Step matching is by `id`. A step is "changed" when any of
`operation/inputs/outputs/references/guards/notes` differ. `metadata_changed`
captures top-level `name/description/version` differences.

### A4. New MCP tools (`server.py`)

- `validate_procedure_graph(path: str) -> dict` — parse + `validate_pg` against the
  currently loaded KG (if any).
- `diff_procedure_graphs(path_a: str, path_b: str) -> dict` — parse both + `diff_pg`.

### A5. Refactor note

`procedure_graph.py` stays the parser/model. New `validation.py` and `diff.py`
keep concerns separated and files focused.

---

## Part B — Task 01: Rust Core `src/lifecycle/` Module

New module `src/lifecycle/` in the `grap` crate. New dependency: `sha2 = "0.10"`.
Timestamps via `std::time::SystemTime` → unix-millis (no new time dep).

### B1. Content addressing — `ContentHash`

- `ContentHash(String)` — lowercase hex SHA-256 of content bytes.
- `ContentHash::of(bytes: &[u8]) -> ContentHash`.
- `Display`/serde as the hex string.

### B2. Version model — `Version` + `VersionStore`

```rust
enum VersionKind { Pg, Ontology }

struct Version {
    hash: ContentHash,
    kind: VersionKind,
    created_at: u64,                 // unix millis
    metadata: BTreeMap<String, String>, // e.g. name, version-string
}
```

`VersionStore` (filesystem-backed at `<ws>/versions/`):

- blobs: `<ws>/versions/objects/<hash>` (raw content bytes)
- meta:  `<ws>/versions/meta/<hash>.json` (serialized `Version`)
- `put(kind, content: &[u8], metadata) -> Version` — hash; if blob absent, write
  blob + meta; idempotent (content-addressed dedupe).
- `get(hash) -> Option<(Version, Vec<u8>)>`
- `exists(hash) -> bool`
- `list(kind) -> Vec<Version>`

Versions are immutable.

### B3. Publish state machine

```rust
enum PublishLevel { Draft, Promoted }

struct PublishRecord {
    hash: ContentHash,
    level: PublishLevel,
    rationale: Option<String>, // required for Promoted
    ts: u64,
}
```

- Stored at `<ws>/published/<hash>.json`.
- `publish(hash, Draft, None)` — low-friction.
- `promote(hash, rationale)` — requires non-empty rationale; errors otherwise.
- Only a `Promoted` version may be activated (enforced by `Workspace::activate`).

### B4. Activation model — `Activation` + `ActivationStore`

```rust
enum ActivationState { Created, Active, Superseded }

struct Activation {
    activation_id: String,
    pg_logic_id: String,            // stable PG name → identity for "one production per logic"
    pg_version: ContentHash,
    frozen_weights: BTreeMap<String, f64>,
    kg_version: Option<ContentHash>, // ontology snapshot reference (lighter, POC-minimal)
    created_at: u64,
    state: ActivationState,
    is_production: bool,
}
```

`ActivationStore` (filesystem at `<ws>/activations/`):

- records: `<ws>/activations/<activation_id>.json`
- production pointer per logic: `<ws>/production/<pg_logic_id>.json` → `{ activation_id }`
- `create(...)`, `get(id)`, `list()`, `list_for_logic(pg_logic_id)`
- `set_production(activation_id)` — clears prior production flag for that
  `pg_logic_id`, marks the previous one `Superseded`, sets the new one
  `is_production = true` / `Active`.
- `production_for(pg_logic_id) -> Option<Activation>`

### B5. Audit log — `AuditLog`

Append-only JSONL at `<ws>/audit/events.jsonl`.

```rust
struct AuditEvent {
    ts: u64,
    event_type: String, // version_published | version_promoted |
                        // activation_created | production_flag_moved
    actor: Option<String>,
    subject: String,    // hash or activation_id
    details: BTreeMap<String, String>,
    rationale: Option<String>,
}
```

- `append(event)` — opens in append mode, writes one JSON line, flushes.
- `read_all() -> Vec<AuditEvent>` (POC convenience; full query is Task 03).

Every mutating `Workspace` operation appends an event.

### B6. Change detection / last-known-good

- `Workspace::detect_change(pg_logic_id, current_bytes) -> ChangeStatus` where
  `ChangeStatus { changed: bool, current_hash, production_hash: Option<ContentHash> }`.
  `changed = current_hash != production_hash`.
- `Workspace::last_known_good(pg_logic_id) -> Option<Activation>` = the current
  production activation. (Automatic runtime fallback behavior is deferred to
  Task 03/07; this provides the primitive.)

### B7. `Workspace` (facade)

Ties `VersionStore` + publish records + `ActivationStore` + `AuditLog` to a root
directory.

```rust
struct Workspace { root: PathBuf, /* sub-stores */ }

impl Workspace {
    fn open(root: impl AsRef<Path>) -> Result<Self, LifecycleError>; // create dirs if absent
    fn publish_pg(&self, content: &[u8], level: PublishLevel,
                  rationale: Option<String>, metadata: BTreeMap<String,String>)
                  -> Result<Version, LifecycleError>;
    fn promote(&self, hash: &ContentHash, rationale: String) -> Result<(), LifecycleError>;
    fn activate(&self, activation_id: String, pg_logic_id: String,
                pg_version: ContentHash, weights: BTreeMap<String,f64>,
                kg_version: Option<ContentHash>) -> Result<Activation, LifecycleError>;
    fn set_production(&self, activation_id: &str) -> Result<Activation, LifecycleError>;
    fn current_production(&self, pg_logic_id: &str) -> Option<Activation>;
    fn detect_change(&self, pg_logic_id: &str, current: &[u8]) -> ChangeStatus;
    fn last_known_good(&self, pg_logic_id: &str) -> Option<Activation>;
}
```

`activate` errors if `pg_version` is not a `Promoted` published version.

`LifecycleError` (via `thiserror`, already a dep): `NotFound`, `NotPromoted`,
`MissingRationale`, `Io`, `Serde`.

### B8. Workspace directory layout

```
<workspace>/
  versions/
    objects/<hash>          # raw content bytes (PG yaml, ontology snapshot)
    meta/<hash>.json        # Version
  published/<hash>.json     # PublishRecord
  activations/<id>.json     # Activation
  production/<pg_logic_id>.json   # { activation_id }
  audit/events.jsonl        # AuditEvent stream
```

### B9. KG/ontology version (minimal)

Ontology versioning is "lighter" (Task 01 decision B). For the POC, an ontology
snapshot reference = a `ContentHash` of a small manifest (e.g. KG path +
node_count + save snapshot id) stored as a `VersionKind::Ontology` version. Full
ontology versioning belongs to Task 04. `kg_version` on an activation is optional.

---

## Part C — pygrap Bindings + MCP Rewiring

### C1. pygrap (`crates/pygrap/src/lifecycle.rs`)

PyO3 classes mirroring the Rust API:

- `Workspace` — `open(root)`, `publish_pg(content: bytes|str, level: str,
  rationale=None, metadata=None)`, `promote(hash, rationale)`,
  `activate(activation_id, pg_logic_id, pg_version, weights=None, kg_version=None)`,
  `set_production(activation_id)`, `current_production(pg_logic_id)`,
  `detect_change(pg_logic_id, current)`, `list_versions(kind=None)`,
  `read_audit()`.
- `Version`, `Activation`, `AuditEvent` — `#[pyclass(get_all)]` data classes with
  `__repr__`.
- Errors mapped to a `pygrap.LifecycleError` (alongside existing `GraphError`).
- Registered in `crates/pygrap/src/lib.rs`.

### C2. MCP server (`server.py`)

- On startup, open a `Workspace` at a configured dir (e.g. env `GRAP_WORKSPACE`
  or default `./grap-workspace`). Store on `_state["ws"]`.
- `load_procedure_graph`: after parse, `ws.publish_pg(raw_yaml_bytes,
  level="draft", metadata={name, version})`; return the resulting version hash.
- Rewire `activate` / `set_production` to call the Rust-backed `Workspace`
  (replacing the in-memory `Activation` class + dict). `pg_logic_id` = `pg.name`.
- New tools: `publish_pg(level, rationale)`, `promote_pg(version_hash, rationale)`,
  `get_production()`, `list_versions()`, `get_audit_log()`.
- **Fix the duplicate `get_current_activation` definition** (currently defined
  twice; the second silently overrides the first).
- `get_status` updated to read from the `Workspace`.

---

## Testing Strategy

- **Rust (`cargo test`)** — unit tests in `src/lifecycle/`:
  - `ContentHash::of` stable + deterministic.
  - `VersionStore` put/get/dedupe/list round-trips on a `tempfile` dir.
  - Publish state machine: draft→promote requires rationale; activate rejects
    non-promoted.
  - `ActivationStore` set_production moves the flag, supersedes the previous,
    enforces one production per `pg_logic_id`.
  - `AuditLog` append/read round-trip; every mutating op emits an event.
  - `detect_change` true/false against production hash.
- **pygrap** — `maturin develop` then a smoke script exercising
  publish→promote→activate→set_production→read_audit.
- **Python (pytest)** — `validate_pg` (structural + cross-KG), `diff_pg`,
  KG-ref resolution in `project_to_grap`.

## Dependencies / Build Notes

- Adds `sha2 = "0.10"` to root `Cargo.toml`.
- Task 01 requires `maturin develop` to rebuild pygrap. `cargo test` runs without
  it. The full Python integration smoke test requires the rebuilt wheel.

## Out of Scope (explicit)

- Full Temporal Graph query/subsystem (Task 03) — only the append-only log here.
- Ontology modeling + reasoning (Task 04).
- Publish-time validation engine beyond format-level checks (Task 06).
- Experiment quarantine (Task 07), config-governance MCP surface beyond frozen
  weights (Task 08), run-record/reconstruction (Task 09).
- Modular PG composition / includes (deferred per Task 05 decision).
- Role-based authorization on MCP tools (Task 02).

## Acceptance Criteria

**Task 05:**
- `project_to_grap` resolves `kg:` references into `references` edges and reports
  unresolved ones.
- `validate_procedure_graph` returns structured errors/warnings (structural +
  cross-KG) and never blocks.
- `diff_procedure_graphs` returns added/removed/changed steps + metadata changes.

**Task 01:**
- A PG can be published as Draft, promoted (with rationale), and activated with a
  specific set of frozen weights + an optional KG/ontology version reference.
- The system reports the current production activation and its frozen config.
- Multiple activations of the same `pg_logic_id` can coexist; exactly one is
  production.
- Moving the production flag appends an auditable event to the JSONL log.
- `detect_change` identifies when current PG bytes differ from the last
  production activation's frozen version.
