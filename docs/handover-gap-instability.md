# Handover: the adoption-coupled gap instability — diagnosis plan & strategy

> **For:** Opus5 (implementation owner). **From:** review session 2026-07-25.
> **Scope:** analysis, experiment plan, fix strategy, and work queue. No code
> here by design — procedures are specified declaratively; you own the
> implementation choices.
> **Prime directive:** run **E0 first**. It requires no hypothesis, no LLM, no
> mutation, and the data for it is already persisted. Do not build a fix before
> E0's decision tree has spoken — three theories have already died to single
> probes in this investigation, and two more were weakened tonight.

---

## 1. My read of what is happening

The pipeline has two halves, and only one of them is sick.

**The production half is sound.** Extraction (names-mode context), dedup,
checkpointing, endpoint promotion, quality telemetry: all measured healthy over
two consecutive zero-failure runs (650, 700 docs). The knowledge graph itself is
consistent (`integrity: ok` at every check) and extraction shape is
content-responsive (relCV 0.5–0.86, no template collapse, no vocabulary
collapse).

**The measurement/governance half has one systemic defect expressed through
three instruments.** The canary "noise floor" (~830 ± 12), the production gap
jump (919 → 1467 across a single adoption), and the historical self-coverage
wobble are the *same phenomenon* observed through three different dashboards:

> **Adoption mutates the state that gap-scoring reads.** Scoring is
> bit-deterministic while that state is constant (919, 919 on consecutive runs;
> symmetric difference 0 on repeat runs). The moment an adoption occurs —
> projection into the evidence KG, a new ontology object, a new exclusion set —
> ~7% of characteristics flip, stably and content-independently.

The 7% is the tell. 830/11,723 ≈ 7.1%; the floor drifted 816 → 857 as the
ontology grew. A constant *fraction* flipping — regardless of which documents
were ingested — is the signature of a **threshold-marginal population** being
pushed across a scoring boundary by an environmental change, not of semantic
regression in the ontology.

**What the flips are NOT (already falsified, do not re-investigate):**

| ruled out | by |
|---|---|
| scoring nondeterminism | two identical runs, symmetric difference 0 over 710 gaps |
| growth mislabelled as regression | `_baseline_outcome_ids` verified live (3,892 ids); growth partition now in both canary and gap_delta |
| mapping-hint *content* drift on shared ids | only 10 of 3,881 shared outcomes changed hint keywords |
| net-improvement alarm semantics | fixed (`new−growth > resolved`), tested |
| corpus dirt | full 2,092-file audit: zero junk |

**What was weakened tonight (my own probes, so treat as evidence, not gospel):**
the naive "projected nodes crowd the top-k" story. Direct text queries against
the live index found **no** `[ASPICE outcome]` / `[characteristic]`-prefixed
nodes, and a verbatim outcome-name query returned only document chunks. Also the
node arithmetic contradicts naive projection: the evidence KG holds 4,042 nodes
(2,124 chunks + **1,918 unidentified others**) while a full projection of the
active ontology would be ~15,600 nodes. Whatever projection does to this index,
it is not "one text-indexed node per outcome/characteristic."

## 2. The surviving candidate channels, ranked

**C1 — Query composition changed by adoption (my current favourite).** The
reasoner builds each characteristic's query as `characteristic + readable form +
hint_for(outcome_id).keywords`. Adoption swaps the ontology *object*: outcome
**ids are content-derived and rename**; hints are keyed by outcome id; outcomes
can gain hints they previously lacked. Any of these changes the *query string*
for a characteristic whose text never changed → different retrieval → coverage
flip. The 10-hint diff that "falsified" hint drift only compared shared,
unrenamed ids — it did not test hint *presence* changes or renamed-id
repointing. Consistent with: content-independence, adoption-coupling, ~7%
marginal band, determinism between adoptions.

**C2 — Exclusion-set instability.** `exclude_nodes` is an id set held in
process state; rehydration deliberately does not rebuild it; `_project()`
reassigns node ids. Runs in different processes (or before/after an in-process
adoption) can exclude different node sets, changing which retrieved hits
survive filtering. Consistent with adoption-coupling; must predict *hits*
differ while queries match.

**C3 — Index composition change from projection.** The 1,918 non-chunk nodes
are unidentified. If adoption adds/merges/re-embeds nodes in the searched
index, BM25 idf and vector neighbourhoods shift for unchanged queries. My
probes made the crude version unlikely but did not identify the 1,918 — a
census is required regardless, because *whatever they are, they are 47% of the
search space gap-scoring runs in*.

**C4 — Frozen-weights / activation drift.** Runs use
`activation.frozen_weights` when a production activation exists, else config
defaults. If pre/post runs resolved different weight sets, thresholds move.
Cheap to check in E0 (weights are recorded in run summaries).

## 3. The experiment ladder — pre-registered, cheapest first

**E0 — Diff the persisted explanations (zero mutation, zero LLM, ~minutes).**
The workspace already holds matched forensic records:
`run-23668600` (pre-adoption, 919 gaps, 919 explanations) and `run-5fdff41d`
(post-adoption, 1,467 gaps, 1,467 explanations). Every explanation persists the
target, the exact **query string**, and the **top-5 hits with node ids, scores,
snippets**.

Procedure: compute the flipped-target set (present post, absent pre — and the
reverse). For every flipped target that exists in both eras' *outcome space*,
compare its recorded query and hits across eras. Classify each flip:

| observation on flipped targets | verdict | next |
|---|---|---|
| query strings differ | **C1 confirmed** | fix = query pinning (below); measure share of flips explained |
| queries identical, hit node-ids differ | C2/C3 | run E1 census |
| queries and hits identical, coverage/score differs | C4 | audit frozen weights per run |
| target's outcome id absent pre (renamed) | rename accounting | fold into C1; quantify |

Pre-register: **≥70% of flips fall in one class** (prediction: the C1 row).
Refute-my-read if flips are evenly smeared across classes.

Also in E0, for free: confirm both runs' recorded weights are identical (kills
or convicts C4 immediately), and quantify how many flipped targets belong to
renamed outcomes.

**E1 — Index census (read-only).** Enumerate all 4,042 nodes in the live
evidence KG with label + text-prefix + embedding-presence; classify chunk vs
non-chunk; identify the 1,918. If the core lacks a node-enumeration API, adding
one is justified on its own (observability of the search space). Answer: what
are they, which adoption generation created them, are they text-indexed,
vector-indexed, or both?

**E2 — Single-characteristic microscope (read-only).** Take 3 flipped targets
from E0's biggest class. Re-run the lexical-signal path live with (a) k=5 vs
k=50, (b) with and without the exclusion set, (c) query built with vs without
hints. One afternoon; converts the E0 statistics into a mechanism you can watch.

**E3 — Adoption replay on a disposable copy (mutating, offline).** Copy `kg/` +
`ws/` to a scratch area (note: **current backups do NOT include these** — see
§6). Re-adopt the *already-active* ontology (a semantic no-op) and re-score.
Flip count ≈ 0 exonerates projection entirely (pointing everything at C1/C2);
flip count ≈ floor convicts the projection/index channel. This is the original
"decisive experiment," now cheaper because E0 will have told you what to look
at.

**Method rules (from the lessons doc, non-negotiable):** pre-register
predictions and refutation thresholds before each experiment; measure baselines
before setting thresholds; pair every count with a shape statistic; never run
mutating experiments against the live pilot; when you add a metric, **test the
path, not just the producer** (three producer-wired/path-missing incidents this
session).

## 4. Fix strategy (post-diagnosis; do not start before E0)

**S1 — Query pinning (if C1).** A characteristic's retrieval query must be a
function of the *characteristic*, not of the ontology object's identity graph:
derive queries from characteristic text alone, or key hints by stable content
hash rather than outcome id, or freeze the query string into the persisted gap
record and reuse it for delta comparisons. Smallest fix with the largest
stability payoff; also makes gap deltas meaningful across adoptions by
construction.

**S2 — Evidence/ontology search separation (if C2/C3, and desirable anyway).**
Gap scoring should run over an **evidence-only search space**: either project
ontology/PG scaffolding into a separate graph, or give the core a query-time
label filter (evidence chunks already carry `doc:` labels; `required_properties`
exists in the search API surface — check whether the Rust core can filter on it,
and add it if not; it is our codebase). This eliminates the exclusion set, the
id-instability class, and the idf question in one move, and it is the
architecturally honest end-state: *the ontology should never compete with its
evidence.*

**S3 — Alarm/threshold recalibration (after S1/S2).** Once gap counts are
adoption-invariant, re-tighten: `canary_max_new_gaps` down from 1,200 toward the
true regression rate; keep the (now partitioned) net-regression alarm; keep the
gap-spike alarm absolute — it correctly caught a real 4.3-point coverage drop
and must not be normalised away until the mechanism is fixed and the number is
trustworthy.

Acceptance criterion for the whole fix: **re-adopting an unchanged ontology
produces zero gap flips**, and a normal incremental adoption's flip count is
explained by its content diff (growth + genuine regressions only).

## 5. Reinterpretation of historical numbers (record, don't re-litigate)

All absolute self-coverage figures (94.7% @ 49 docs, 93.9% @ 650, 92.2% → 87.8%
across the last adoption) are **index-composition-dependent** and comparable
only within a constant-adoption window. Trends *between* adoptions are
trustworthy (bit-identical repeats prove it); levels *across* adoptions are not,
until S1/S2 lands. The augmented-audit demo searches the same evidence KG; its
PASS/GAP verdicts were grounded in real chunks and are safe, but re-validate it
once the index changes shape under S2.

## 6. The rest of the queue (prioritized, with my reasoning)

1. **E0 → E1/E2 → fix per §4.** Everything else in governance waits on this;
   it is the last barrier to a green autonomous round.
2. **Backup coverage gap (quick, do immediately):** the backup script captures
   metrics + working-graph DBs but **not `kg/` (evidence index) nor `ws/`
   (workspace/audit trail)** — the two things E3 needs and the two things a
   disk loss would actually hurt. Extend it; the sqlite online-backup pattern
   already in the script applies to `ws` stores too if applicable, plus plain
   directory copies for `kg/` taken while no run is writing.
3. **Chronic parse-failure quarantine:** the same 2 chunks fail JSON parsing on
   every retry (`parsefail=1.0` on the retry-only pass) and will retry forever.
   Add a per-chunk failure count to the manifest; after N failures, park the
   chunk with reason `unparseable` (visible in the queue, like parked
   relations). Identify the current offenders and check whether they are the
   repetition-degeneration documents.
4. **Layout-run normalization — TESTED AND REJECTED (2026-07-25).** Do not
   implement. A/B extraction on the five biggest-saving (most table-heavy)
   chunks, same prompt/model/parser, raw vs cleaned:
   **outcomes 0.65x, characteristics 0.56x, relations 0.78x** — cleaning loses
   44% of characteristics. Mechanism: collapsing runs of spaces destroys the
   column alignment that DELIMITS TABLE CELLS, so adjacent cells fuse and the
   model stops seeing them as distinct data points (`17126.json` 1999->490 chars,
   24->8 characteristics; `18903.json` 1896->162 chars, 1->0 outcomes). The
   padding is load-bearing, not noise. Artifacts:
   `pilot-law-ro/distillation/cleaning-test/`, driver `test_cleaning.py`.
   *Untested refinement, if the ~7.5h is ever worth chasing:* drop box-drawing
   and dot leaders only (pure noise) while converting space runs to an EXPLICIT
   delimiter (e.g. ` | `) so cell boundaries survive rather than vanish. Would
   need its own A/B before adoption.
5. **Arm the rate-budget in default policy:** `max_outcomes_added_per_chunk=3.0`
   proved itself; it currently lives only in the loop driver's per-run policy.
   Decide whether it belongs in `DEFAULT_ADOPTION_POLICY` (with `enabled` still
   off by default).
6. **Backpressure (design item):** ingestion continues while adoption is
   blocked, growing the working-graph-vs-active divergence. With the cascade
   fixed this is less urgent, but the factory still has no mechanism to slow
   intake when governance is rejecting. Design note, not urgent code.
7. **The 502 KB outlier** (`50956.json`, ~135 chunks, form-heavy): watch it on
   ingestion; it is legitimate law, not junk.

## 7. One meta-observation for the record

The theory-after-theory pattern in this investigation was not waste — each
falsification was cheap and narrowed the space (nondeterminism, bookkeeping,
hint content, naive crowding: all dead in under an hour of compute total). But
the decisive asset was sitting in the audit trail the whole time: **matched
per-gap explanations on both sides of the adoption, with queries and hits
persisted.** The system already records why it did what it did; the discipline
is to *read the record before theorizing past it*. E0 is that discipline made
into a procedure.
