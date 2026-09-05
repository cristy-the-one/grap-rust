# Architecture value audit — September 2026

Audited revision: `a87e7ac`. Scope: decisions added mainly in June–July 2026,
checked against their history, current implementation, application callers,
tests, and recorded pilot findings. This is an assessment, not a runtime change.

## Judgment

**The project has accumulated both useful engineering and unjustified product
complexity. Keep the reliability work; reduce the workflow platform around it.**

The strongest later work makes a real operation more trustworthy or cheaper:
durable extraction progress, bounded retries, embedding batches, provenance,
and keeping ontology scaffolding out of evidence retrieval. The weakest work
adds lifecycle states, tools, and automated transitions before the operation
being governed has the semantics its name promises.

The biggest problem is not the number of files. It is that callers must understand
many concepts while some important guarantees still stop between modules.
"Graph-native procedure execution" currently means a gap-check call scoped by
references. "Reconstruction complete" means referenced artifacts exist, not that
the evidence and execution can be reproduced. More machinery around those
contracts does not make them stronger.

No conclusion here depends on which assistant wrote a change. The recent engine
pass is subject to the same test: its maps cost memory, and its corrected
traversal still has some workload-specific overhead. Its justification is
reproduced wrong answers and measured performance, not architectural vocabulary.

## Decision ledger

| Decision | Judgment | Action |
| --- | --- | --- |
| Checkpointing, bounded retries, batch embeddings | Strong value | Keep as standard extraction behavior |
| SQLite extraction source of truth with derived graph/search views | Valuable, expensive implementation | Keep ownership model; stop rebuilding every view on every save/open |
| Separate ontology and evidence retrieval | Strong value | Keep; finish separating PG scaffolding too |
| Content-addressed versions, explanations, audit records | Strong value, narrower than advertised | Keep; define auditability separately from reproducible replay |
| Procedure graph projection + execution/lifecycle claims | Not yet earning its complexity | Treat current PG as a scope/configuration document until operations execute |
| LLM-generated PG patches and automatic promotion | Premature for current semantics | Generate supported reference edits deterministically; freeze further expansion |
| Policy gate, rollback, canary | Useful operational controls | Keep opt-in; evaluate against fixed, independent evidence |
| Two-model agreement extraction | Implemented experiment, value not established | Keep off the normal path until quality/cost ablation justifies it |
| RRF fusion | Sound local abstraction | Keep |
| Default PageRank channel | Unjustified default in inspected evidence | Make opt-in; require relevance and cost measurements |
| Tool tiers, pagination, compact encoding | Useful small helpers | Keep, but advertise a smaller task-focused default surface |
| Global MCP module serving as application layer | Poor seam | Put application state and operations behind one reusable service interface |
| Local roles | Useful mode guard, not identity/security | Keep small and label honestly |
| “Temporal Graph” as a separate conceptual subsystem | More ambitious name than implementation | Keep the linked event log; defer a dedicated graph model |

## 1. Procedure machinery is ahead of procedure execution

[`server.py`](../mcp-server/grap_mcp/server.py), `run_procedure` (line 2425),
never dispatches step operations, evaluates guards, or propagates step outputs.
It counts steps and calls `reasoner.detect_gaps`. In
[`reasoner.py`](../mcp-server/grap_mcp/reasoner.py), `scoped_outcomes` uses the
PG's references to choose outcomes. That is useful scope selection, but it is
not a procedure interpreter.

**Reproduced:** loading a PG containing `operation_that_does_not_exist` and a
`false` guard, then calling `run_procedure`, returned `status: ok`, "Procedure
executed", and eight gaps. The operation and guard did not affect execution.

There are also two distinct tracked-run implementations. `run_procedure` calls
the reasoner without weights and records no explanation hashes;
`_run_gap_detection` (line 2484) passes configured/frozen weights and persists
explanations. A probe configured `lexical_threshold=0.99`: `run_procedure`
passed no weights, while `detect_gaps` passed the configured value. Thus a
shared-looking lifecycle currently permits different behavior through adjacent
tools. These are concrete costs of the current seam.

This matters especially for the July PG-drafting/autopromotion additions.
[`pg_patch.py`](../mcp-server/grap_mcp/pg_patch.py) asks an LLM to add one step
per uncovered outcome, remove obsolete references, retain valid references,
and bump a version. Its supported correctness gate is largely set membership.
Those edits can be generated deterministically while preserving the surrounding
document. The model introduces latency, malformed-output handling, retries,
and a review queue without supplying necessary reasoning for that restricted job.

**Decision:** consolidate execution through the tracked gap-check path. Describe
the current PG honestly as a versioned selection/configuration document. Generate
simple coverage-reference patches directly. Only build a procedure runtime when
a real use case requires supported operations, data flow, guards, and per-step
results; reject unsupported behavior then. Do not add more promotion machinery
before that choice is made.

## 2. Versioning is valuable; full reproducibility is not implemented

June's version/run/explanation stores capture useful historical artifacts.
Deleting them would force callers to reinvent version identities, audit links,
and explanation storage. They pass the deletion test.

But `Activation.kg_version` permits a KG **or ontology** reference
([`activation.rs`](../src/lifecycle/activation.rs), line 23). Both run paths in
`server.py` commonly fill it with the ontology version and then query the
currently loaded mutable evidence graph and PG object.

`Workspace::reconstruct` ([`workspace.rs`](../src/lifecycle/workspace.rs), line
392) checks whether referenced version/explanation blobs exist. It does not
restore evidence, rerun the computation, or establish that current computation
used the recorded versions. An absent PG/KG reference is treated as present for
this completeness calculation.

**Reproduced:** adding evidence between two gap runs left their `kg_version`
identical; it was the ontology hash. Reconstruction of the earlier run still
reported `complete: true` after the corpus changed. This is consistent with an
artifact-availability check, but not with an evidence-snapshot guarantee.

**Decision:** preserve the stores. Separate `ontology_version` from
`evidence_snapshot` and distinguish "artifacts available" from "replayable".
An eventual replay contract needs immutable input content or retained references
that can recover it, plus executed PG content, effective weights, and relevant
engine/tokenizer/model settings. A hash alone cannot recover overwritten data.
That is targeted completion of a valuable feature, not a reason to build a
larger temporal database.

## 3. The three-graph slogan encouraged harmful mixing

The original "one engine, three meanings" framing is a useful vocabulary for
knowledge, instructions, and history. It does not require all three to share a
search population or even the same physical representation.

The July separation of ontology scaffolding from evidence was an excellent
correction. `_project_ontology` (`server.py`, line 204) now creates a separate
graph. Its reference resolver and projection verification are real consumers;
this is not an unused duplicate introduced only for appearance.

The [recorded pilot investigation](canary-noise-floor-investigation.md) reports
a canary noise floor falling from roughly 842–889 new gaps per batch to 0–5
after the separation. Those are historical field measurements, not numbers
reproduced in this audit. The mechanism is supported by the code: filtering
scaffolding after top-k retrieval cannot undo its ranking and document-frequency
effects. PG steps still enter the evidence graph in `load_procedure_graph`
(`server.py`, line 2389), leaving a smaller version of the same conceptual issue.

Meanwhile the Temporal Graph is implemented by an append-only JSONL log with
typed links and filtered scans, not by Rust graph traversal
([`audit.rs`](../src/lifecycle/audit.rs), lines 65–139). **That simpler
implementation is appropriate.** The misleading part is presenting it as
evidence that everything is powered by one graph model.

**Decision:** keep evidence separate from metadata and generated requirements;
move PG scaffolding out of its ranking population too. Keep a linked event log
until actual graph-shaped historical queries justify more. One engine may serve
several views without becoming the authoritative store for every domain fact.

## 4. The extraction source of truth is sensible; full reprojection is not free

June 11 added [`GraphStore`](../mcp-server/grap_mcp/kee/graph_store.py): a
transactional SQLite store of concepts, aliases, characteristics, relations,
provenance, chunk manifests, and failures. The working Rust graph and entity
index are derived retrieval views. This is a useful separation because the
extraction state needs richer bookkeeping than the graph currently models.

However, [`WorkingGraph`](../mcp-server/grap_mcp/kee/working_graph.py) maintains
the same information in several in-memory maps as well as these stores.
`save` (line 851) writes all SQLite state, calls `_load_state`, and rebuilds the
graph/index. `_project` (line 818) clears and recreates the graph and entity
index; concept ids are compacted/remapped. Opening persisted state also rebuilds
the views. `GraphStore.save` clears and rewrites its tables in a transaction.

Saving truth before projection is a good recent correction: a projection or
embedding failure should not discard extracted work. But rebuilding everything
makes checkpoint cost depend on accumulated state, even if one chunk changed.
It also makes concept identity depend on a representation detail.

**Decision:** keep SQLite as the explicit extraction authority and keep Rust as
the retrieval engine. Give concepts stable domain ids independent of arena ids.
Persist only changed extraction records; update or lazily rebuild derived views
by generation. Preserve the durable checkpoint before any optional embedding or
projection work. Do not move all provenance tables into Rust merely to preserve
the "graph-native" story.

## 5. Reliability features earned their place; automation is not quality proof

The checkpoint loop and dead-letter handling in
[`graph_extract.py`](../mcp-server/grap_mcp/kee/graph_extract.py), around lines
202–247, directly address lost long-running work and repeatedly failing chunks.
Provider/embedder injection also has real alternatives and enables offline
tests. These are deep modules worth keeping. The
[lessons](dark-factory-lessons.md) record concrete failures and recovered costs;
they should not be discarded just because the surrounding "dark factory"
branding is ambitious.

Policy gates, rollback, and canaries similarly have legitimate operational
jobs: cap unattended changes, expose rejected changes, and detect retrieval
regressions. They do **not** establish that an extracted claim is true.

The working graph exports observed concepts into `process_outcomes` and turns
extracted characteristics into `required_characteristics`. The reasoner then
checks their coverage against evidence from the corpus. This can measure
self-coverage and drift. It does not, by itself, establish independently correct
requirements or domain correctness. The binding also has no
`has_characteristic` method, so the reasoner's optional structural-evidence hook
is inactive on the actual default Rust binding; the gap decision relies on its
lexical evidence path.

The name-hash gate deserves similarly precise language.
`verify_concept_hashes` in [`governance.py`](../mcp-server/grap_mcp/governance.py)
verifies SHA-256 of each outcome name. **Reproduced:** replacing all required
characteristics while retaining the name passed that check. It is name
consistency, not extraction correctness or integrity of the complete claim.
Content-addressed proposal versions serve a different, useful purpose.

**Decision:** retain opt-in operational automation, but keep observed claims,
candidate taxonomy, and approved normative requirements conceptually distinct.
Use a small independently judged evaluation set for quality. Do not equate an
empty intervention queue, matching hashes, model agreement, or fewer gaps with
a validated knowledge model.

## 6. The framework is growing around a weak application seam

Current source inventory: `server.py` is 3,264 lines with **71 `@tiered` tools**
and 116 functions/methods. The older architecture document says 33 tools.
Neither count alone proves overengineering. The relevant evidence is that this
one module owns transport registration, mutable workspace/graph state, pipeline
execution, adoption, canaries, projection, maintenance, and exception aggregation.

The GUI's [`bridge.py`](../aspice-gui/backend/bridge.py), lines 40–64, imports
that transport module, manually resets selected `_state` fields, and dispatches
by `getattr`. The state class still supports dict-style compatibility and lives
as one global instance (`server.py`, lines 74–183). Wrapping a global dictionary
in a class did not create independent session ownership. New fields such as
`ontology_kg` and `extraction_workdir` are not in the GUI's manual reset list.

**Decision:** introduce one instance-owned application service used by MCP, GUI,
and batch entry points. Put state ownership, tracked execution, and mutation
invariants there. Keep policy calculations pure. This should replace duplicate
orchestration, not add a service class that merely forwards to the global module.
Do not split 71 tools into 71 classes or create a plugin system to organize them.

The small tier/pagination/encoding helpers are mostly worth their cost. But
tiers default to `full`, new tools implicitly become `standard`, and the core
tier is not a complete audit workflow. Use task-oriented startup profiles with
compatibility aliases for current tool names. Pagination currently slices an
already materialized list, so it saves response size rather than backend work.
Make no scalability claim beyond that.

## 7. Specific additions to keep experimental

- **N-version extraction (July 20):** it genuinely runs two extraction passes,
  matches outcome names/embeddings, intersects characteristics, and exposes
  disputes. I found implementation tests, not an independent precision/recall
  and runtime ablation establishing that this deserves the routine cost.
  Agreement may reduce noise, but shared omissions and correlated errors remain.
  Keep it as an explicit experiment, not a default requirement for trust.
- **PageRank default (June 8):** RRF addresses incomparable score scales with a
  compact, useful primitive. Default global centrality is a separate decision.
  `HybridWeights::default` sets `centrality=0.4`; `pagerank_cached` still clones
  a graph-sized vector on cache hits and recomputes after topology changes.
  Inspected tests demonstrate mechanics, not task relevance. Prefer centrality
  off until an ablation shows it helps the intended retrieval task.
- **Distillation research:** keep its experimental status. A journal that
  records refuted hypotheses is valuable evidence; it is not justification to
  install a permanent reasoning stage before one succeeds on the intended task.
- **Sharding/HNSW:** do not incorrectly blame these on the latest work. Sharding
  dates to May 20. No sharded graph use appeared in the inspected current MCP,
  GUI, or extraction paths, and the binding does not enable `ann-hnsw`.
  Preserve compatibility, stop expanding these features without a measured need.
- **Roles:** the tiny module explicitly calls itself a POC simulation with no
  identity backend. It is a reasonable local mode guard. Keep that description;
  avoid treating local workspace role selection as multi-user authorization.

## Simplification order

1. **Unify the two run paths and correct their names/contracts.** The next unit
   of work should eliminate the weights/explanation divergence, report
   unsupported procedure execution honestly, and distinguish artifact checks
   from replay guarantees.
2. **Remove unnecessary work.** Generate the currently supported PG reference
   edits without an LLM; stop injecting PG metadata into evidence retrieval;
   keep default ranking channels only when justified by task evaluation.
3. **Establish one application-state owner.** Replace GUI manipulation of server
   globals with the same application interface used by MCP and batch work.
4. **Make persistence incremental behind existing behavior.** Stable concept ids,
   one authoritative extraction store, and rebuildable retrieval views; no new
   database family or distributed scheduler.
5. **Re-earn optional complexity.** Compare single-model and two-model extraction,
   centrality on/off, and workflow variants against independently labeled tasks
   with quality, runtime, memory, and human-intervention measurements.

The smaller target is: **Rust retrieval engine + durable extraction records + a
single application service + optional policy-controlled workflows + thin user
interfaces.** The existing audit/version primitives can support it. Most value
comes from clarifying ownership and deleting redundant work, not from another
architectural layer.

## Evidence and limits

- History inspection distinguishes May core work, June lifecycle/graph additions,
  and July automation/reliability work. Sources above refer to `a87e7ac`.
- AST inspection counted tools and module structure. Call-site inspection checked
  the current application paths; it does not prove there are no external users.
- The isolated [contract probe](experiments/architecture-contract-probe.py) ran
  against the rebuilt binding from the preceding engine pass, with only temporary
  local state and no LLM/network calls. It reproduced the execution, weight,
  snapshot, hash-scope, and structural-hook findings described above.
- The earlier engine pass's 349 passing Python/MCP tests do not contradict these
  findings: the missing architectural guarantees were not established by those
  tests. This audit did not rerun the large corpus or claim new quality results.
- No production graph, policy, runtime implementation, or existing user file was
  changed by this investigation.
