# Ontology Distillation — experiment journal

> Append-only research trace for the distillation theme
> ([[ontology-distillation-theme]], proposal in
> `docs/ontology-distillation-proposal.md`). The method is the lesson
> ([[dark-factory-lessons]] §Field data): a **tight hypothesis → experiment →
> measure → verdict** loop, run until a useful conclusion. This file is the
> trace; both confirmations and refutations are recorded, because a cheap
> refutation is progress.

## Operating protocol

Each iteration is one falsifiable claim, **pre-registered** (prediction and
refutation threshold written *before* running), then measured against it:

1. **Hypothesis** — one sentence, falsifiable.
2. **Prediction + threshold** — the numbers that would confirm it, and the ones
   that would refute it. Written before the run. No moving goalposts.
3. **Experiment** — exactly what is computed, on which graph snapshot.
4. **Result** — the measured numbers (filled in after the run).
5. **Verdict** — CONFIRMED / REFUTED / INCONCLUSIVE, against the pre-registered
   threshold.
6. **Next** — what this implies for the next iteration.

**Trace & checkpoints (for recovery).**
- This journal is the human-readable trace, append-only.
- Every iteration writes its inputs (params, thresholds, the graph snapshot hash)
  and outputs (verb embeddings, clusters, type assignments, lifted triples,
  metrics) to `pilot-law-ro/distillation/iter-<N>/` as JSON, so a crashed run
  resumes, a result is re-inspectable, and a re-run is deterministic.
- Expensive intermediates (the 418 bge-m3 verb embeddings) are cached, not
  recomputed — the batching/checkpoint lessons applied to the experiment itself.
- Clustering uses a fixed seed; all thresholds are recorded in the iter dir.
- Experiments run against a **frozen snapshot** of the graph, never the live
  working graph while a factory/loop run is writing it.

---

## Iteration 1 — does the corpus have a clean grammar? (PENDING)

Starts after the 400→600 loop (`bijitjqmk`) completes and its results are
collected. Runs on the frozen 600-doc graph.

**Hypothesis.** The 418 surface relation verbs collapse into a small set of
abstract relations, and the highest-support instance edges lift into
human-sensible abstract triples with high type-purity — i.e. this corpus has a
learnable schema.

**Prediction + threshold (pre-registered):**
- Verb compression: 418 verbs → **≤ 25** clusters that cover **≥ 80%** of edges.
- Synonym unification: `GUVERNUL` / `Guvernul României` / `Guvern` land in **one**
  class; `fac parte integrantă din` / `face parte integrantă din` / `is part of`
  land in **one** relation.
- Top-10 abstract triples by support: **≥ 7/10** judged sensible by a human
  (e.g. `AuthoritativeBody --issues--> NormativeAct`).
- Mean type-purity of the top relations: **≥ 0.80**.
- **REFUTE if:** ≥ 40 clusters needed for 80% coverage, OR < 4/10 top triples
  coherent, OR the obvious synonyms above do not unify.

**Experiment.**
1. Embed the 418 verb phrases (bge-m3, batched; cached to the iter dir); cluster.
2. Coarse-type the ~200 highest-degree endpoints by name-morphology rules + one
   bounded LLM pass (NormativeAct / AuthoritativeBody / Official / Institution /
   Article / Annex / LegalException / …).
3. Lift the top-50 edges by frequency to `Class --Rel--> Class`; compute support
   and purity per abstract triple.
4. Emit: compression ratio, edge coverage, mean purity, the top-10 triples for
   human judgment, and the two synonym-unification checks.

**Result.** _(to be filled)_

**Verdict.** _(to be filled)_

**Next.** If CONFIRMED → build Stage 1+2 properly and proceed to schema lifting
(Stage 3) with support/purity gates. If REFUTED → record *why* (no clean
predicate structure? classes too muddy?) and decide whether a different signal
(structural role over embeddings, or a narrower relation subset) is worth one
more iteration before abandoning.

---

## Revision log
- **2026-07-24** — Journal created; Iteration 1 pre-registered. Gated on the
  400→600 loop completing.
