# The canary noise floor — open investigation

> **Status:** OPEN. Tracked 2026-07-25 after the 650→700 autonomous run.
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

## The decisive experiment (next step)

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
