# Real-Corpus Pilot Report: Romanian Legal Acts

2026-07-20. First confrontation of the dark factory with real data:
10 documents (7 act types, ~39 KB text) sampled from the 2,094-doc
`real-data-law-ro` corpus, sized so one cycle stays interactive.
Toolchain: Ternary-Bonsai-27B (llama.cpp :8080, thinking off) for
extraction, **bge-m3 via Ollama** for embeddings (multilingual — first
real-embedder run, retiring review item 6), incremental KEE workdir,
policy gate + canary enabled. Driver: `pilot-law-ro/driver.py`.

## Cycle log

| Cycle | Time | What happened |
|---|---|---|
| 1 | 24 min | 23/29 chunks extracted (6 timeouts at the fixed 120 s cap); proposal **rejected by the integrity gate** — a real bug (below), caught exactly as designed |
| 2 | 6 min | 6 failed chunks retried under `GRAP_LLM_TIMEOUT=300` (all succeeded); hash fix in place; **adopted: 34 outcomes** (bootstrap canary skip); gap run: 137 |
| 3 | ~2 min | converged no-op: proposal `already_active`, queue empty, **HEALTHY: True** |

Working graph at convergence: 34 concepts, 142 characteristics, 47
aliases (13 embedding-merges), 29 chunks, 2 grounded edges, 55 parked
relations.

## Findings (each caught by a gate or a log, none by corruption)

1. **VRAM contention (ops).** Bonsai's default 64 k context + a resident
   embedding model do not fit a 16 GB card: decode collapsed 30×
   (1.4 t/s). `BONSAI_CTX=16384` → 10.3 GB combined, full speed. Now a
   documented constraint for laptop deployments.
2. **Stale concept hashes on multi-alias concepts (code, fixed).**
   `_canonical_label` prefers the *shortest alias*; real-data merges made
   the exported name diverge from the hashed node-creation text, and the
   integrity gate refused adoption. Toy corpora (one alias per concept)
   could never expose this. Export now hashes the exported name
   (regression test `test_export_hashes_survive_shorter_alias`).
3. **Fixed 120 s LLM timeout too tight (code, fixed).** Dense legal
   chunks lost 6/29 extractions. `GRAP_LLM_TIMEOUT` env added; the
   manifest design was validated in passing — failed chunks were *not*
   marked done and retried for free in cycle 2.
4. **Superseded proposals lingered in the queue (code, fixed).** Cycle
   1's dead proposal kept the factory "unhealthy" after cycle 2 adopted a
   newer one. Proposal-derived queue entries older than the latest
   adoption now clear (`test_superseded_proposals_clear_from_queue`).
5. **Language drift (open — next tuning target).** Bonsai code-switches:
   `framework_contract` (EN) and `contractul_cadru` (RO) are the same
   concept twice, and cross-lingual duplicates slip past the 0.86
   embedding merge even with bge-m3. Likely fix: pin the output language
   in the extraction prompt ("use the source document's language"),
   and/or lower sim for cross-lingual pairs. This is the dominant
   quality defect in the adopted ontology.
6. **Self-coverage gap detection needs calibration (open).** 137/142
   characteristics read as gaps against their own source docs: extracted
   characteristics are long tokenized phrases
   (`75_used_for_payment_of_medicines_and_medical_services`) that rarely
   clear the 0.6 lexical threshold, and EN-translated ones can never
   match RO evidence. Until characteristic granularity and language are
   tuned, gap counts on this corpus measure phrasing, not coverage.
7. **Relation grounding struggles on legal prose (open).** 2 grounded
   edges vs 55 parked. The parked mechanism did its job (nothing bogus
   entered the graph), but the relation vocabulary/prompting is not yet
   useful on this corpus.

## Quality of the adopted ontology (qualitative)

Genuinely meaningful structure came out: fund-utilization splits (75 %
medicines/services, 20 % system organization, 5 % administration),
framework-contract elaboration duties (CNAS + College of Physicians,
government-decree approval), contribution collection via Ministry of
Finance territorial directorates, the transport and defense/judiciary
insurance houses as separate entities. The extraction is *real* — the
defects are duplication (finding 5) and characteristic phrasing
(finding 6), not hallucination.

## Tuning cycles 4–5 (2026-07-20, later)

| Cycle | Change | Result |
|---|---|---|
| 4 | prompt: pin output language to source | **Win, committed (`5ff4593`).** Uniformly Romanian concepts, EN/RO twins gone, grounded edges 2→7, gaps 137→128. 13 min, zero timeouts. |
| 5a | + short-characteristics rule | **Crashed — finding 8:** duplicate parked relations violated the store PK at save time; the whole 18-min extraction was lost (transactional rollback left the store clean). Fixed: `_park` dedupe + `INSERT OR IGNORE`, regression-tested. |
| 5b | same, rerun on the fix | **Net regression, reverted.** Characteristics did get shorter (170, split), but English concept names returned (`contract_framework` beside `contractul_cadru`), gaps rose to 163, edges fell back to 2. |

Additional findings:

8. **Duplicate parked relations abort the save (code, fixed).** See above —
   the second-worst failure class observed (work loss, not corruption).
9. **Instruction budget (prompting lesson).** Bonsai-27B reliably follows
   one appended rule, not two: the characteristics rule diluted the
   language pin. Tune one prompt rule at a time and re-measure.
10. **Diacritic-mangling tokenization (open, likely the next big win).**
    `tokenize()` truncates at Romanian diacritics (*referinţă* →
    `referin`), corrupting outcome ids and lexical gap-matching. This is
    probably a larger contributor to gap noise than characteristic
    length — a scoring-side fix (diacritic transliteration in
    `reconcile.tokenize`) beats further prompt surgery.

## Tuning cycle 6 (diacritic-aware scoring) — win, committed (`2a5a05f`)

`reconcile.fold` (NFKD, combining marks stripped) applied on both sides
of every lexical operation: tokenize, keywords, `_content_tokens`,
`_covers`. Result vs cycle 4: **ids fully readable Romanian**
(`termenilor_de_referinta`, formerly `referin`), gaps 128 → **122**,
parked 54 → **47**, edges held at 7, 38 outcomes, 12.4 min.

11. **Numeric tokens are unmatchable (open — cycle 7 target).**
    `_covers` extracts evidence words with `[a-z]+`, dropping digits,
    while characteristic tokens keep them (`15`, `1999`, `75`) — so every
    date/percentage-bearing characteristic is a permanent gap. Same
    structural class as the diacritics bug; fix is `[a-z0-9]+` on the
    evidence side + tests. Legal text is date-laden, so this likely
    outweighs cycle 6's own gap improvement.

## Tuning cycle 7 (numeric tokens in coverage) — correct fix, null result

`_covers` now matches digits (`413c2f4`, unit-proven) but gaps stayed at
exactly 122 (delta 0/0) — which localizes the dominant bottleneck:

12. **The retrieval layer is diacritic-blind (open — the next big fix,
    Rust-side).** Reasoner queries are folded ASCII (`sanatate`) but the
    core text index stores raw diacritic evidence (`sănătate`), so the
    lexical channel often never retrieves the right chunk — scoring
    fixes cannot help on hits that never arrive. Proper fix: an
    ascii-folding analyzer in the core's fulltext index (Rust +
    pygrap wheel rebuild). Query-side workarounds are impossible:
    characteristic ids are already folded, so the diacritic form cannot
    be reconstructed.

Cycle 7 side-proofs on real data: audit-trail rehydration restored the
active ontology in a fresh process; `already_active` made the re-run a
zero-LLM no-op; a scoring-only cycle costs ~4 min.

## Tuning cycle 8 (retrieval) — gaps 122 → 1

The Rust fix for finding 12 landed first (`normalize_term`: lowercase +
NFKD + strip combining marks on both paths of BOTH text-index backends;
the on-disk backend was passing raw case-preserved tokens to the engine,
so it was case-sensitive too). Wheel rebuilt, `pilot-law-ro/kg` deleted to
force re-ingest — and the re-run still scored **122, delta 0/0**. That
null result exposed the biggest finding of the pilot:

13. **The evidence KG held ZERO nodes for all seven prior cycles (code,
    fixed).** The corpus is `.json`; KEE extraction is law-JSON-aware, but
    evidence ingestion only matched `.md/.txt`, silently ingested nothing
    (`files: 0` reported deep in an unprinted step), and the factory said
    HEALTHY. Every gap run of the pilot scored against an empty index —
    cycles 6–7 could never have moved the number. Fixed twice over:
    `_text_and_label_from_json` moved into `ingest.py` so evidence
    ingestion and extraction read the SAME text for the same file, and
    `run_pipeline` now records an `evidence_ingest` event that raises an
    `evidence_empty` queue alarm whenever configured evidence dirs match
    zero files (latest-state semantics; clears on the next non-empty
    ingest). A silent zero is exactly what a dark factory must not do.

With evidence actually ingested (folded index + folded scoring):
**gaps 122 → 1**, in 264 s. The one survivor is honest: an
English-language characteristic left over from pre-language-pin drift
(`...:regarding_some_social_protection_measures_for_personnel_in_the_
defense_production_sector`) — a real ontology defect correctly flagged,
not a scoring artifact.

The improvement itself then tripped one more latent bug:

14. **`list_runs` returned filesystem order, not chronological (core,
    fixed).** Run ids are random hex, so "the latest production run" was
    a random run: the 122→1 improvement was reported as **121 NEW gaps**
    (delta paired backwards), fired a false `gap_regression` alarm, and —
    worse — the canary's fallback baseline and the factory-status trend
    used the same broken assumption. `RunStore::list()` now sorts by
    `(started_at, run_id)` and pygrap exposes `started_at`/`completed_at`.
    Regression test: `list_is_chronological_regardless_of_filename_order`.

Verification cycle 8c (post-fix): delta computed against the correct
predecessor, false alarm cleared, HEALTHY restored.

Best-known configuration after eight cycles: cycle 6 prompts + folded
index + real evidence (38 RO outcomes, 7 edges, **1 gap**, ~4.5 min
scoring cycle). Self-coverage now measures what it claims to measure.

## Cycle 9 (kill the last gap) — the queue doing its job

A from-scratch re-extract under the language pin did NOT fix the EN
characteristic: Bonsai re-emitted the same English translation for that
chunk (13.5 min), and the fresh 37-outcome graph was **rejected by the
churn gate** (4 outcomes / 37 characteristics removed vs budget 0) —
correctly, since from-scratch extraction drifts. The right move turned
out to be the human act the exception queue exists for: the EN
characteristic was a *translated duplicate* of an existing RO
characteristic on the same concept (both from doc 16659), so the EN row
was deleted from the working graph (curation, not extraction). Policy
was temporarily widened with a rationale (added 200 / removed 10 /
chars-removed 60 / canary 60) for the re-baseline, and the curated
all-RO ontology auto-adopted: **canary passed, EN gap resolved**.

15. **Mapping-hint variance flips coverage (open — tuning target).**
    Adopting the re-extracted graph produced 6 gaps on characteristics
    whose text was IDENTICAL to previously-passing ones — only the
    extraction's mapping hints changed, which changed retrieval seeding.
    Gap stability currently depends on hint quality; retrieval should be
    made hint-robust (e.g. union of hint-seeded and characteristic-only
    queries). All 6 resolved on their own once the corpus grew (better
    seeds available), confirming the diagnosis.

## Cycle 10 (scale to 49 docs) — first honest zero, then the real baseline

39 more acts (stratified by tip_act, 1.5–10 KB each, ~166 KB) took the
corpus to 49 docs. One lights-out pass (77 min): **139 new chunks
extracted, zero timeouts**; working graph grew 37→280 concepts,
136→1016 characteristics, 6→53 grounded edges, 212 parked. Before
adoption, the old 37-outcome ontology hit **0 gaps** on the enlarged
evidence — the pilot's first honest zero. The 280-outcome proposal was
policy-rejected on pure volume (243 added > 200 even widened), reviewed
(integrity 280/280, provenance clean, **0 EN-looking ids at scale** —
the language pin held across 39 unseen documents), and adopted manually
with a rationale. A follow-up cycle picked up one straggler chunk and
auto-adopted the 281st outcome through the widened gate (canary passed,
40 new gaps ≤ 60).

Scaled baseline: **281 outcomes, 1016 characteristics, 54 gaps (94.7 %
self-coverage)**; full scoring cycle ≈ 31 min. Policy restored to strict
(added 5 / removed 0 / chars-removed 0 / canary 0) after the
re-baseline. The `gap_regression` alarm for the 54 new-ontology gaps is
truthful pending triage.

16. **Scoring wall-time is journaling-bound, not search-bound (open).**
    ~2 s per characteristic with near-zero CPU: the per-query session
    journal and per-gap explanation records dominate, likely aggravated
    by the workspace living under a OneDrive-synced path. At 1016
    characteristics that is ~31 min/cycle. Options: batch the journal
    writes, or move pilot state off synced storage.

## Recommended next interactive cycles

1. Triage the 54 gaps (list is in run-8ea143a2): expect a mix of honest
   phrasing defects and hint-variance (finding 15).
2. Hint-robust retrieval (finding 15) — likely the biggest gap-count
   stabilizer at scale.
3. Scoring throughput (finding 16) before any further corpus growth.
4. Then 200+ docs, watcher-driven (run_factory.py), queue-only
   supervision.
