# Reducing tables while they remain tables

> **Status:** design note + pre-registered experiment. Nothing implemented.
> **Origin:** the naive layout-cleaning A/B was **rejected** on 2026-07-25
> (characteristics 0.56×). This note explains *why* it failed, separates the
> genuinely-noise characters from the load-bearing ones, and specifies the
> variants worth testing. See [[dark-factory-lessons]] Theme VIII.

## The prize and the trap

Table-heavy Romanian legal documents (budget annexes, tariff schedules) spend
most of their characters on layout. On the five most table-heavy unprocessed
files, 50–91% of a chunk was collapsible whitespace and rules — worth ~452
chunks (~7.5 h of decode) across the remaining 1,392 documents.

The trap is that **the layout carries the table's structure**. Measured, same
prompt/model/parser, five chunks:

| | RAW | naive CLEAN |
|---|---|---|
| outcomes / chunk | 3.4 | 2.2 (0.65×) |
| characteristics / chunk | 10.0 | **5.6 (0.56×)** |
| relations / chunk | 3.6 | 2.8 (0.78×) |

`17126.json` went 1999 → 490 chars and 24 → 8 characteristics. `18903.json`
collapsed 1896 → 162 chars and produced **zero** outcomes where raw produced one.

## Why it failed — two distinct mistakes

**Mistake 1 — collapsing the cell delimiter.** Runs of spaces are what separate
columns in a fixed-width table. `SPACE.sub(" ", t)` turns
`"Denumire        1.250      lei"` into `"Denumire 1.250 lei"`, which is no longer
three cells but one sentence. The model can no longer tell a label from a value.

**Mistake 2 (a bug in the cleaner, not in the idea) — deleting vertical rules.**
The regex dropped `│ ║ ├ ┤ ┼` alongside `─ ═`. But horizontal rules are
decoration while **vertical rules are delimiters** — `│` is doing exactly the job
a `|` does in a markdown table. The cleaner removed the one character that was
already encoding the structure correctly, and there was no need to.

Dot leaders are the same story: in a table of contents or a form,
`"Agentul economic ......... 1.250"` uses the dots *as* a delimiter. Collapsing
them to a single `.` leaves `"Agentul economic . 1.250"`, which reads as a
sentence boundary rather than a cell boundary.

**The generalisation:** every one of those runs is a *delimiter encoded by
repetition*. Removing the repetition removes the delimiter. The reduction has to
**re-encode** the delimiter compactly, not delete it.

## The design space

**V1 — Delimiter substitution (the minimal correct version).**
Replace each run with one explicit delimiter token rather than with nothing:
runs of 2+ spaces → ` | `; dot/dash leader runs → ` | `; horizontal rules
(`─ ═ ─`) → drop entirely (genuinely decoration); vertical rules (`│ ║`) →
normalise to `|` and **keep**. Cost is ~3 characters per boundary instead of
10–40, so most of the saving survives while every cell boundary remains visible.

**V2 — Markdown table normalisation (the interesting hypothesis).**
Detect fixed-width column blocks (consecutive lines whose whitespace runs align
at the same offsets) and re-emit them as markdown pipe rows with a header
separator. Rationale: LLMs are heavily trained on markdown tables, so this may be
**both smaller and better understood** than the ASCII original — the only variant
with a plausible path to *beating* RAW rather than merely matching it. Riskier to
implement (column detection on ragged real-world text) and worth attempting only
if V1 confirms that delimiter preservation is what matters.

**V3 — Header protection.** Whatever the variant, never let reduction or chunk
truncation drop a table's header row: it is the only place the column *semantics*
appear, and every data row below depends on it. Cheap and orthogonal — worth
including in whichever variant wins.

**V4 — Leave tables alone, reduce only prose.** Apply reduction only outside
detected table blocks. Gives up most of the saving (tables are where the
characters are) but is guaranteed not to regress. The fallback if V1 and V2 both
fail.

## Pre-registered A/B protocol

**Use the same five chunks** (`18425`, `18903`, `50956`, `18338`, `17126` — the
biggest-saving, most table-heavy) so results are directly comparable to the
already-measured RAW and naive-CLEAN arms. Same system prompt, model, parser,
settings. Artifacts and driver already exist:
`pilot-law-ro/test_cleaning.py`, `distillation/cleaning-test/`.

**Baselines already measured** (this is the Theme VIII discipline — the thresholds
below are anchored to real numbers, not guessed):

- RAW: 3.4 outcomes / 10.0 characteristics / 3.6 relations per chunk
- naive CLEAN: 2.2 / 5.6 / 2.8, at ~0.35× the characters

**Success criterion (a variant is adoptable only if both hold):**

| metric | requirement |
|---|---|
| characteristics per chunk | **≥ 0.95× RAW** (≥ 9.5) — extraction must not lose content |
| input characters | **≤ 0.70× RAW** — the saving must be real |

**Refutation:** characteristics < 0.90× RAW ⇒ that variant is rejected, as the
naive version was. Do not soften this threshold after seeing the numbers.

**Also record** (shape, not just counts): relation-type diversity, whether any
arm produces *more* characteristics than RAW (would indicate the original layout
was actively confusing the model), and parse-failure counts — a shorter input
should not increase them.

**Sample-size caveat:** five chunks decided the rejection because the effect was
large (0.56×) and consistent in direction across all five. A variant that lands
*near* the 0.95× line needs more chunks — 15–20 — before adoption, because the
per-chunk variance is high.

## Ordering constraint (do not lose this)

Reduction changes chunk boundaries, therefore chunk ids, therefore the manifest.
Applying it to already-processed documents forces a **full re-extract** of them.
So either scope it to new documents only, or accept a one-time re-baseline. This
is why the decision has to be made *before* an ingestion batch, not after — and
why the naive version was tested before the 700→750 run rather than during it.
