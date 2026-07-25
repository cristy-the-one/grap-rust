# The canary noise floor — open investigation

> **Status:** OPEN, but narrowed. E0 executed 2026-07-26 (see below): the canary
> *baseline* run is the outlier, not the candidate; three hypotheses refuted; a
> concrete code-level mechanism identified with a one-line test to confirm it.
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
