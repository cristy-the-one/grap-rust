# Simplification implementation

Implements the ordered decisions in [the architecture audit](architecture-value-audit-2026-09.md).

## Run contracts and unnecessary work

- `run_procedure` is a compatibility name for the same tracked gap check as
  `detect_gaps`. Both use frozen activation weights and PG content, persist
  explanations, and report that procedure operations were not executed.
- Run metadata identifies the actual ontology and explicitly records that no
  evidence snapshot was captured. `reconstruct_run.artifacts_available` checks
  referenced files; `replayable` is false. Legacy `complete` retains its artifact
  availability meaning.
- PG loading no longer inserts procedure steps into evidence retrieval.
- Reference patches are deterministic, preserve existing step semantics, and
  retain coverage validation, draft publication and promotion policy. The old
  `model` argument is accepted but ignored. Removed references require review
  under the existing nontrivial-diff policy.
- Default Rust hybrid ranking disables PageRank. Explicit centrality weights
  remain supported; no retrieval-quality improvement is claimed without an
  independent task evaluation.

Validation: existing Python suite 349 passed; two additional run/patch contract
regressions passed. Rust default-ranking regression passed.

## Application ownership

`GrapApplication(workspace, ...)` now owns the application operations, graph
state, workspace selection, provider factories and session history. MCP registers
bound operations; the GUI and batch runners instantiate the same application
without importing the MCP transport. Workspace selection creates a fresh instance
and does not change process environment variables or reset selected global fields.
Old MCP tool names remain callable for compatibility.

The service deliberately remains one cohesive module. This change fixes ownership
and removes transport coupling; it does not claim that moving code reduces its
intrinsic complexity. Independent application instances do not coordinate writes
to the same graph directory.

Validation: 352 existing/application tests passed, then the corrected two-instance
isolation regression passed separately; application import is checked to avoid
loading MCP. GUI integration suite: 18 passed.
