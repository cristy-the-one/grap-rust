# Dark-Factory Proposals for the Knowledge Construction Engine

Goal: evolve the KEE loop from *"a human reviews everything"* to *"machines run
the factory; humans handle a short exception queue"* — intervention that is
**minimal and rare by design**, not by neglect.

> **Status: Phases 1–3 implemented.** Phase 1: P-1 policy gate + auto-adoption,
> P-2 `rollback_ontology`, `diff_ontologies`, `diff_runs`, P-6
> `list_pending_interventions` (policy governed via `set_policy`). Phase 2:
> P-8 `run_pipeline` + `run_factory.py --watch` (P-3 corpus watcher), P-7
> `run_maintenance` + `verify_graph`, P-9 `get_factory_status` with gap-trend
> alarms + `factory_pulse.json`. Phase 3: P-4 canary auto-promotion
> (`canary_enabled`/`canary_max_new_gaps`, quarantined experiment + automatic
> rollback, `canary_result` events) and P-5 n-version extraction
> (`extract_ontology_nversion`, `governance.agreement_merge`, disputed
> proposals + `model_disagreement` alarm). See `mcp-server/KEE_WORKFLOW.md`.
> Phase 4 (P-10 LLM-drafted PG patches) remains a proposal.

The governing idea is **management by exception**. The current philosophy ("the
LLM proposes, a human adopts") stays intact — but "a human adopts" is replaced
by "a *versioned policy* adopts, and a human is summoned only when the policy
cannot decide." The human acts move up one level: from reviewing every proposal
to reviewing the *rules* that review proposals. That is what makes intervention
rare *by design*: the routine verdicts are machine-checkable, and the humans own
only the residue.

Everything below is grounded in what the repo already has (tool names in
`mcp-server/CAPABILITIES.md`, loop in `KEE_WORKFLOW.md`). Effort labels:
S ≈ hours, M ≈ 1–3 days, L ≈ a week+.

---

## 1. Where humans are needed today

| Touchpoint | Frequency today | Dark-factory target |
|---|---|---|
| Review + adopt every ontology proposal | every extraction | policy gate auto-adopts; human sees only policy failures |
| Trigger ingest / re-extract when docs change | every corpus change | corpus watcher, nobody triggers anything |
| Read gap reports | every run | delta-triage; only *new or regressed* gaps escalate |
| Promote/activate PG versions | every change | canary auto-promotion inside the existing quarantine |
| Maintenance (compact, reindex, retention, integrity) | ad hoc / never | scheduled autopilot |
| Weights, retention, roles, policy changes | rare | **stays human forever** — this is the designed-in rare intervention |

---

## 2. The proposals

### P-1. Policy-gated auto-adoption (the keystone) — M

Add `adoption_policy` as governed, versioned config (same store as weights,
admin + rationale to change), and a tool `evaluate_proposal(version_hash)` that
returns a machine verdict. A proposal **auto-adopts** iff *all* checks pass:

1. **Integrity** — `concept_hashes` verify (already built, adoption gate).
2. **Validity** — `validate_ontology` has no errors (already built).
3. **Churn budget** — diff against the active ontology stays inside limits:
   e.g. ≤ N outcomes added, **zero** outcomes/characteristics removed that an
   active PG references. Needs a new `diff_ontologies(hash_a, hash_b)` tool
   (S; the PG diff machinery in `diff.py` is the template).
4. **Provenance floor** — every outcome backed by ≥ k source chunks
   (`provenance` is already in every proposal; today nothing enforces it).
5. **Stability** — extract twice, same content hash (the pipeline is
   deterministic given the same LLM output; instability = model noise →
   escalate). Cheap because proposals are content-addressed already.

Verdict + evidence are audited (`adopted_by: policy@<version>`), so the trace
graph shows *why* the factory acted, exactly as it does for humans today.
Anything failing any check lands in the exception queue (P-6) — it does not
block the line; the previous ontology stays active.

**This single proposal removes the highest-frequency human act in the loop.**

### P-2. Rollback as a first-class tool — S *(prerequisite for everything else)*

Adoption is currently one-way; a dark factory must be able to reverse itself
faster than a human can be paged. Add `rollback_ontology(to_version_hash,
reason)` — re-project a previously adopted version, audited as an automatic
compensating action. Every automated promotion in P-1/P-4 assumes this exists.
Without rollback, auto-adoption is reckless; with it, a wrong auto-adoption
costs one audited revert instead of an incident.

### P-3. Corpus watcher — continuous, unattended intake — M

The manifest-based incremental machinery already exists
(`incremental.ingest_incremental`, `extend_extraction_graph` syncs
new/changed/removed). What's missing is the trigger. Add a
`watch_corpus(paths, interval)` runner (or an OS scheduler entry invoking a new
`run_pipeline` CLI — see P-8) so that: docs change → reingest + extend →
extract → policy gate → (auto-adopt | exception). Nobody "runs KEE" anymore;
the corpus drives the factory.

### P-4. Canary promotion through the existing quarantine — M

The quarantine machinery (`run_experiment`, quality metrics excluding
experiments, `adopt_experiment` through the validation-gated path) is already
the right shape — it is just human-driven. Automate it: every auto-adopted
ontology first runs gap detection **as an experiment** against the production
baseline; auto-promote iff the delta stays inside budget (gap churn ≤ X%, no
gap appearing on an outcome that was previously clean), else auto-rollback
(P-2) + exception. Needs `diff_runs(run_a, run_b)` (S) — today runs can only be
eyeballed one at a time.

### P-5. N-version extraction — shrink the review residue — M/L

The working graph already *parks* ungroundable relations instead of asking a
human. Generalize that pattern to concepts: run extraction with **two local
models** (e.g. Ternary-Bonsai-27B + a Qwen via Ollama — both are one env var
away) and auto-accept only the agreement set (matched by content hash /
embedding similarity, machinery that exists in `reconcile`/`working_graph`).
Disagreements are parked, not adopted and not blocking. Humans review the
*disagreement residue* — typically a small fraction of the proposal — instead
of the whole ontology. Cost is one extra extraction pass; both models are free
and local.

### P-6. The exception queue — the only place humans look — M

If humans are rare, the factory needs exactly one surface that answers "does
anything need me?". Add `list_pending_interventions()` aggregating, with age:
policy-gate failures (P-1), parked concepts/relations (P-5), canary failures
(P-4), promote refusals, integrity mismatches (`verify_node`,
`reconstruct_run` gaps), maintenance errors. Everything already emits audit
events — this is a view over the trace graph, not new state. The operational
contract becomes: **empty queue = the factory needs nobody.** A pulse file
(`_grap_pulse.json` is the precedent) makes it pollable by dashboards or a
phone notification without opening anything.

### P-7. Maintenance autopilot + integrity sweeps — S

`compact_graph`, `reindex_entities`, `enforce_retention`, plus a new
`verify_graph(sample=N)` (loop `verify_node` over a sample; the primitive is
built) and a `reconstruct_run` spot-check — bundled into one idempotent
`run_maintenance()` invoked from a scheduler. Results audited; failures go to
the exception queue. Today all of this is manual admin work that in practice
never happens — the definition of a latent failure.

### P-8. `run_pipeline` — the factory as one declarative, resumable unit — M

One CLI/tool that executes the whole line from a versioned config file
(pipeline-as-code, published to the workspace like weights):
`watch → ingest → extract → gate → canary → promote → maintain`. Idempotent and
resumable (every stage is already content-addressed, so re-running skips
completed work). This is what an OS scheduler, CI, or a cron'd agent invokes
headlessly. It also makes the factory *testable*: the offline stub demo becomes
a pipeline integration test.

### P-9. Drift alarms instead of eyeballs — S/M

Extend the existing telemetry/quality-metrics into a small time series per run:
gap count, gap churn, extraction stability (P-1.5), dedup rate, parked ratio,
proposal size. Alarms fire only on threshold breaches or trend breaks, feeding
the exception queue. Humans stop *reading* reports; they get *interrupted* by
anomalies — the correct direction of information flow for a dark factory.

### P-10. LLM-drafted PG patches (the far end) — L

PG authoring stays human longest, but the same propose/gate pattern applies:
when an adopted ontology delta orphans or adds characteristics, have the local
LLM draft a PG patch, run it through `validate_procedure_graph` + `diff` + the
canary (P-4), and queue it as a *pre-validated draft* for one-click promotion.
Human still decides — but decides on a finished, machine-checked artifact
instead of authoring from scratch. Do this last; it depends on P-1…P-6 being
trustworthy.

---

## 3. What stays human — rare *by design*

These are the interventions the design deliberately keeps, because they change
the rules rather than apply them. Everything else escalates only on exception.

- Changing the **adoption policy**, weights, retention (admin + rationale, audited).
- Granting **roles** (bootstrap via `GRAP_ROLE`).
- Reviewing the **exception queue** (target: minutes/week, trending down as
  policy and agreement-sets improve).
- Approving **PG drafts** (until P-10 earns auto-promotion for trivial patches).
- Irreversible destruction (retention windows can automate deletion, but
  *shortening* a window is a human act).

## 4. Suggested build order

| Phase | Items | Outcome |
|---|---|---|
| 1 | P-2 rollback, `diff_ontologies`, `diff_runs`, P-1 policy gate, P-6 queue | routine adoption is machine-decided; humans see a queue |
| 2 | P-8 pipeline runner, P-3 watcher, P-7 maintenance autopilot, P-9 alarms | nobody triggers or babysits anything |
| 3 | P-4 canary auto-promotion, P-5 n-version extraction | wrong decisions self-revert; review residue shrinks toward zero |
| 4 | P-10 PG patch drafting | the last frequent human act becomes an approval click |

Phase 1 alone converts the factory from "human in the loop" to "human on the
loop". The load-bearing insight: **almost every gate this needs is already a
deterministic primitive in the repo** (content hashes, versioned proposals,
validation, quarantine, audit). What is missing is not intelligence — it is
plumbing: rollback, diffs, a policy evaluator, a queue, and a scheduler entry.
