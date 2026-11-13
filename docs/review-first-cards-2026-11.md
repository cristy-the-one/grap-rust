# Review — first five cards (PRs 6, 7, 8, 10, 11)

**Reviewer:** Claude. **Date:** 2026-11-13. **Method:** each branch was checked out and run; every
finding below was reproduced by a command, not read off the diff.

All five pass their own tests and the full Python suite. All five followed their card. **Four of
the nine findings are defects in the cards, not in the implementation** — they are marked *spec*
and the cards are corrected in the same change as this review.

| PR | Card | Verdict |
|---|---|---|
| 11 | R-07 loud embedder failure | **Approve** after one small addition |
| 10 | R-02 DBC reader | **Changes requested** — attributes missing (*spec*) |
| 6 | G-01 packet generator | **Changes requested** — must be fixed before anyone labels |
| 7 | G-03 scorer | **Changes requested** — can report perfect scores for nothing |
| 8 | G-04 sealed-set guard | **Changes requested** — the guard does not guard |

---

## PR 8 and PR 7 — scorer and sealed-set guard

### F1. The sealed set is read on every run; `--sealed` only decides whether to log it *(impl + spec)*
`_sheet_paths` always collects `open/` **and** `sealed/`. The flag only appends the log line.

Reproduced: a gold folder with one open row (matched) and one sealed row (unmatched), scored
**without** `--sealed`: recall `0.5`, no `ACCESS.log` written. Delete the sealed sheet: recall `1.0`.
So sealed rows move every ordinary score and leave no trace. The sealed set stops being held back
the first time anyone runs the scorer, which defeats its purpose.

**Required:** default reads `open/` only. `--sealed` reads `sealed/` **only**, and writes the log
line *before* printing. A flat folder (sheets directly inside) counts as open.

### F2. A missing or empty sheet scores 1.0 *(impl + spec)*
With no `pairs.csv` and no `questions.csv`: `pairs_f1=1.0`, `questions_hit_rate=1.0`. With no
predictions at all: `statements_precision=1.0`. The report prints no counts, so a perfect score
from an absent sheet is indistinguishable from a real one.

**Required:** print `n` (gold rows, predicted rows, documents) beside every metric. A metric with
no gold rows prints `n/a`, never a number. Precision with zero predictions prints `n/a`.

### F3. Unlabelled and mistyped rows are scored in silence *(impl)*
Reproduced with an unfilled packet and an **empty system output**: `pairs_f1=1.0`,
`questions_hit_rate=1.0`. Causes: a blank or unknown pair label (`"sme"`) counts as "not same"; an
answerable question whose `where` is empty is a hit for any system (`all([])` is true); a blank
`kind` counts as a gold row.

**Required:** refuse, naming the sheet and the count, when any gold row has a blank `kind`, a pair
label outside the guide's list, or an answerable question with an empty `where`.

### F4. Statements match across documents *(spec)*
Matching uses `item_id` alone. The packet generator's `item_id` is the article label, and in the
real packet `Articolul 1` occurs 30 times and `Articolul 2` 30 times. A system claim about article
1 of one act matches the gold row for article 1 of another.

**Required:** match on `(source_id, item_id)`. Card B3 corrected.

*Not findings:* the 0.79 / 0.80 boundary is implemented exactly in integers; intervals resample
documents as specified; 160 rows score in 0.6 s; no row is ever printed.

---

## PR 6 — statement packet generator

Runs on the real corpus in 10 s, byte-identical across two runs, 150 rows from 54 documents, no
duplicate packet IDs, does not use the statement splitter. Good.

### F5. The gold set would be almost entirely articles 1–4 *(spec)*
The card said "the first 4 in file order". On the real corpus that gives, in the open sheet:
`Articolul 1` ×30, `Articolul 2` ×30, `Articolul 3` ×20, `Articolul 4` ×19 — **99 of 124 rows**.
Opening articles are purpose, scope and definitions; obligations, sanctions and amendments sit
later. A labeller would spend twenty hours on an unrepresentative sample.

**Required:** within a file, order eligible items by `sha256(seed + file + item_id)` and take the
first 4. Card G-01 corrected. **Do not send a packet to the labeller before this lands.**

### F6. The stop condition is not implemented *(impl)*
Card: "Stop if fewer than 40 distinct source files can be reached." Reproduced: a one-file corpus
exits 0 and writes a 4-row packet with an empty sealed sheet.

**Required:** exit non-zero with the number of files reached; also when fewer than `--items` rows
were produced, or the sealed sheet is empty.

---

## PR 10 — DBC reader

Deterministic; keeps signals in file order; and it caught something the card did not anticipate —
`cantools` silently drops signals parked on `VECTOR__INDEPENDENT_SIG_MSG`, which the reader now
refuses rather than lose. That is the right instinct.

### F7. A message has no CAN identifier, and a signal has no signedness *(spec)*
The card's attribute list omitted them, and the implementation followed the card. Reproduced on
`full_features.dbc`: message attributes are `cycle time`, `length`, `senders` only. Without the
frame ID a message cannot be tied to a bus trace; without signedness a value cannot be decoded.

**Required:** message — `frame_id` (hex), `is_extended_frame`; signal — `is_signed`, `is_float`,
`is_multiplexer`, `multiplexer_ids`, `multiplexer_signal`. Card R-02 corrected.

### F8. Attribute keys contain spaces *(spec)*
`"start bit"`, `"byte order"`, `"cycle time"`, `"value table"`. Use `snake_case`, as every other
reader will. Card corrected.

*Recorded, not blocking:* `strict=True` refuses a whole file for one overlapping-signal pair, and
the unassigned-signal refusal does the same. Both are honest — nothing partial is returned — but
real OEM files commonly have both, so expect refusals on first contact with real data. Decide then
whether to read such files with the damage counted, as the ARXML spec does.

---

## PR 11 — loud embedder failure

Matches the card. Probed five configurations: unreachable endpoint without the flag raises and
names the fix; with the flag it falls back; explicit `hashing` works; unset backend without
`sentence-transformers` raises; default model is `bge-m3`. Launch scripts already select `bge-m3`.

### F9. A mistyped backend name still gets the stub in silence *(spec)*
`GRAP_EMBED_BACKEND=olama` returns `HashingEmbedder` with no error — the exact failure the card
exists to remove, through a door the card did not mention.

**Required:** an unrecognised backend name raises, listing the valid names. Card R-07 corrected.

*Minor:* the comment in `aspice-gui/backend/main.py` still describes the old silent fallback and
names the old model. Outside the card's file list, so correctly untouched; fix it with F9.

---

## What this says about the cards

Small models followed the cards closely and did not wander, which is what the cards were for. The
weak point was the cards themselves: where a card left something unsaid, the implementation was
silent too, and no test noticed. Two changes to the working rules follow:

1. **Every scoring or sampling tool must print its denominators.** A number without its `n` is
   not reviewable.
2. **Run each new tool once on the real corpus before review**, and attach the output. F5 is
   invisible in a unit test and obvious in the first real run.
