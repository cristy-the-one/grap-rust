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

---

## Iteration 2 — is class induction the bottleneck, and is entropy the right
## relation statistic? (PRE-REGISTERED 2026-07-25, before running)

Runs on the frozen 600-doc snapshot `_backup-20260725-0631` (3,739 concepts /
2,624 edges — includes the 763 relations recovered by endpoint promotion).
Fully deterministic: embeddings + graph analytics only, no LLM call, so it is
exactly reproducible.

**Hypotheses.**
- **H1 (class induction).** Iteration 1's typed coverage (56.5%) was capped by
  hand-written morphology rules, not by relation clustering. Seeding those rules
  as high-precision labels and propagating by concept-embedding + structural-role
  similarity will substantially raise coverage.
- **H2 (entropy is the honest statistic).** Purity failed because it punished
  legitimate polysemy. Normalized **entropy of a relation's distribution over
  (class,class) pairs** will cleanly separate *specific* relations (low entropy →
  real schema axioms with domain/range) from *generic* ones (high entropy →
  `se referă la`-like, correctly not axioms). The separation should be bimodal,
  not a smear.
- **H3 (direction noise is measurable).** Collapsing each relation's unordered
  class pair to its dominant direction will yield a consistent schema and
  quantify extraction direction-noise as a minority fraction.

**Predictions + refutation thresholds (written before the run).**
| metric | predict | refute if |
|---|---|---|
| typed edge coverage (both endpoints classed) | **≥ 75%** (from 56.5%) | < 65% |
| unclassified share of top-250 hubs | **≤ 10%** (from 29%) | > 20% |
| induced classes | 15–50 | > 80 or < 8 |
| relations with normalized entropy ≤ 0.5 ("specific") | **≥ 8** | < 5 |
| bimodality: gap between specific and generic entropy modes | visible | entropy uniformly mid-range (no separation) |
| direction consistency of the top specific relation | **≥ 90%** dominant | < 75% |

**Experiment.** (1) Reuse the cached verb clusters from iteration 1. (2) Seed
classes from morphology rules; build per-class centroids from concept-name
embeddings **and** a structural-role vector (distribution over incident relation
clusters, in/out); assign each unassigned concept to its nearest class if above a
recorded similarity floor, else leave Unclassified. (3) Lift all edges; per
relation compute the (class,class) distribution, its normalized entropy, and the
dominant direction. (4) Emit the distilled schema: specific relations with
domain/range/support, generic relations listed separately, plus all metrics.

**Result** (run 2026-07-25, `distill_iter2.py`, snapshot `_backup-20260725-0631`,
3,739 concepts / 2,624 edges, 42 s, no LLM):

| metric | predicted | measured | |
|---|---|---|---|
| typed edge coverage | ≥ 75% | **70.5%** (from 56.5%) | FAIL |
| hubs unclassified | ≤ 10% | **14%** (from 29%) | FAIL |
| induced classes | 15–50 | **7** | FAIL |
| specific relations (H ≤ 0.5) | ≥ 8 | **2** | FAIL |
| entropy separation (bimodal) | visible | **+0.14 gap** | **PASS** |
| top relation direction dominance | ≥ 90% | **73%** | FAIL |

The distilled schema that survived the entropy filter:

```
AuthoritativeBody --emite-->         NormativeAct   [300, H=0.37, dir 73%]
Official          --contrasemnează--> NormativeAct   [200, H=0.42, dir 96%]

generic (correctly excluded): se referă la (H=0.75, 28 pairs), aproba (0.80),
adoptată de (0.59), modificarea şi completarea (0.81), hotărăşte (0.76)
```

**Verdict: REFUTED on the thresholds; H2 confirmed; three sharp findings.**

1. **H1 partially holds but under-delivers, and the cause is my design, not the
   data.** Coverage did improve (56.5% → 70.5%, hubs 29% → 14%), so class typing
   *was* the bottleneck. But I implemented "class induction" as *seeded
   propagation* — assigning concepts to the 7 existing morphology classes — which
   structurally **cannot discover a new class**. That is why the class count came
   in at 7 and failed its own 15–50 prediction. The proposal called for
   *clustering*; I substituted something weaker and cheaper. The prediction wasn't
   wrong, the implementation was.
2. **H2 (entropy) is confirmed and is the right statistic.** The separation is
   real (+0.14 gap between the specific and generic modes) and it puts exactly
   the right relations on each side: `se referă la` ("references", 28 class
   pairs, H=0.75) is correctly *not* an axiom, while `contrasemnează` is. Purity
   is retired.
3. **Direction noise is real, relation-specific, and localized.**
   `contrasemnează` is 96% direction-consistent; `emite` only 73%. The 27% of
   inverted `emite` edges trace to the `EMITENT:` header pattern being extracted
   both ways. This is a concrete KEE extraction defect that distillation
   surfaced — the header should yield a canonical direction.

**The open question that matters most.** Only 2 relations passed the entropy
filter, and I cannot yet tell whether the other 13 are *genuinely generic* or
whether **my induced labels are too noisy/coarse** and are inflating their
entropy. Those are opposite conclusions with opposite fixes. Iteration 3 must
separate them before anything else.

---

## Iteration 3 — is high entropy genuine polysemy or label noise?
## (PRE-REGISTERED 2026-07-25, before running)

**Hypothesis.** The 13 "generic" relations are high-entropy mainly because of
**coarse/noisy class labels**, not genuine polysemy. Computing entropy over only
the *high-precision seeded* labels (dropping propagated ones) will lower it
materially for relations whose semantics are actually typed.

**Predictions + refutation thresholds.**
| check | predict | refute if |
|---|---|---|
| relations moving from generic → specific when restricted to seeded labels | **≥ 3** | 0 |
| mean entropy drop on the 13 generic relations (seeded-only vs induced) | **≥ 0.10** | ≤ 0.03 |
| `aproba` entropy, seeded-only | **≤ 0.6** (from 0.80) | > 0.75 |

If CONFIRMED → the fix is better classes (true clustering, finer granularity),
and the schema will grow as typing improves. If REFUTED → the corpus genuinely
has few typed relations, the distilled grammar is small (~2–4 axioms + a generic
`references`), and we should stop trying to enlarge it and instead test whether a
*small* schema is still useful as extraction context (which is the pitch-black
claim that actually matters).

**Result** (run 2026-07-25, `distill_iter3.py`, same snapshot, 3.9 s, no LLM;
15 relations at support ≥ 8):

| check | predicted | measured | |
|---|---|---|---|
| generic relations that became specific | ≥ 3 | **0** | FAIL |
| mean entropy drop on generic relations | ≥ 0.10 | **+0.008** | FAIL |
| `aproba` entropy, seeded-only | ≤ 0.60 | **0.76** (was 0.80) | FAIL |

Entropy is essentially **invariant** to the label set — every ΔH lands within
±0.05, and several are *negative* (seeded-only entropy slightly higher for
`emite`, `contrasemnează`, `fac parte integrantă din`). There is no systematic
label-noise effect at all.

```
relation                     H(induced)  H(seeded)     ΔH
se referă la                       0.75       0.72   +0.03
emite                              0.37       0.40   -0.03
contrasemnează                     0.42       0.47   -0.05
aproba                             0.80       0.76   +0.04
modificarea şi completarea         0.81       0.80   +0.01
```

**Verdict: REFUTED, decisively — and this settles the question.** The
high-entropy relations are **genuinely polysemous**, not artefacts of coarse
labels. Better classes will not enlarge the schema.

A second observation reinforces it: seeded support ≈ induced support on every
high-support relation (`emite` 411 vs 416). The 228 propagated labels barely
touch the edges that matter, so iteration 2's propagation was nearly irrelevant
to the schema — which is why its entropy didn't move.

**The conclusion for the theme, per iteration 3's own pre-registration.** This
corpus has a **genuinely small grammar**: two strong axioms plus a handful of
borderline ones, over a substrate of type-agnostic cross-referencing.

```
AuthoritativeBody --emite-->          NormativeAct   [416, H=0.37]
Official          --contrasemnează--> NormativeAct   [264, H=0.42, dir 96%]
  borderline:  se face public (0.57) · adoptată de (0.59) · fac parte integrantă din (0.63)
  genuinely generic:  se referă la (H=0.72–0.75, 28 class pairs)
```

That last line is a real finding about the domain, not a defect: **legal
cross-referencing is inherently type-agnostic** — any act may reference any
article, body, budget line or other act — so a `references` relation *should*
have maximal entropy. A distiller that forced it into a typed axiom would be
lying about the law.

**So we stop trying to grow the schema.** The pre-registered implication holds:
the next question is not "how do we find more axioms?" but **"is a small schema
still useful as extraction context?"** — which is the pitch-black claim that
actually matters, and is now the cheapest remaining experiment. A ~5-line grammar
that is *stable* is arguably a better bounded context than a 3,700-concept sea,
regardless of how few axioms it holds.

**Next (iteration 4) — the claim that matters.** A/B test: extract a held-out set
of chunks (a) with the current concept-sea context and (b) with the distilled
5-line schema as context, and compare extraction quality and prompt size. To
pre-register: what "quality" means without a gold standard (candidates —
agreement with the existing graph on the same chunks, self-consistency across
runs, human spot-judgement on a sample).

---

## Iteration 3b — inverse-relation canonicalization
## (PRE-REGISTERED 2026-07-25, before running)

**A correction to iteration 2's finding.** Iteration 2 reported `emite` at 73%
direction dominance and I called it a KEE extraction defect. Inspecting the edges
shows that was wrong. The 111 "inverted" edges are
`Act --emite--> GUVERNUL/PARLAMENT`, derived from the document header
`HOTĂRÂRE nr. 99 …` / `EMITENT: GUVERNUL` — they encode **`hasIssuer`**,
correctly directed, but carry the label `emite` because the header word is
`EMITENT`. The other 295 encode **`issues`**. Both are faithful to the source;
the relation *label* conflates a relation and its inverse.

Extraction is therefore not at fault, and "fixing" it would push
Romanian-specific morphology into a generic component. **Collapsing inverse
framings is distillation's job** — the same job it already does for
`is part of` / `face parte integrantă din`.

**Hypothesis.** Canonicalizing each relation's inverse directions (for a relation
R and unordered class pair {A,B}, fold `(B,R,A)` into the higher-support
`(A,R,B)`) will raise direction dominance to 1.0 and *lower* entropy, because two
distinct class pairs collapse into one.

**Predictions + refutation thresholds.**
| check | predict | refute if |
|---|---|---|
| `emite` direction dominance | **1.00** (from 0.73) | < 1.00 |
| `emite` normalized entropy | **≤ 0.30** (from 0.37) | ≥ 0.37 (no drop) |
| specific relations (H ≤ 0.5) | **≥ 3** (from 2) | still 2 |
| typed edge coverage | unchanged (~70.5%) | changes (would mean edges lost) |

**Result** (run 2026-07-25, same frozen snapshot, deterministic, no LLM):

| check | predicted | measured | |
|---|---|---|---|
| `emite` direction dominance | 1.00 | **1.00** (from 0.73) | PASS |
| `emite` normalized entropy | ≤ 0.30 | **0.05** (from 0.37) | PASS |
| specific relations (H ≤ 0.5) | ≥ 3 | **3** (from 2) | PASS |
| typed edge coverage | unchanged | **1,849 edges, unchanged** | PASS |

**Verdict: CONFIRMED on all four.** The distilled schema after canonicalization:

```
AuthoritativeBody --emite-->           NormativeAct        [416, H=0.05, dir 100%]
Official          --contrasemnează-->  NormativeAct        [264, H=0.40, dir 100%]
NormativeAct      --adoptată de-->     AuthoritativeBody   [ 89, H=0.41, dir 100%]
```

`emite` at **H = 0.05** is essentially a pure typed axiom — 416 instances,
one class pair, one direction. Compare the same relation two iterations ago:
purity 0.82 and a contradictory reverse triple at support 41. The signal was
always there; two successive measurement fixes (entropy instead of purity, then
inverse canonicalization) were needed to see it.

**An observation that points at the next step.** `NormativeAct --adoptată de-->
AuthoritativeBody` is the semantic **inverse of** `AuthoritativeBody --emite-->
NormativeAct` — but under a *different label*, so intra-label canonicalization
cannot merge them. Detecting that two distinct relations are inverses of each
other over the same class pair is **inverse-relation discovery**, i.e. Stage 5
axiom induction from the proposal. It would reduce these three axioms to two plus
an `inverseOf` assertion — a genuinely smaller and more truthful grammar.

---

## Revision log
- **2026-07-25** — Iteration 3b run: CONFIRMED on all four checks. `emite`
  H 0.37→0.05, dominance 73%→100%, specific relations 2→3, coverage unchanged.
  Noted that `emite` and `adoptată de` are cross-label inverses, motivating
  Stage-5 inverse-relation discovery.
- **2026-07-25** — Iteration 3b pre-registered, correcting iteration 2's
  misattribution: the `emite` direction split is inverse-relation label
  conflation (`issues` vs `hasIssuer`), faithful to the source text, and belongs
  to distillation to canonicalize — not an extraction defect.
- **2026-07-25** — Iteration 3 run: REFUTED decisively (0 relations moved, ΔH
  +0.008, entropy invariant to label set). Conclusion: the corpus has a genuinely
  small grammar (2 strong axioms + type-agnostic `references`); label quality is
  not the limit. Theme redirected from "grow the schema" to "test the small
  schema as extraction context" (iteration 4).
- **2026-07-25** — Iteration 2 run: REFUTED on thresholds (coverage 70.5% vs 75%,
  7 classes vs 15–50, 2 specific relations vs ≥8), H2/entropy CONFIRMED (+0.14
  bimodal gap), direction noise quantified (`emite` 73% vs `contrasemnează` 96%).
  Root cause of the class-count miss: seeded propagation cannot discover classes.
  Iteration 3 pre-registered to separate label noise from genuine polysemy.
- **2026-07-25** — Iteration 1 verdict recorded (CONFIRMED, purity refuted).
  Iteration 2 pre-registered against the frozen 600-doc snapshot.
- **2026-07-24** — Journal created; Iteration 1 pre-registered. Gated on the
  400→600 loop completing.
