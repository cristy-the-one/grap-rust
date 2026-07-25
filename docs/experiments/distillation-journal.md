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

## Iteration 4 — is a small schema good extraction context?
## (PRE-REGISTERED 2026-07-25, before running) — THE PITCH-BLACK CLAIM

The claim that matters: a *stable, small* schema as extraction context makes
prompt size independent of corpus size, so the factory can run unbounded. Tested
as **non-inferiority**, not superiority (per the agreed basis): context must stop
growing **and** quality must not collapse.

**Arms.** Same chunks, same system prompt, same parser, same model/settings.
- **A (concept-sea)** — today's context: `wg.context_for(text)` over the
  3,739-concept graph, bounded by the caps from `a25dfc1`.
- **B (distilled schema)** — the iteration-3b schema: 7 class names + 3 typed
  axioms + the generic-relation note. Fixed size, independent of the graph.

**Chunks.** 10 chunks from documents **never ingested** (the pool holds ~1,490
unused acts), so neither arm has memorised them.

**Hypotheses.**
- **H1 (bounded).** B's prompt is materially smaller and, unlike A, cannot grow
  with the corpus.
- **H2 (non-inferior yield).** B extracts a comparable amount — it must not
  collapse.
- **H3 (canonicalising, the interesting one).** B should *reduce inverse-label
  conflation at the source*: told `AuthoritativeBody --emite--> NormativeAct`, the
  model should stop emitting the `hasIssuer` framing that cost us iteration 3b.
  If so, schema-as-context improves raw-graph consistency, not just prompt size.

**Predictions + refutation thresholds.**
| check | predict | refute if |
|---|---|---|
| prompt tokens, B vs A | **B ≤ ⅓ of A** | B ≥ A |
| outcomes+relations per chunk, B vs A | **≥ 75% of A** (non-inferior) | < 60% of A |
| `emite`-family edges in canonical direction, B | **≥ 90%** (baseline 73%) | ≤ 73% (no gain) |
| parse failures, B | ≤ A + 1 | markedly worse |

Human spot-check (10 chunks, side-by-side) is the only correctness signal and is
recorded separately as a judgement, not a threshold.

**Result** (run 2026-07-25, 10 held-out chunks × 2 arms, artifacts in
`distillation/iter-4/`):

| pre-registered check | predicted | measured | |
|---|---|---|---|
| prompt tokens B vs A | ≤ 0.33× | **0.13×** (250 vs 1,941) | PASS |
| yield B vs A | ≥ 0.75× | **2.33×** | PASS |
| B `emite` canonical rate | ≥ 90% | **100%** | PASS |
| B parse failures | ≤ 1 | **0** | PASS |

**All four checks passed. The experiment is still REFUTED, on quality — and the
metrics are what failed.**

Inspecting the outputs (the step the numbers did not require):

```
arm B relation types:  emite ×7,  adoptată de ×7,  contrasemnează ×7
                       + literal type strings "--emite-->", "--adoptată de-->", …
GUVERNUL --emite--> ORDONANŢA           and
ORDONANŢA --adoptată de--> GUVERNUL     ← one fact, both directions, because both were listed
MONITORUL OFICIAL --contrasemnează--> ORDONANŢA   ← false; a gazette countersigns nothing
16% of arm-B outcome names were CLASS names ("NormativeAct", "AuthoritativeBody")
arm B emitted exactly 3 relations on 10/10 chunks
```

Arm B **echoed the schema instead of reading the document**: it instantiated every
listed axiom a near-equal number of times regardless of content, copied the ASCII
arrow notation into the *type* field, duplicated inverse pairs because both were
listed, fabricated `contrasemnează` to fill the third slot, and in one chunk used
the class names *as* the instance names — which would collapse every act in the
corpus into a single node. Arm A, by contrast, produced correct sparse triples
(`PARLAMENTUL --emite--> LEGE`).

So B's "2.33× yield" was duplication plus fabrication, and its "100% canonical
rate" was copying, not comprehension.

**Verdict: REFUTED. Schema-as-context, presented this way, is actively harmful.**

**Two lessons, both larger than this experiment.**
1. **The ossification risk is real and immediate, not eventual.** The proposal
   worried that a schema-guided extractor would slowly come to confirm its own
   schema. It does not do it slowly; given the schema as context it does it on the
   first chunk, crudely, by fabricating instances of every axiom. The
   anti-ossification machinery (MDL eviction, residual set) is therefore not a
   refinement to add later — without it, the self-improving loop degenerates
   immediately.
2. **Every metric I pre-registered was a quantity metric, and all four were
   unanimously fooled by a qualitative collapse.** Tokens, yield, canonical rate,
   parse rate: all passed, all meaningless. The only element of the design that
   could catch this was the human spot-check — and it was caught by *looking at
   the artifacts*, not by reading the dashboard. Pre-registration protects against
   moving the goalposts; it does **not** protect against measuring the wrong thing.

**Two flaws in my own design, owned.**
- I used only the **first chunk** of each document, so every sample was a
  structurally identical header. That amplified the uniformity and means
  mid-document extraction was never tested.
- The arm-B prompt said "Use these class names and relation directions when the
  same kinds of thing appear here" — an *invitation to instantiate*. The schema
  was framed as a template to fill rather than a vocabulary to constrain.

**Next (iteration 4b).** The claim is not dead, but the *presentation* is. Retest
with the schema as a **constraint, not a template**: supply the class names for
naming consistency, state the permitted relation types as a closed vocabulary,
and instruct explicitly that a relation may be asserted *only if this text states
it*; drop the arrow notation. Sample mid-document chunks, not just headers. Add a
qualitative check to the pre-registration this time — e.g. relation-type entropy
per chunk (a template-filler produces near-zero variance across chunks, which is
exactly the signature that gave this away).

---

## Iteration 4b — schema as *constraint*, not template
## (PRE-REGISTERED 2026-07-25, before running)

Iteration 4's collapse may be my prompt's fault rather than the idea's: it said
"use these class names and relation directions when the same kinds of thing appear
here" — an invitation to instantiate — and drew the axioms in ASCII arrows the
model then copied into the type field. 4b separates *bad prompt* from *bad idea*.

**The asymmetry that motivates arm C.** The concept-sea context lists
**instances**, so "reuse what you see" *is* the desired behaviour (dedup). A
schema lists **patterns**, so "reuse what you see" becomes *assert this pattern*
(fabrication). They are not symmetric substitutes: dedup needs instance names. If
that holds, the right context is class vocabulary **plus a bounded sample of
instance names** — and pitch black was already achieved by bounding the context in
`a25dfc1`, making a second mechanism unnecessary.

**Arms** (same chunks, system prompt, parser, model):
- **A** concept-sea (unchanged baseline).
- **B2** schema as a **closed vocabulary**: class names, permitted relation types
  as a plain list (no arrows, no domain/range template), and an explicit
  *"assert a relation ONLY if this text states it; omit types that do not apply."*
- **C** hybrid: class vocabulary **+ bounded list of instance names only** (no
  characteristics, no relations, no axioms) — nothing to instantiate.

**Chunks.** 10 **mid-document** chunks (index ≥ 1) from never-ingested docs, so
headers no longer dominate and structural uniformity is not baked in.

**Predictions + refutation thresholds.** Quantity checks are now *two-sided* —
iteration 4 taught that a yield far *above* baseline signals fabrication, not
quality.
| check | predict | refute if |
|---|---|---|
| prompt tokens vs A | B2 & C ≤ 0.5× | ≥ A |
| yield vs A (two-sided) | **0.75×–1.5×** | outside 0.6×–2.0× |
| **CV of per-chunk relation count** (template detector) | **≥ 0.25** | ≤ 0.10 (constant output = template) |
| class-name-used-as-instance-name | **0%** | > 0% |
| notation leakage in relation types (`-->`) | **0** | > 0 |
| schema-violating relations (type used with wrong endpoint classes) | **≤ 10%** | > 25% |

**Result** (run 2026-07-25, 10 mid-document held-out chunks × 3 arms):

| | A concept-sea | B2 closed vocab | C hybrid |
|---|---|---|---|
| prompt tokens | 3,355 | **663 (0.20×)** | **929 (0.28×)** |
| yield | 9.9 | 9.3 (0.94×) | 9.0 (0.91×) |
| relation-count CV | 0.42 | 0.49 | **0.82** |
| distinct relation types | 34 | **14** | **37** |
| class-name-as-instance | 0 | 0 | 0 |
| notation leakage | 0 | 0 | 0 |
| schema violations | 2/4 (50%) | 7/13 (54%) | **0/2 (0%)** |
| parse failures | 0 | 0 | **2** |

**Arm C: 6/6 pre-registered checks PASS. Arm B2: 5/6, failing on violations.**

**First, a correction to my own metric.** I pre-registered "schema violations ≤ 10%"
*without measuring the baseline* — and the baseline is **50%**. The threshold was
unachievable by construction. Inspecting arm A's violations shows they are the
already-known inverse-label conflation (`ORDONANŢA --emite--> GUVERNUL`), i.e. the
iteration-3b phenomenon, not new errors. **Third instrument error in five
iterations: measure the baseline before setting an absolute threshold.**

**But B2 is genuinely harmful, for a reason the metric only hinted at.** Its
violations are not classifier gaps — they are *type overloading*:

```
Ministerul Transporturilor    --emite--> "aproba şi comunică părţii solicitante"
Ministerul Apărării Naţionale --emite--> "organizează şi asigura, în con…"
Ministerul Apărării Naţionale --emite--> "transmite Agenţiei Naţionale d…"
```

The object is a **verb clause, not an entity**. Given a *closed list* of permitted
relation types, the model forced every "body does something" statement onto the
nearest permitted type instead of omitting it — despite the explicit "omit types
this text does not support". The collapse in distinct relation types (34 → **14**)
is the same effect measured from the other side: a closed vocabulary destroys the
natural diversity of legal relation language.

**Arm C — class vocabulary + bounded instance names, with no relation types
listed — is the design that works.** It has the *highest* relation-type diversity
(37, above baseline), the highest content-responsiveness (CV 0.82), zero
violations, and a 3.6× smaller prompt. Its two parse failures are repetition
degeneration (`"alt venit bugetar"` ×N until the 2048-token cap) — the known
Bonsai failure mode, though 2/10 vs A's 0/10 is a real cost worth watching.

**Verdict: the asymmetry hypothesis is CONFIRMED.**
- Listing **instances** is safe and necessary — reuse of an instance name *is*
  dedup, the behaviour we want.
- Listing **patterns** (axioms, or even a closed type vocabulary) is unsafe —
  "reuse what you see" becomes "assert/force this pattern", producing fabrication
  (iteration 4) or type overloading (B2).
- A **class vocabulary** is a safe middle: it aids naming without asserting
  anything.

**What this means for pitch black.** Arm C's context is a fixed class list plus a
*bounded* sample of instance names — bounded by construction, independent of
corpus size. But that is precisely what `context_cap` plus the caps from
`a25dfc1` already do. **So the unbounded-context problem was already solved by
bounding the context; distillation's contribution to pitch black is the small
stable class vocabulary, not a replacement for instances.** Iteration 4 was
chasing a second mechanism that was never needed. The pitch-black claim stands —
on the simpler foundation we already built.

---

## Iteration 5 — systematic axiom discovery (PRE-REGISTERED 2026-07-25)

**Discovery-first, no cherry-picking.** Enumerate *every* candidate axiom over the
lifted graph, score them all, and only then look. Two families:

- **Inverse pairs.** Two signals, both computed for all ordered relation pairs:
  *(i) instance-level* — count `(x,y)` with `R1(x,y)` **and** `R2(y,x)` present;
  confidence = overlap / min(|R1|,|R2|). Strong evidence. *(ii) type-level* —
  `R1: A→B` and `R2: B→A` over the same class pair with overlapping endpoint
  populations. Weaker, but the only signal available if each document states a
  fact one way only.
- **Subsumption `R1 ⊑ R2`.** For all ordered pairs: of the `(x,y)` with `R1`, what
  fraction also have `R2`? (AMIE-style confidence, with support.)

**The procedure is on trial, not the axioms.** The `emite` / `adoptată de` inverse
pair — spotted by eye in 3b — is **held back as a validation case**. It must be
*rediscovered*. If the procedure misses it, the procedure is wrong.

**Predictions + refutation thresholds.**
| check | predict | refute if |
|---|---|---|
| `emite`/`adoptată de` in top-5 inverse candidates | **yes** | absent → procedure invalid |
| high-confidence axioms (conf ≥ 0.5, support ≥ 8) | **3–15** | 0, or > 50 (noise, no ranking power) |
| plausibility of top-10 by inspection | **≥ 60%** | < 40% |

**Result** (run 2026-07-25, 33 candidate relations, 1,056 ordered pairs, 2.5 s):

| check | predicted | measured | |
|---|---|---|---|
| held-back pair in top-5 inverse candidates | yes | **ABSENT** | FAIL |
| high-confidence axioms (conf ≥ 0.5, sup ≥ 8) | 3–15 | **0** | FAIL |

Total output: **1 inverse candidate** (`Publicat în ~inv~ va elaborat`, score 0.25,
spurious) and **0 subsumption candidates**.

**Verdict: the PROCEDURE is refuted, exactly as pre-registered.** It missed the
validation case, so it is wrong — and the diagnosis is more interesting than the
failure.

**Why it failed — the corpus is "one fact per document per relation".**
```
instance-level overlap(emite, reversed adoptată de) = 0   of 423 × 98 edges
emite        dominant type signature: (AuthoritativeBody, NormativeAct)  296
adoptată de  dominant type signature: (NormativeAct, AuthoritativeBody)   46   ← exact reverse
```
The type signatures **do** reverse cleanly. But both of my scoring families rested
on **instance-pair co-occurrence** — inverse needed `R1(x,y)` ∧ `R2(y,x)`;
subsumption needed the same `(x,y)` under both relations — and a legal document
states a fact *one way only*. `EMITENT: X` and `adoptată de X` never appear for the
same act, so co-occurrence is **structurally zero**, which also explains the zero
subsumption candidates.

Worse, I gated even the *type-level* signal behind an "overlapping endpoint
populations" requirement (as pre-registered). Inverse relations cannot satisfy it
by construction: they are attested in *disjoint* documents, so their endpoint
populations barely intersect. The one requirement I added to make the weak signal
credible is precisely the one that made it unusable.

**The transferable lesson.** **AMIE-style rule mining assumes a dense graph** where
the same entity pair participates in many relations. A per-document extraction
graph is sparse in exactly that dimension — each pair typically carries one
relation from one sentence. So axiom discovery over this kind of graph must be
**type-level** (over class signatures), not **instance-level** (over entity-pair
co-occurrence). Choosing the standard algorithm without checking that its core
assumption holds cost this iteration.

**Cost of the diagnosis, acknowledged:** the held-back pair has now been *spent* —
having used it to debug, its rediscovery in 5b is weaker evidence. 5b must
therefore also be judged on whether its *other* candidates are plausible.

---

## Iteration 5b — type-level axiom discovery (PRE-REGISTERED 2026-07-25)

**Correction.** Drop the endpoint-population requirement and the reliance on
instance-pair co-occurrence. Score type-level inverse purely on: dominant type
signatures being exact reverses, **weighted by the support of both relations** and
by how concentrated each signature is (its share of that relation's typed edges).
Keep the instance-level signal as a *bonus*, not a gate. Add domain/range axioms
(a relation's dominant signature with its concentration as confidence), which is
type-level by nature and was missing entirely.

**Predictions + refutation thresholds.**
| check | predict | refute if |
|---|---|---|
| held-back pair in top-3 inverse candidates | yes (weak evidence now) | absent → mechanism still wrong |
| domain/range axioms at concentration ≥ 0.6, support ≥ 8 | **3–12** | 0, or > 40 |
| plausibility of top-8 across both families | ≥ 60% | < 40% |
| spurious `Publicat în ~inv~ va elabora`-type pairs in top-3 | 0 | ≥ 2 |

**Result** (run 2026-07-25, 33 relations, deterministic, no LLM):

| check | predicted | measured | |
|---|---|---|---|
| held-back pair rank | ≤ 3 | **#1** (score 0.59) | PASS |
| domain/range axioms (conc ≥ 0.6, sup ≥ 8) | 3–12 | **4** | PASS |
| plausibility of top-8 | ≥ 60% | **6/6 = 100%** | PASS |
| spurious pairs in top-3 | 0 | **0** | PASS |

```
INVERSE
  [0.59] emite        ~inv~ adoptată de   (Auth,Norm)<->(Norm,Auth)  conc .72/.52
  [0.46] adoptată de  ~inv~ hotărăşte     (Norm,Auth)<->(Auth,Norm)  conc .52/.54

DOMAIN / RANGE
  [411  conc .72]  AuthoritativeBody --emite-->          NormativeAct
  [246  conc .76]  Official          --contrasemnează--> NormativeAct
  [ 36  conc .67]  NormativeAct      --se face public--> OfficialGazette
  [ 10  conc .60]  AuthoritativeBody --se asigura de-->  AuthoritativeBody
```

**Verdict: CONFIRMED.** The corrected type-level procedure recovers the held-back
pair at **rank 1**, the spurious candidate from iteration 5 is gone, and every one
of the top six is semantically defensible — including the second inverse
(`Act --adoptată de--> Body` vs `Body --hotărăşte--> Act`: "adopted by" / "rules",
genuinely inverse) which was *not* on my radar.

**The independent rediscovery worth noting.** `NormativeAct --se face public-->
OfficialGazette` is the `publishedIn` axiom hand-written as an *illustration* in
the original proposal. Nobody encoded it; the systematic procedure found it from
36 instances at concentration 0.67. Two of the proposal's three illustrative
axioms (`issues`, `publishedIn`) were recovered from data. The third (`amends`)
was not — `modificarea şi completarea` has entropy 0.80 and fails the
concentration floor, i.e. in this corpus amendment genuinely relates many kinds of
thing, consistent with iteration 3.

**The distilled grammar, final form.** With the inverse assertions folded in, the
corpus reduces to three axioms plus their inverses — a grammar small enough to
read in one breath and every line traceable to hundreds of instances:

```
AuthoritativeBody issues NormativeAct        (inverse: adoptedBy; surface forms
                                              emite / adoptată de / hotărăşte)
Official          countersigns NormativeAct
NormativeAct      publishedIn OfficialGazette
+ an untyped `references` relation spanning 28 class pairs (correctly untyped)
```

---

## Iteration 5 — original scoping note (2026-07-25)

Per direction: **discovery first, systematic, no cherry-picking.** Enumerate all
candidate axioms over the lifted schema — inverse pairs (`R1(A,B)` vs `R2(B,A)`
with high instance overlap), subsumption (`R1 ⊑ R2`), and functional/domain
constraints — score every candidate by support and confidence, and only then look
at which ones are meaningful. The `emite` / `adoptată de` inverse pair noticed in
3b must be *rediscovered* by the procedure, not assumed; if the procedure misses
it, the procedure is wrong. To be pre-registered in full after iteration 4.

---

## Revision log
- **2026-07-25** — Iteration 4 pre-registered (the pitch-black claim, tested as
  non-inferiority); iteration 5 scoped as systematic discovery-first axiom
  induction, with the 3b inverse pair as a held-back validation case.
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
