# The canary noise floor — investigation

> **Status: RESOLVED 2026-07-26.** Cause is **index pollution**, the original
> candidate A. Adoption projects the ontology *into the evidence KG*, taking it
> from 4,692 to 23,170 nodes — **real evidence is 20% of what BM25 ranks over**.
> The canary baseline scores a clean index (reloaded from disk) and the candidate
> scores the polluted one, so the canary has been comparing two different
> corpora. Fix: project the ontology into a separate graph. See *Resolution*.
> Everything above that section is the investigation as it happened, including
> two of my own hypotheses that the evidence killed.

> **Superseded status:** OPEN, but narrowed. E0 executed 2026-07-26 (see below): the canary
> *baseline* run is the outlier, not the candidate; three hypotheses refuted; a
> candidate mechanism identified (not yet demonstrated) with a one-line test to
> settle it. The obvious fix is unsafe as first written — see the correction.
> Tracked 2026-07-25 after the 650→700 autonomous run.
> **Impact:** the canary cannot currently act as a fine-grained regression gate.
> Mitigated (not fixed) by calibrating `canary_max_new_gaps` to the measured
> floor, so it functions as a gross-failure detector. See
> [[dark-factory-lessons]] Theme VI (governance is a control system) and Theme
> VIII (the instrument is the experiment).

## The symptom

Every adoption produces ~830 "new gaps" attributed to the candidate ontology,
which the canary reads as regressions and rolls back. Measured over four
consecutive batches of the 650→700 run:

| batch | churn | canary | regressions | growth | outcomes added |
|---|---|---|---|---|---|
| 1 | ok | failed | 816 | 0 | +20 |
| 2 | ok | failed | 820 | 0 | +19 |
| 3 | ok | failed | 838 | 0 | +21 |
| 4 | ok | failed | 844 | 0 | +18 |

**830 ± 12 (sd 12).** The stability is the loudest clue: batches with quite
different documents produce nearly identical counts, which is the signature of
something systematic and content-independent rather than semantic regression.

## Established facts

1. **Scoring is deterministic.** Two consecutive `reasoner.detect_gaps` runs over
   identical inputs produced 710 gaps each with **symmetric difference 0**. The
   floor is not nondeterminism.
2. **All flips are on pre-existing outcomes** (`growth = 0` in every batch), so
   they are not gaps on newly added outcomes.
3. **The ontology barely changes.** Diffing the true active state against the
   proposal: 89 outcomes added, **302** `(outcome, characteristic)` pairs added
   (84 on existing outcomes, 218 on new ones). ~300 content changes cannot
   directly explain ~830 flips.
4. **Mapping hints barely change.** Only **10** of 3,881 shared outcomes had
   different hint keywords.
5. **The retrieval index is only ~53% evidence.** The evidence KG holds 4,042
   nodes of which 2,124 are document chunks; the remaining 1,918 (47%) are
   non-evidence nodes.

## Hypotheses falsified

| hypothesis | test | verdict |
|---|---|---|
| Growth mislabelled as regression (the `_baseline_outcome_ids` partition is wrong or inert) | called the helper directly on the live hash | **falsified** — returns 3,892 ids correctly; the fallback path is not taken |
| Mapping-hint variance changes retrieval queries for unchanged characteristics (pilot finding 15) | diffed hint keyword sets between active and proposal | **falsified** — only 10 outcomes affected |
| Gap scoring is nondeterministic | two identical runs, compared gap-target sets | **falsified** — symmetric difference 0 |

## Remaining candidates (untested)

Both turn on the fact that **adoption mutates the very graph gap detection scores
against** — `_adopt_record` projects the ontology into `_state.kg`, and the
canary's post-adoption run therefore sees a *different KG* than its pre-adoption
baseline, independently of the ontology's content:

- **A. Projection perturbs BM25 statistics.** Adding projected nodes shifts idf
  for every term, so the top-k retrieved for an *unchanged* characteristic can
  change, flipping token coverage across the threshold. Excluding projected nodes
  from *results* (which `_is_projected` does) does not undo their effect on the
  *index*.
- **B. Node-id instability across projection.** Projected-node exclusion is
  primarily by node id (`exclude_nodes`), and `_project()` deletes and re-adds
  nodes, so ids are reassigned. A stale exclusion set would exclude the wrong
  nodes. The reasoner has label/text-prefix guards as a "timing-robust secondary
  guard", which may or may not fully cover this.

## Stronger evidence: it shows up in PRODUCTION runs too (2026-07-25)

The autonomy pass gave a cleaner instance than any canary measurement, because it
compares two ordinary **production** gap runs with no canary machinery involved,
across one adoption:

| run | gaps | characteristics | gap rate | self-coverage |
|---|---|---|---|---|
| run-4fd5a5 (pre-adopt) | 919 | 11,723 | 0.0784 | 92.2% |
| run-236686 (pre-adopt) | 919 | 11,723 | 0.0784 | 92.2% |
| **run-5fdff4 (post-adopt)** | **1,467** | 12,064 | **0.1216** | **87.8%** |

Adoption added 106 outcomes (+2.7%) and 341 characteristics (+2.9%). Gaps rose
**60%**. Normalising by ontology size does **not** explain it: the *rate* ratio is
still **1.55×**, and self-coverage drops **4.3 points**. Two consecutive
pre-adoption runs are identical (919, 919), so the metric is stable until an
adoption happens.

**Consequence for the alarms.** The `gap spike` alarm is therefore *correct* to
fire, and must not be normalised away — it is reporting a real, unexplained
degradation. The factory ending `HEALTHY=False` after an autonomous adoption is
the honest outcome, not a bug to be tuned out. Only the `gap_regression` alarm
needed fixing, and only for the part that genuinely was growth (gaps on outcomes
that did not exist at the baseline run).

This also sharpens the decisive experiment below: the phenomenon reproduces
without the canary, so the experiment can be run purely with production gap runs.

## E0 executed (2026-07-26) — the baseline run is the outlier

Run offline against the verified snapshot `_backup-20260726-0226`, resolving each
run's `explanation_hashes` through the content-addressed object store. No GPU, no
mutation, nothing touched in the live pilot. **The persisted explanations carry
the retrieval query, the retrieved node ids, their scores and their per-hit
coverage** — enough to answer most of this without re-running anything.

Take two runs with the **same ontology hash, same scope, same weights**, 32
minutes apart:

| run | kind | when | gaps |
|---|---|---|---|
| run-d945d462 | Production | 01:16 | **1,579** |
| run-00173908 | canary baseline | 01:48 | **824** |

Diffed by target: 731 shared, **848 flagged only by production**, 93 only by the
baseline. So the canary's "~830 new gaps attributed to the candidate" is not
adoption adding gaps at all. **The candidate run (1,615) is consistent with the
previous production run (1,579). It is the *baseline* that is anomalously low**,
and the canary subtracts it from a normal number and reports the difference as
regression. We have been reading a broken reference, not a damaged subject.

### Three hypotheses refuted, offline

| hypothesis | test | verdict |
|---|---|---|
| Node-id instability across projection (candidate B) | 737 nodes retrieved by both runs, compared id → snippet text | **falsified** — 0 mismatches |
| The baseline sees the projected ontology as its own evidence (self-coverage) | inspected the 471 nodes only the baseline retrieved | **falsified** — all ordinary document chunks (legal text), no ontology text |
| Production applies a leave-one-out exclusion of an outcome's own source chunks | self-match score of dropped vs kept hits | **falsified** — dropped hits are *less* self-matching (0.28 vs 0.44) |

### What is actually different

- **Retrieval depth.** Production returns 2–3 hits where the baseline returns 5;
  906 baseline hits are absent from production's results. Production is
  *excluding more nodes*.
- **Scores move, slightly.** Of 2,433 common (target, node) score pairs, 67.8%
  are bit-identical and 32.2% move — median ratio 1.0018, full range 0.947–1.044.
  So the index is not static between the runs, but the perturbation is ±5%.
- **Coverage is not piled against the threshold.** Max coverage per gap target:
  652 of 1,579 sit at 0.00–0.05 (true misses) and only ~85 lie within 0.10 of the
  0.6 cut. Threshold adjacency can explain a minority of flips, **not** ~830 —
  worth stating because it was my first hypothesis and the data does not support
  it.

### The mechanism this points to (not yet proven)

Both call sites pass `exclude_nodes=getattr(_state, "projected_nodes", set())`
(`server.py:2380`, `server.py:2425`). That attribute is assigned a fresh `set()`
**only** when a KG is created or a workspace opened (`:87`, `:119`, `:137`,
`:338`). Every adoption, rollback and ontology load instead does
`.update(...)` (`:1021`, `:1138`, `:2288`, `:2326`) — the set only ever grows.

Within a single batch the ordering is fixed: the canary baseline runs **before**
`_adopt_record` updates the set, and the production run **after**. The two runs
therefore *provably* score with different exclusion sets, one of them larger by
a whole projection. A larger exclusion set removes candidate evidence from
retrieval → shallower hit lists → lower coverage → more gaps. That is precisely
the asymmetry measured above, in the right direction and roughly the right size,
and it explains what the refuted hypotheses could not: the dropped nodes are
ordinary document chunks with no semantic relationship to the target, because
they are being excluded **by id, not by meaning**.

It also explains two things previously filed as coincidence: two consecutive
pre-adoption runs are identical (919, 919 — no adoption between them, so no
update), and the floor creeps upward as the ontology grows (818→863 over the 750
run, 822→868 over the 800 run).

### The floor is a sawtooth — measured in production (2026-07-26)

The 850-doc run supplied the confirmation without any code change. Across three
consecutive runs of five batches each, the canary floor **rises monotonically
within a run and drops at every process boundary** — while the ontology only ever
grows:

| run | batch 1 → batch 5 floor | outcomes at batch 1 |
|---|---|---|
| 750 | 818 → 863 | 3,998 |
| 800 | 822 → 868 | 4,097 |
| 850 | 824 → 843 | 4,222 |

At the two boundaries:

| boundary | outcomes | floor |
|---|---|---|
| 750 b5 → 800 b1 | **+8** | **−41** |
| 800 b5 → 850 b1 | **+30** | **−44** |

If the floor were a function of ontology size it could only go up. It drops by
~40, twice, at exactly the two points where a new `loop_driver.py` process opens
the workspace and `_state.projected_nodes` is reset to `set()`. **The floor
tracks the number of adoptions since process start, not the size of the
ontology** — roughly +9 to +11 per adoption within a run, reset on restart.
Nothing else in the system has that shape, and the accumulating exclusion set is
the only piece of state with that lifetime — but see the correction below: that
is a strong association, not a demonstrated mechanism.

### What the sawtooth does and does not establish (correction, same day)

The sawtooth is solid and replicated: the floor depends on **how many adoptions
have happened in this process**, and resets when the process restarts. That much
is measured.

It does **not** establish the causal path I attached to it. Two further offline
checks weakened that step:

- **No projected node appears in either run's results.** Across all 1,138
  distinct nodes retrieved by the two runs, **zero** carry the `[ASPICE ...]`
  projection marker. Exclusion is working correctly in *both* runs, so the
  difference between them is not ontology text leaking in as evidence.
- **The dropped nodes show no id clustering.** The 249 nodes production dropped
  are spread across the whole id range roughly in proportion to the nodes it
  kept — not concentrated in a band, as a stale-id-collision story would predict.

So: process lifetime changes the floor, and the exclusion set is the only piece
of state with that lifetime — but the mechanism connecting them is not yet
demonstrated. `excluded_nodes` in the run summary (`79258f9`) is what closes it.

**Re-reading the two checks above (same day, correcting the correction).**
`_is_projected` (`reasoner.py:90`) applies **three** guards, not one: the
`exclude_nodes` id set, a text-prefix guard (`[ASPICE outcome]`,
`[characteristic]`, `[PG Step]`), and a label-prefix guard (`kg:aspice.`,
`pg-step-`). The id check runs first and is unconditional — it never verifies
that the node really is projected.

That structure explains the "zero ASPICE text in results" finding without
exonerating the id set: genuine projected nodes are caught by the text and label
guards *whatever* the id set contains. Which yields a sharper statement:

> Because genuine projected nodes are suppressed by the prefix guards anyway,
> the **only** way `exclude_nodes` can change a result is by dropping a node that
> is *not* a projected node. Any difference in results attributable to the id set
> is, by construction, **real evidence wrongly discarded**.

It also explains why the dropped nodes are ordinary legal chunks spread across
the whole id range — chunk ids are spread across that range. Both observations
now support the mechanism rather than weakening it. The direct test is unchanged
and still pending: compare `excluded_nodes` between a batch's baseline and
production run.

### Costing the "separate graph" option (2026-07-26)

Two measurements say the ontology's presence in the *evidence* KG buys gap
detection nothing:

- `pygrap.IndexedKnowledgeGraph` has **no `has_characteristic` method**, so
  `_structural_signals`' `hasattr` guard always fails and it returns `[]`
  unconditionally.
- Across **4,070 persisted explanations** from three runs: **zero** carry a
  structural signal. Confirmed empirically, not just from the code.

So projected ontology nodes are never read as structural evidence, and are always
suppressed as lexical evidence. What they *do* is enlarge the index (perturbing
BM25 statistics for everything else — candidate A) and force the `exclude_nodes`
plumbing that is the suspected cause of the floor. Projecting the ontology into
its own graph removes both at once and deletes the stale-id hazard by
construction, rather than managing it.

**Not yet checked:** whether any *other* consumer (demos, `blast_radius`,
neighbour queries, the augmented-audit demo) relies on ontology nodes living in
the evidence KG. The gap path does not; that has to be established for the rest
before the change is scoped as cheap.

### The fix is not "replace instead of union" (do not apply as first written)

An earlier draft of this document recommended making `projected_nodes` describe
the current projection. That is **unsafe**, for two reasons found by reading the
producers:

1. **Two different producers feed the set.** Ontology projection
   (`ontology.py:89-114`, at adopt/rollback/load) and *procedure graph*
   projection (`procedure_graph.py:229`, at PG load) both `.update()` it.
   Replacing on adoption would silently un-exclude every PG step node.
2. **Old projected nodes are never deleted from the KG.**
   `project_ontology_to_grap` only ever calls `kg.add_node`. Nodes from earlier
   projections remain in the graph, so they must remain excluded. Replacing the
   set would make stale ontology text retrievable as evidence — the
   self-coverage failure, introduced by the fix meant to prevent it.

Any real fix has to track *which* projection each id came from, or exclude
projected nodes from the **index** rather than from the results. That is a
larger change than one line, and it invalidates the 1,200 calibration, so it
needs a decision rather than a patch.

**A correction to an earlier projection.** After the 800 run I noted the floor
creeping (818→863→868) and warned it would reach the 1,200 budget around
2,500–3,000 documents. That was wrong, because it read a sawtooth as a trend.
The *run-start* floor moves only +3 per 50 documents (818 → 822 → 824), which
would not reach 1,200 within this corpus at all. The real exposure is different
and more specific: a run with **many batches in one process** accumulates ~10 per
adoption, so a 40-batch run would cross the budget from process lifetime alone,
with no change in the data. Until `projected_nodes` is fixed, keep runs to ≲20
batches — or restart the process between them, which is what the current 5-batch
driver has been doing by accident.

### The one-line test that settles it

Record `len(exclude_nodes)` in each run's summary alongside `scope` and
`weights`. If the canary baseline and the production run of the same batch report
different sizes, this is confirmed and the fix is to make `projected_nodes`
describe the *current* projection (replace, not union) — and, separately, to
decide whether gap scoring should exclude projected nodes from the **index**
rather than from the **results**, since excluding them from results still lets
them shift BM25 statistics.

This supersedes the "decisive experiment" below as the cheapest next step: it
needs no snapshot, no re-projection, and no factory downtime.

## The decisive experiment (superseded, kept for reference)

Isolate projection from ontology change:

1. Snapshot the KG and workspace (never run this against a live pilot).
2. Run gap detection → gap set **G₀**.
3. Re-project the **same, unchanged** ontology (a no-op semantically).
4. Run gap detection again → **G₁**.

`|G₀ △ G₁|` is then the flip count attributable to **projection alone**, with the
ontology held constant. If it is ~830, candidate A/B is confirmed and the fix is
architectural: gap scoring must be invariant to projection — either project into a
separate graph, or exclude projected nodes from the index rather than from
results.

If it is ~0, projection is exonerated and the cause is in how the baseline and
canary runs differ (scope, weights, or the `exclude_nodes` set passed to each).

**Do not run this while a factory loop is in flight** — step 3 mutates the live
KG. That is why it was deferred rather than attempted during the 650→700 run.

## Mitigation in force

`canary_max_new_gaps = 1200` in `pilot-law-ro/loop_driver.py` — above the measured
830 ± 12 floor with margin. This makes the canary a **gross-failure detector**,
not a regression gate: it will catch a catastrophic adoption but not a subtle one.
That is a deliberate, documented downgrade, and it is calibrated to a *measured*
baseline rather than guessed — the discipline from Theme VIII, which was learned
by setting a "violations ≤ 10%" threshold against an unmeasured 50% baseline.

## Why this matters beyond the canary

The self-coverage gap metric is used for more than the canary: it drives
`detect_gaps`, the `gap_regression` alarm, and the pilot's headline
"self-coverage" figure (93.9% at 650 docs). If ~830 of ~717–850 gaps can appear
or vanish from a projection rather than from evidence, then **the absolute gap
count is a far softer number than it looks**, and only *large* movements in it
should be treated as signal.

---

## Resolution (2026-07-26) — index pollution, confirmed by telemetry

`79258f9` added `excluded_nodes` and `kg_nodes` to every persisted run summary.
The 900→950 run produced 15 instrumented runs, and the first three lines answer
the whole question:

| role | gaps | kg_nodes | excluded_nodes | evidence share |
|---|---|---|---|---|
| canary baseline | 885 | **4,692** | 0 | 100% |
| candidate | 1,648 | **23,170** | 18,478 | **20.3%** |
| production | 1,648 | 23,170 | 18,478 | 20.3% |

`23,170 − 4,692 = 18,478`, exactly the exclusion-set size. So:

**Adoption projects the ontology into the evidence KG and quintuples it.** The
graph is reloaded from disk at the start of each batch (~4,700 real evidence
nodes); the canary baseline runs against that clean index; adoption then adds
18,478 ontology nodes in memory; the candidate and production runs score against
an index in which **only 20% of the nodes are evidence**.

The canary has not been detecting regression. It has been **comparing two
different corpora** — a clean one and one that is four-fifths ontology
scaffolding — and reporting the difference as the candidate's fault.

### Why every earlier observation now fits

Excluding projected nodes from *results* does not stop them being ranked. The
top-k is computed over the polluted index and the ontology nodes are filtered
out **afterwards**, so a top-5 that is 80% ontology returns 1–2 real documents.

- *Production returns 2–3 hits where the baseline returns 5.* Its top-k is being
  eaten by ontology nodes and filtered after the fact — retrieval is starved by
  post-hoc filtering, not by exclusion of the wrong ids.
- *The dropped hits are ordinary documents, spread across the id range.* They
  were displaced out of the top-k by projected nodes, not excluded.
- *No `[ASPICE …]` text ever appears in results.* The filter works; that was
  never the problem.
- *Scores shift ±5% on common nodes.* idf computed over a 4.9× corpus.
- *The floor is stable and content-independent.* It is a property of index
  composition, not of the documents in the batch.
- *It rises within a run and resets at process restart.* Each adoption adds
  ~160–190 more projected nodes; reloading the KG from disk restores the clean
  index.

### Two of my own hypotheses that this kills

- **Stale ids wrongly excluding real evidence.** Dead. `excluded_nodes` tracks
  the projection exactly, and projected ids are allocated *above* the evidence id
  range (4,692+), so applying a stale set to a freshly reloaded 4,731-node graph
  is a harmless no-op. That is also why the baseline's carried-over set does no
  damage.
- **Threshold adjacency.** Already refuted by the coverage histogram; it plays no
  part.

Candidate A — stated in the first version of this document and then set aside in
favour of more elaborate stories — was right all along. It was dismissed because
the effect was imagined as a subtle idf perturbation. It is not subtle: the index
quintuples.

### The consequence that matters more than the canary

**Every production gap run has been scoring against an index that is 80%
ontology scaffolding.** The headline self-coverage figures come from those runs.
On a clean index the same ontology reports 885 gaps instead of 1,648 — so the
factory's real self-coverage is materially better than it has been reporting,
and the "gap spike" alarm after each adoption was measuring pollution, not
degradation.

This also retires the note under *Established facts* that "the retrieval index is
only ~53% evidence". At 950 documents it is **20%**, and falling as the ontology
grows faster than the corpus.

### The fix

Project the ontology into its **own graph**, leaving the evidence KG pure. Costed
in the section above: gap detection loses nothing (no structural signals exist),
PG reference resolution degrades to warnings while the authoritative check reads
the in-memory ontology, and the `exclude_nodes` plumbing becomes unnecessary for
ontology nodes.

**It invalidates the 1,200 canary calibration.** With a clean index on both
sides, baseline and candidate become comparable and the floor should largely
disappear. The budget must then be re-measured, not guessed — the same discipline
that set 1,200 against a measured 830.

---

## Outcome of the fix (2026-07-26, 950→1000 run)

First run with the ontology in its own graph. Both pre-registered predictions
held.

**1. The floor collapsed.**

| run | canary "new gaps" per batch |
|---|---|
| 900→950 (polluted index) | 842, 851, 876, 889, 888 |
| **950→1000 (clean index)** | **0, 2, 5, 4, 0** |

**2. Gaps fell by half, as *resolved*, not as churn.** Batch 1 reported
`new=98, resolved=1028` and the total went **1,880 → 950**. The ~870 that
disappeared are the pollution. Subsequent batches settled at 964 → 1,007 as the
corpus grew normally.

**3. The two sides of the canary now score the same corpus.** From the run
summaries: baseline and candidate both report `kg_nodes=5003`,
`excluded_nodes=0`. Before the fix those columns read 4,692 / 0 for the baseline
and 23,170 / 18,478 for the candidate.

So the canary is a working fine-grained regression gate again, for the first
time. After batch 1's one-off re-baseline (84 regressions as the index changed),
per-batch regressions were 11, 17, 13, 3 — small enough to mean something.

### The budget is now the open item

`canary_max_new_gaps = 1200` was calibrated against the polluted floor and is
meaningless at a measured floor of 0–5. It is loose in the *safe* direction, so
nothing is at risk, but the gate is not gating.

**Recommendation: do not tighten on this run alone.** Batch 1 contained a
one-time re-baseline transient (84 regressions) that a tight budget would have
caught as a failure, and one run cannot distinguish a stable floor from a quiet
one. Run 1000→1050 at the current budget, confirm the 0–5 floor holds across a
second run, then set the budget from **two** runs of evidence — the same
measure-then-set discipline that produced 1,200, applied to a floor that is now
worth measuring.
