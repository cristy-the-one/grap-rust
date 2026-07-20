# Real-Corpus Pilot Report: Romanian Legal Acts (cycle 1–3)

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

Best-known configuration after six cycles: **cycle 6** (language-pinned
prompt + diacritic-folded scoring, `GRAP_LLM_TIMEOUT=300`,
`BONSAI_CTX=16384`, bge-m3, chunk_chars 2000, concurrency 4):
38 RO outcomes, 7 edges, 122 gaps, ~12 min/cycle.

## Recommended next interactive cycles

1. Prompt: pin output language to the source document; re-extract (only
   changed prompts → full re-extract of the workdir; budget ~25 min).
2. Consider chunk_chars 3000 (fewer, richer calls) and measure quality.
3. Calibrate self-coverage: shorter characteristics in the prompt
   ("3-6 word characteristics"), or a lower lexical threshold for
   RO text.
4. Then scale: 50 docs, same loop, watch cycle time and queue size.
