# Self-Review: Dark-Factory Phases 3–5

Written 2026-07-20, after two live lights-out runs against Ternary-Bonsai-27B
and with the suite at 253 passing. This is a critical pass over the canary
(P3a), n-version extraction (P3b), PG drafting (P4), and auto-promotion (P5)
— what the live runs proved, what was found and fixed, and what remains as an
honest limitation with a proposed remedy.

## What the live runs proved

- **The whole line runs against a real local model.** Fresh workspace, all
  four policies on, two `run_pipeline` cycles against live Bonsai-27B
  (verified in the server log: 5 prompt evaluations, ~44 tok/s decode):
  extraction → policy adoption → PG draft → auto-promotion → gap runs →
  maintenance → `HEALTHY: True`, empty queue. Run 2 fully idempotent
  (`already_active`, zero gap delta, manifest-skipped ingest).
- **The gates catch real LLM failures.** The first live run's PG draft
  dropped the mandatory `references` field (the model mimicked the input's
  shape over the prose rule). The deterministic coverage gate rejected it,
  the queue said `pg_draft_failed`, nothing was promoted. This is the
  system's central claim working under fire: a bad model output cost an
  audited rejection, not a corrupted procedure graph.
- **Stubs cannot test prompt fidelity.** All 253 tests were green while the
  live draft failed. The fix (a concrete example step in the prompt,
  `8f9181f`) was found only by running the real model. Keep a live smoke
  (the scratch driver pattern) as a periodic check; treat stub-green as
  necessary, never sufficient, for anything with a prompt in it.

## Found in this review and fixed immediately

- **Zero-outcome adoption footgun.** The n-version disagreement *report* is
  a published record whose hash a human (or agent) could plausibly pass to
  `adopt_proposed_ontology`. It would have passed hash verification
  trivially (no hashes to check), projected nothing, and **silently
  deactivated gap detection** — a perfectly healthy-looking factory
  governing nothing. Both `_adopt_record` and `rollback_ontology` now refuse
  documents with zero process outcomes (tested, incl. the rollback path).

## Known limitations, ranked, with remedies

1. **Canary baseline staleness.** The baseline is the *previous* production
   run, which predates this cycle's evidence ingest. New evidence usually
   reduces gaps (bias toward passing — benign); evidence *removals* could
   surface as new gaps blamed on the candidate ontology (false rollback —
   conservative but wrong attribution). Remedy: run a fresh baseline gap-run
   after ingest but before adoption (one extra gap run per cycle; cheap).
2. **Renames read as regressions.** `diff_runs` compares explanation
   targets, so renaming an outcome id yields new+resolved simultaneously and
   the canary counts the "new" side — renames effectively cannot
   auto-adopt. Conservative by design, but it biases the factory against
   refactoring. Remedy: match renamed targets via concept_hashes (same
   sha256(name) on both sides of the rename means same concept) before
   counting.
3. **No state rehydration across restarts.** `ontology_version`,
   `projected_nodes`, and the loaded PG are session state. A fresh process
   over an old workspace: the canary's rollback target is missing (flagged,
   not fatal — by design), and evidence exclusion falls back to the
   label/text-prefix guards in `_is_projected` (verified present — this is
   why the design survives restarts at all). Remedy: on workspace open,
   rehydrate the active ontology from the latest `ontology_adopted` /
   `ontology_rolled_back` audit event. Medium effort, closes several edges
   at once.
4. **Promoted PG drafts never reach the PG file.** `run_pipeline` reloads
   `pg_path` each cycle, so after an auto-promotion the *file* is still the
   old PG; if adoption ever recurs, the drafter re-drafts from the stale
   file. Content addressing keeps this idempotent (same draft → same hash →
   already promoted → not pending), so the cost is a wasted LLM call, not
   drift — but the file and the promoted lineage diverge. Remedy: opt-in
   config flag to write auto-promoted drafts back to `pg_path` (file becomes
   the working copy of the promoted head).
5. **Auto-promotion stops at governance level — deliberately.** P5 promotes
   through `promote_pg`; it does not `activate` or move the production flag.
   Weight-freezing activation stays an explicit act. This is a design choice
   worth restating, not a gap: "lights-out" covers propose→verify→promote;
   *arming* production remains human.
6. **N-version matching quality is embedder-bound.** Agreement matching
   falls back from exact normalized names to embedding similarity; with the
   hashing embedder (tests, demos) that fuzzy tier is weak. In production
   run n-version with a real embedder (`GRAP_EMBED_BACKEND=ollama`,
   nomic-embed-text) or expect the agreement set to be under-inclusive —
   which fails *safe* (more residue for humans), never wrong.

## Verdict

P3–P5 hold up: every trust-critical decision is a deterministic predicate
(hash equality, diff counts, coverage sets, additive-only rules), the LLM
never has authority — only proposals — and the one live failure mode found
was caught by the gates exactly as designed. The limitation list above is
short, ranked, and none of it is load-bearing for safety; items 1–3 are the
worthwhile next investments, in that order.
