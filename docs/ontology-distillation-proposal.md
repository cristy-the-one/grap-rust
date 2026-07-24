# Ontology Distillation — a research theme for the dark factory

> **Status:** research proposal, 2026-07-24. A candidate new *stage* of the KEE
> factory. Written to be argued with; every claim here is a hypothesis with a
> stated check, in keeping with [[dark-factory-lessons]] (the measure→check loop
> is the method).

## The problem, precisely

KEE today produces an **extensional** ontology — an *ABox*, in description-logic
terms: thousands of assertions about specific individuals.

```
ORDONANŢA nr. 72/1998        --emite-->        (its content)
LEGE nr. 145/1997            --se referă la--> LEGE nr. 49/1999
prezenta hotărâre            --contrasemnează--> Ministrul finanţelor
```

It is faithful, provenance-rich, and almost unusable as a *map*: 3,235 concepts,
418 distinct relation verbs, one node per document artifact. You cannot see the
forest; there are only trees, each named after a single leaf.

What we want is an **intensional** ontology — a *TBox*: the small set of
**classes** and **typed relations** (with domain/range) that the specifics are
instances of. The user's example is exactly right in spirit:

```
AuthoritativeBody  --issues-->      NormativeAct
NormativeAct       --amends-->      NormativeAct
NormativeAct       --publishedIn--> OfficialGazette
```

This is not a summary and not a rename. It is a **grammar**: a compact vocabulary
(classes) plus the sentence patterns allowed over it (typed relations). The
corpus becomes "sentences" in that grammar, and — crucially — every grammar rule
must be *deduced from and traceable to* the raw ontology and the KG, never
asserted by fiat.

The signal that this is real and latent is already in our data: `GUVERNUL` (224
edges), `Guvernul României` (22), `Guvern` (14) are one entity in three forms;
`fac parte integrantă din` / `face parte integrantă din` / `is part of` are one
relation in three forms (across two languages); `emite` (220), `aprobă` (101),
`hotărăşte`, `decide`, `adoptată de` are one abstract act. `GUVERNUL --emite-->
lege`, with 220 supporting instances, *is* "government issues law" — we just have
not yet said so at the type level.

## Where this sits in the field

This is **ontology learning / schema (TBox) induction** over a knowledge graph,
and it composes three well-studied sub-problems:

- **Entity typing / class induction** — grouping individuals into classes.
- **Open-relation canonicalization** — collapsing surface predicates into
  canonical relations (cf. Galárraga et al., *Canonicalizing Open KGs*; CESI).
- **Statistical schema / axiom induction** — lifting instance edges to typed
  schema assertions with support, and mining implication axioms (cf. AMIE-style
  rule mining; the "ontology learning layer-cake" of Cimiano: terms → synonyms →
  concepts → hierarchy → relations → axioms).

Two things make *our* version distinctive, and both come straight from our
lessons:

1. **Provenance-grounded abstraction.** Generic ontology learning emits an
   abstract schema and stops. Ours keeps every abstract axiom tethered to the
   instance edges that support it, and through them to the source passages. "An
   authoritative body issues normative acts" is not asserted — it is *deduced,
   with 340 supporting instances, each traceable to a document*. Falsifiable
   abstraction.
2. **Governed abstraction.** The distilled ontology is a *proposal* that passes
   the factory's gates — integrity (every component grounded in ≥K instances),
   provenance (traceable), and a **canary** (does it *help* a held-out task?).
   It is adopted or rolled back exactly like a KEE ontology. Abstraction that has
   to earn its place.

## The proposed workflow

A pipeline, so it can become a factory stage. The division of labour is our
spine: **deterministic graph analytics propose the structure; the LLM only
names; a gate curates.** The abstraction is *earned from data*, not hallucinated.

**Stage 1 — Relation canonicalization (start here; see §First experiment).**
Cluster the 418 verbs into abstract relations using three signals grap-rust makes
cheap: (a) the embedding of the verb phrase (bge-m3), (b) the **type-signature**
of the endpoints each verb connects, once Stage 2 exists, and (c) morphological
folding (the diacritic/`reconcile.fold` machinery already unifies `aproba`/
`aprobă`). Bonsai *names* each cluster with a canonical predicate + one-line
definition, from a **bounded** sample of members (the context-scaling lesson —
never the whole set). Output: ~10–20 abstract relations, each with its canonical
name, the surface verbs it subsumes, and its empirical domain/range.

**Stage 2 — Class induction.** Cluster concepts into classes from: embedding
similarity (vectors already in the KG), **structural role** (the profile of
relation types a node participates in, in/out), name morphology (`ORDONANŢA|LEGE|
HOTĂRÂRE nr. …` → NormativeAct; `GUVERNUL|MINISTERUL|BANCA NAŢIONALĂ|CASA …` →
AuthoritativeBody), and the promoted hub endpoints as high-confidence seeds. LLM
names each class + definition from a bounded sample. Output: ~20–50 classes with
members (+ provenance) and membership features.

**Stage 3 — Schema lifting + statistical support.** Map every raw edge
`inst_a --verb--> inst_b` to `Class(a) --Rel(verb)--> Class(b)`. Aggregate:
**support** = number of instance edges under each abstract triple; **purity** =
fraction of a relation's instances whose endpoints fall in the dominant class
pair. Keep triples with support ≥ τ and purity ≥ ρ. This yields the distilled
schema with quantified domain/range:

```
AuthoritativeBody --issues--> NormativeAct   [support 340, purity 0.92]
NormativeAct --publishedIn--> OfficialGazette [support 210, purity 0.97]
NormativeAct --amends--> NormativeAct         [support 55,  purity 0.88]
```

**Stage 4 — Grounding & verification (the reasoner earns its keep).** For each
abstract triple, walk down to the supporting instances → source chunks →
documents, and compute coverage/precision against the KG. An abstract component
is admitted only if the KG *supports* it. This is the augmented-audit reasoner
pointed at the schema level.

**Stage 5 (stretch) — Axiom induction.** AMIE-style mining over the lifted graph
for implication axioms — e.g. `amends ⊑ references`, `issues` has domain
`AuthoritativeBody` — turning the distilled ontology from a type graph into a
small logic that compliance rules can exploit.

## How the lessons shape it

- **Measure→check is the method, not patience** ([[dark-factory-lessons]] §Field
  data). Each stage is a stated hypothesis with a metric (below). We build the
  smallest slice first and let the number confirm or kill the direction.
- **Bounded context.** Every LLM naming call sees a *bounded sample* of a
  cluster, never the growing ontology — the exact wall we just hit and fixed.
- **Provenance / audit.** Every distilled edge carries its support set and a
  confidence; the run is audited and reproducible, like any factory run.
- **Governance.** The distilled ontology is adopted through churn/canary/rollback
  against a held-out task — abstraction is falsifiable and revertible.
- **grap-rust as substrate.** Structural-role features, support aggregation over
  thousands of edges, and coverage verification are graph-heavy — and the core is
  the fast part (a full scoring pass is ~6s). We can afford expensive analytics.

## Evaluation — each an experiment, not a vibe

**Intrinsic.**
- *Compression*: 418 verbs → M relations, 3,235 concepts → N classes. (Hypothesis:
  M ≈ 10–20, N ≈ 20–50.)
- *Coverage*: fraction of instance edges explained by an admitted abstract triple.
  (Hypothesis: ≥ 70% with support ≥ 5.)
- *Purity*: mean purity of admitted relations. (Hypothesis: ≥ 0.85.)

**Extrinsic — the real test.** Does the distilled ontology enable what the raw
one cannot?
- *Abstract compliance*: write one rule — "every NormativeAct is publishedIn an
  OfficialGazette" — at the schema level, and check it across all 3,235 outcomes
  via the lift function. The augmented-audit demo, generalized from hand-authored
  string characteristics to a *grammar*. (Hypothesis: a handful of abstract rules
  reproduce the per-document checks the current demo does one act at a time.)
- *Bounded extraction context*: feed the distilled *schema* (tiny, stable) back
  as extraction context instead of the raw concept sea. (Hypothesis: extraction
  quality holds with a context that no longer grows with the corpus — a
  principled cure for the scaling wall, and a **self-improving loop**: extract →
  distil → better context → extract.)
- *Corpus-level questions*: "which bodies issue the most acts?", "the amendment
  DAG at the type level" — answerable over the distilled graph, not the raw one.

## First experiment (de-risk in a day, measure before building)

Do **only Stage 1 + a crude Stage 2**, on the current 400-doc graph, and answer
one question: *do the verbs and hubs actually collapse into a clean, small
grammar?*

1. Embed the 418 verbs (bge-m3, batched); cluster.
2. Assign a coarse class to the ~200 highest-degree endpoints by rule + one
   bounded LLM pass (NormativeAct / AuthoritativeBody / Official / Institution /
   Article / Annex / …).
3. Lift the top-50 most-frequent edges; print the resulting abstract triples with
   support and purity.
4. **Measure:** Do the top-10 abstract triples match human intuition (e.g.
   `AuthoritativeBody issues NormativeAct`)? What is the verb-compression ratio
   and the coverage of the top-50 edges? Does `GUVERNUL/Guvern/Guvernul României`
   land in one class; do `is part of`/`face parte integrantă din` land in one
   relation?

If the top triples are sensible and coverage is high, the grammar is real and the
full pipeline is worth building. If the verbs don't cluster or the classes are
mud, we have learned — cheaply, early — that this corpus lacks a clean schema, and
we stop. Either outcome is progress; waiting for a finished pipeline to find out
is the anti-pattern the lessons warn against.

## Why this is the step from "dark" to "pitch black"

A *dark* factory runs lights-out but still has a wall: the extraction context
grows with the corpus, so at some scale a human must intervene (raise the window,
bound the context — as we just did by force). A **stable, abstract schema as
context removes the wall entirely.** The distilled TBox is small and changes
slowly, so extraction prompts stop growing with the corpus — the factory can run
*unbounded*, indefinitely, with no human ever resizing anything. That is the
step from *dark* (autonomous until it hits a scaling limit) to **pitch black**
(autonomous without one). It is the strongest reason distillation is a factory
capability and not just a reporting convenience — provided the extrinsic test
below confirms the schema is genuinely good extraction context.

## Becoming part of the factory

The end state is a governed **distillation stage**: it runs after adoption,
proposes a distilled ontology as a versioned artifact with provenance, passes the
canary against the abstract-compliance held-out task, and is adopted or rolled
back. Its outputs feed two consumers — the human (a navigable map over the sea)
and the machine (bounded, abstract extraction context that closes the
self-improving loop). The raw ontology stays the ground truth; the distilled one
is the grammar we read it through.

## Risks & open questions

- **Granularity control.** Classes can collapse too far (everything is a
  "Document") or not enough. What sets the right level — a target compression, a
  stability criterion across corpus growth, human anchors?
- **Language drift at the type level.** The corpus is Romanian with EN leakage;
  the abstraction should *unify* `is part of` with `face parte integrantă din`,
  which is a feature — but the class/relation *labels* need a language policy.
- **Support thresholds vs long tail.** Rare-but-real relations (a relation seen
  3× that is genuinely distinct) vs noise. The purity/support gate must not erase
  the informative long tail.
- **Circularity of the self-improving loop.** If the distilled schema guides
  extraction, does extraction then merely confirm the schema? The held-out task
  and provenance grounding are the guard, but this needs watching.
- **Evaluation ground truth.** We have no gold TBox for Romanian law. Extrinsic
  task performance (abstract compliance, corpus QA) is the honest yardstick;
  intrinsic metrics only triangulate.

Related: [[dark-factory-status]], [[dark-factory-lessons]].
