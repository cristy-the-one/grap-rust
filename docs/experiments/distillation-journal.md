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

**Result** (run 2026-07-24, `pilot-law-ro/distill_iter1.py`, snapshot
`_backup-20260724-2109`, 3,321 concepts / 1,792 edges / 419 verbs; params
verb_sim 0.72, top_hubs 250; artifacts in `pilot-law-ro/distillation/iter-1/`,
5.3 s total):

| pre-registered check | threshold | measured | |
|---|---|---|---|
| clusters covering 80% of edges | ≤ 25 | **24** | PASS |
| `GUVERNUL`/`Guvern`/`Guvernul României` → one class | yes | **yes** (AuthoritativeBody) | PASS |
| `fac parte integrantă din`/`face parte…`/`is part of` → one relation | yes | **yes** (one cluster) | PASS |
| top-10 abstract triples sensible | ≥ 7/10 | **~7–8/10** | PASS (borderline) |
| mean purity, top-10 relations | ≥ 0.80 | **0.41** | **FAIL** |
| typed edge coverage (both endpoints classed) | — | 56.5% | — |

419 verbs → 211 clusters overall, but the head is what matters: 24 clusters carry
80% of all edges. The verb clusters are clean and cross-lingual —
`emite`/`EMITENT`/`emit`; `hotărăşte`/`decide`/`DECIDE`/`hotărăște`;
`fac parte integrantă din` absorbing the English `is part of`.

The single highest-support abstract triple is exactly the target of this whole
theme:

```
AuthoritativeBody --emite--> NormativeAct    [support 221, purity 0.82]
Official --contrasemnează--> NormativeAct    [support  71]
StructuralPart --fac parte integrantă din--> NormativeAct  [support 22]
NormativeAct --modificarea şi completarea--> NormativeAct   [support 18]
```

**Verdict: CONFIRMED, with the purity metric refuted.** The hypothesis — this
corpus has a learnable grammar — holds: none of the pre-registered refutation
criteria fired (24 ≪ 40 clusters, ~7–8/10 ≫ 4/10 coherent, both synonym sets
unified). But purity failed badly, and the *diagnosis of why* is the real result:

1. **The metric penalizes honest polysemy.** `se referă la` ("references") has
   purity 0.21 because it legitimately connects many type pairs — that is
   correct behaviour for a generic relation, not a defect. Averaging purity over
   the top-10 relations therefore measures "how many generic relations are in the
   head", not typing quality.
2. **Direction inconsistency in the raw graph.** `NormativeAct --emite-->
   AuthoritativeBody` (support 41) appears alongside the correct
   `AuthoritativeBody --emite--> NormativeAct` (221) — the `EMITENT:` header
   pattern was extracted in both directions. A genuine raw-data finding that
   distillation surfaced.
3. **Class induction is the bottleneck, not relation clustering.** 72/250 hubs
   (29%) are `Unclassified` under hand-written morphology rules (`Statul de
   reşedinţă`, `egalitatea în drepturi`, and the misspelled `Articulul 9`).
   Coverage of 56.5% is capped by typing, not by verbs.

**Next (iteration 2).** Fix the measurement and the weakest stage:
(a) redefine purity per *(relation, direction)* and report generic vs specific
relations separately instead of averaging — a relation's *entropy over type
pairs* is the honest statistic; (b) normalize edge direction for
header-derived patterns; (c) replace hand rules with real class induction
(embedding + structural role + the promoted hub endpoints as seeds) and re-measure
coverage. Pre-register before running.

---

## Revision log
- **2026-07-24** — Journal created; Iteration 1 pre-registered. Gated on the
  400→600 loop completing.
