# Phase 3 Implementation Notes (handoff)

Written 2026-07-19 after Phases 1–2 landed (see `docs/dark-factory-proposals.md`
status header). Phase 3 = **P-4 canary auto-promotion** + **P-5 n-version
extraction**. Build canary first: it is pure orchestration of tools that
already exist (~1 day); n-version needs a merge algorithm (~2–3 days).

## P-4 Canary auto-promotion

**Where:** extend `_publish_and_gate_proposal` in `server.py` — after the
policy verdict passes, insert a canary stage instead of adopting directly.

**Flow (all existing tools):**
1. `prev = _state.ontology_version` (the rollback target — never skip this).
2. Baseline gaps = the last production run (`ws.list_runs("production")[-1]`);
   `run_pipeline` already produces one per cycle. If none exists, adopt
   without canary (bootstrap, mirror the churn check's bootstrap semantics).
3. `_adopt_record(...)` the candidate, then run the canary as a **quarantined
   experiment**: `_run_gap_detection(scope, "experiment", {"canary": version_hash})`
   — experiments are already excluded from quality metrics, full audit parity.
4. `diff_runs(baseline_run_id, canary_run_id)` → budget check.
5. Pass → record `canary_passed` (subject `new=N;resolved=M;to=<hash>`), done.
   Fail → `rollback_ontology(prev, reason="canary: ...")` + record
   `canary_failed` (subject `new=N;from=<prev>;to=<hash>`), **return ok** —
   the line never blocks; the exception goes to the queue.

**Policy keys** (add to `DEFAULT_ADOPTION_POLICY`, they flow through
`set_policy` automatically — note set_policy casts non-`enabled` values to
int, so use integer budgets): `canary_enabled` (treat like `enabled`: bool),
`canary_max_new_gaps` (default 0).

**Queue:** add `canary_failed` as a latest-state alarm in
`list_pending_interventions` (same pattern as `maintenance_failed`: pending
while the most recent `canary_*` event is a failure, cleared by the next
`canary_passed`). Subjects are `k=v;k=v` — parse with `_kv_subject`.

**Gotchas:**
- The baseline and canary must run against the **same evidence KG** — in
  `run_pipeline` order the canary before the step-4 gap run, or reuse step 4's
  run as the canary run (cheaper: adopt → step-4 detect_gaps → diff vs
  previous production run → rollback if over budget; you already record
  `gap_delta` there, so the canary check is ~15 lines).
- `projected_nodes` accumulates across adopt/rollback (old projections stay
  excluded from evidence — correct for scoring, but KG grows). Fine short
  term; note it in the tool docstring and let `compact_graph` handle bloat.
- `diff_runs` compares explanation *targets*; a rename of an outcome id shows
  up as new+resolved simultaneously — count `new_gaps` only against the
  budget, ignore `resolved`.

## P-4 DETAILED BUILD SPEC (do this one first — it is the critical path)

Est. ~120 lines + tests, one session. Every primitive exists; this is wiring.

**Step 0.** Commit Phase 2 (still uncommitted at handoff).

**Step 1 — policy keys** (`governance.DEFAULT_ADOPTION_POLICY`):
`"canary_enabled": False`, `"canary_max_new_gaps": 0`. In `set_policy`,
extend the bool cast: `if key in ("enabled", "canary_enabled"): value = bool(value)`.

**Step 2 — helper in server.py:**
```python
def _canary_check(ws, adopted_hash):
    prod = ws.list_runs("production") or []
    if not prod:
        return {"skipped": "no production baseline"}      # bootstrap
    baseline = prod[-1].run_id
    exp = _run_gap_detection("all", "experiment", {"canary_for": adopted_hash})
    if exp.get("status") != "ok":
        return {"new_gaps": 10**9, "detail": exp}          # conservative: fail
    d = diff_runs(baseline, exp["run_id"])
    return {"baseline_run": baseline, "canary_run": exp["run_id"],
            "new_gaps": d["counts"]["new"] if d.get("status") == "ok" else 10**9,
            "detail": d}
```

**Step 3 — wire into `_publish_and_gate_proposal`.** Capture
`prev = getattr(_state, "ontology_version", None)` **before** `_adopt_record`.
After a successful adopt, when `policy["canary_enabled"]`:
```python
c = _canary_check(ws, version.hash)
if c.get("skipped"):
    ws.record_event("canary_passed", f"new=0;to={version.hash};note=bootstrap")
elif c["new_gaps"] > int(policy["canary_max_new_gaps"]):
    rollback_ontology(prev, reason=f"canary: {c['new_gaps']} new gaps over budget")
    ws.record_event("canary_failed", f"new={c['new_gaps']};from={prev};to={version.hash}")
    auto_adoption.update({"canary": "failed", "rolled_back_to": prev,
                          "new_gaps": c["new_gaps"]})
else:
    ws.record_event("canary_passed", f"new={c['new_gaps']};to={version.hash}")
    auto_adoption["canary"] = "passed"
```
Invariant that makes this safe: a production baseline run exists **iff** an
ontology was active when it ran — so whenever the canary is not skipped,
`prev` is non-None and rollback has a target. The canary runs as a
*quarantined experiment* (`kind="experiment"`), so quality metrics stay clean.

**Step 4 — queue alarm** in `list_pending_interventions` (pass/fail pair,
not single-latest):
```python
ev_f = _latest_event(ws, "canary_failed"); ev_p = _latest_event(ws, "canary_passed")
if ev_f and (not ev_p or ev_f.ts >= ev_p.ts):
    items.append({"kind": "canary_failed", "subject": ev_f.subject, ...})
```

**Step 5** — add `"canary_failed"` to the alarm-kinds tuple in
`get_factory_status`. `run_pipeline` needs no change (the gate helper owns it).

**Step 6 — tests** (extend `test_dark_factory.py` patterns):
1. *Fail+revert:* adopt v1, ingest evidence covering it, `detect_gaps`
   (baseline); enable policy+canary; auto-adopt a v2 whose characteristics
   have no evidence → assert `_state.ontology_version` back at v1's adoption
   hash, `canary_failed` pending, response has `canary: "failed"`.
2. *Pass+clear:* then auto-adopt a good candidate → `canary: "passed"`,
   queue empty again.
3. *Bootstrap:* very first adoption with canary on → `skipped`, stays adopted.
4. `set_policy("canary_enabled", True, ...)` bool-cast round-trip.

**Traps:** count only `new_gaps` against the budget (an outcome rename shows
as new+resolved simultaneously); don't run the canary for
`reason="already_active"` shortcuts; `rollback_ontology` leaves old projected
nodes excluded-but-present in the KG (fine for scoring; `compact_graph`
handles bloat).

## P-5 N-version extraction

**Config seam:** `extract.models` in the pipeline config, each entry
`{"base_url": ..., "model": ..., "extra_body": {...}}` →
`llm.OpenAICompatProvider(base_url=..., model=..., extra_body=...)` (all
ctor params already exist). Run passes **sequentially, never parallel** —
Bonsai-27B holds VRAM; a second resident model will spill (16 GB card).

**Merge algorithm** (new pure function in `governance.py`,
`agreement_merge(proposal_a, proposal_b, embedder, sim)`):
1. Match outcomes across proposals: first by exact name (the concept hash IS
   `sha256(name)`, so equal hashes = equal names), then fuzzy via the existing
   embedder + `reconcile` cosine machinery for near-matches (≥ sim).
2. Agreement set → merged proposal: intersect per-outcome characteristic
   sets; disputed characteristics go into the disagreement report, never
   silently dropped. `source_chunk_counts` = min of the two (conservative
   provenance floor); recompute `concept_hashes` as sha256(name) — legitimate,
   that is the definition.
3. Disagreement set → publish as its own proposal with
   `metadata={"kind": "disputed"}` and record a `model_disagreement` event
   (subject `outcomes=N;version=<hash>`) → queue kind `model_disagreement`.
   Do NOT extend the sqlite `parked` table for this — publishing a disputed
   proposal needs no schema migration and keeps review in the same
   list/get/adopt loop humans already use.
4. The merged (agreed) proposal goes through the normal
   `_publish_and_gate_proposal` — policy + canary apply unchanged.

**Testing hints** (mirror `test_dark_factory.py` patterns):
- Two `StubProvider`s: agree on outcome A, disagree on B → merged proposal
  contains A only, queue shows `model_disagreement`, auto-adoption adopts A.
- Canary: stub a proposal that passes policy but whose adoption creates new
  gaps (easy: adopt an ontology whose characteristics have no evidence in the
  KG) → assert rollback happened (`_state.ontology_version == prev`) and
  `canary_failed` is pending; then a passing candidate clears it.

## General (hard-won this session)

- Keep logic **pure in `governance.py`**, server thin — that is what made
  Phase 1–2 testable in milliseconds.
- Never assert on result *ordering* from search/RRF paths in tests — tie
  order is hash-seed dependent per process (caused a 30% flaky test once).
- Event `details` are write-only in the pyo3 binding — machine state goes in
  `k=v;k=v` subjects, parsed with `_kv_subject`.
- The suite gate: `python -m pytest mcp-server/tests` (venv `.venv`), CI job
  `mcp-server-tests` in `.github/workflows/ci.yml`. 234 tests green at handoff.
- Uncommitted at handoff: Phase 2 (server.py factory tools, run_factory.py,
  test_factory.py, doc updates) — commit it before starting Phase 3.
