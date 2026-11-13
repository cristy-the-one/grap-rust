# Spec — gold-set tooling for the automotive corpus and the test-point sheet

**Status:** specification, ready to implement. **Date:** 2026-11-13.
**Decides:** how the automotive labelling sheets are produced and how Sheet 4 ("What must a test
verify?") is scored.
**Depends on:** the merged packet generators and scorer (cards G-01 to G-04), the merged readers.

---

## 1. What is missing today

- `make_gold_packet.py` and `make_pair_packet.py` **refuse** `--corpus automotive`. They were
  written before the readers existed; their error message still lists the readers as missing.
- Sheet 4 of the automotive labelling guide has **no packet generator and no scorer**. It is the
  sheet that measures the product goal.

---

## 2. Automotive items for Sheets 1 and 2

Both generators already work on lists of `SourceItem`s. For `--corpus automotive` they read the
corpus through the same extension dispatch as ingestion (`ingest._READERS`), so a new reader is
picked up without touching the generators.

| Sheet | Which items are eligible |
|---|---|
| 1 — statements | `kind_hint` `requirement` or `text`, with `text` of 80 to 1,500 characters |
| 2 — pairs | mentions = the `item_id` and `short_name` of every item, plus identifiers found in item text by the identifier rule (card R-09) |

Everything else is unchanged: hash order by seed, at most 4 items per file, every fourth file
sealed, the stop conditions, the printed counts.

The Sheet 1 label columns for automotive are those of the automotive guide: `kind`, `subject`,
`trigger_condition`, `required_behaviour`, `values_and_timing`, `exception`, `refers_to`,
`traces_up_to`, `configuration_dependency`, `sure`, `note`.

**A file set, not a file.** An ARXML source is a set of files. The sampling unit for sealing is
the **source** (`source_id`), not the file, so one source is never split between open and sealed.

---

## 3. Sheet 4 — the packet

`scripts/make_testpoint_packet.py`, same arguments as the other generators, default `--items 40`.

- **Eligible:** items with `kind_hint` `requirement` whose text contains "shall" or "must".
- **Selection:** as G-01 — hash order, at most 4 per source, every fourth source sealed.
- **Columns:** `packet_id`, `source_id`, `item_id`, `passage`, then empty:
  `testable_as_written`, `preconditions`, `stimulus`, `expected_observable_result`,
  `pass_criterion`, `variants`, `negative_cases`, `needs_from_elsewhere`, `note`.
- **Stops, writing nothing:** fewer than 10 sources reached; fewer than `--items` rows; empty
  sealed sheet.

---

## 4. Sheet 4 — what is scored, and what is not

Only two columns can be scored by a program. The spec says so plainly rather than inventing a
number for the others.

| Column | Scored? | How |
|---|---|---|
| `needs_from_elsewhere` | **Yes — the main number** | the IDs the labeller lists, against the IDs in the context the system fetched for that requirement |
| `testable_as_written` | **Yes** | exact agreement on `yes` / `partly` / `no` |
| `preconditions`, `stimulus`, `expected_observable_result`, `pass_criterion`, `variants`, `negative_cases` | **No** | free text; compared by the owner, side by side (§6) |

**`needs_from_elsewhere`**

- The cell holds identifiers separated by `;` or new lines. The literal `none` means the
  requirement needs nothing else. **A blank cell is unlabelled and is refused** (rule from G-03).
- Both sides are compared after `normalise()` from the entity-resolution package, so
  `[SWS_Com_00123]` and `SWS_Com_00123` are equal.
- Per requirement: `needed` = gold IDs, `fetched` = system IDs.
  **Recall** = found ÷ needed. **Precision** = found ÷ fetched.
- Reported: recall and precision over all requirements (micro), each with the bootstrap interval
  by source, and the counts `n_requirements`, `n_needed`, `n_fetched`.
- A requirement whose gold is `none` contributes nothing to recall; it contributes to precision
  if the system fetched anything.

Recall is the number that matters: a test specification written without something it needed is
wrong however fluent it is. Precision is reported so that "fetch everything" cannot win.

**System output read by the scorer**

```json
{"testpoints": [
  {"source_id": "…", "item_id": "…", "needs": ["…", "…"], "testable": "yes"}
]}
```

`testable` may be absent; then that metric prints `n/a`.

---

## 5. Scorer changes

`scripts/score_gold.py` gains a fourth sheet, `testpoints.csv`, read under the same rules as the
others: `open/` by default, `sealed/` only with `--sealed`, counts beside every figure, `n/a`
when there is nothing to measure, refusal on unlabelled rows.

New lines in the report:

```
testpoints_needs_recall=…  [low, high] n_requirements=… n_needed=… n_fetched=… n_docs=…
testpoints_needs_precision=… [low, high] …
testpoints_testable_agreement=… [low, high] n_gold=… n_pred=… n_docs=…
```

---

## 6. The side-by-side sheet

`scripts/export_testpoint_review.py --gold <dir> --system <json> --out <csv>` writes one row per
requirement in the **open** sheet with the labeller's six free-text columns next to the system's
generated values and an empty `verdict` column (`same` / `acceptable` / `wrong` / `missing`).
It never reads `sealed/`. It is a review aid; nothing is computed from it in this version.

---

## 7. One change to the labelling guide

Sheet 4, `needs_from_elsewhere`: *"Write the identifiers, separated by semicolons. If the
requirement needs nothing else, write `none`. Do not leave the cell empty."* — added to
`gold-set-guide-automotive.md` in the same change as this spec.

---

## 8. Cards

Rules of Part A apply.

| Card | Tag | Does | Test first |
|---|---|---|---|
| **TP-01** Automotive Sheet 1 | [S] | §2 for `make_gold_packet.py`. | A folder with the DBC fixture and a ReqIF fixture yields rows; one source never appears in both sheets; the old "readers missing" refusal is gone. |
| **TP-02** Automotive Sheet 2 | [S] | §2 for `make_pair_packet.py`. | Mentions include an item's `short_name`; band counts exact. |
| **TP-03** Sheet 4 packet | [S] | §3. | Only "shall"/"must" requirement items are sampled; same seed, same bytes; the three stop conditions. |
| **TP-04** Sheet 4 scoring | [M] | §4 and §5. | A hand-built gold of 4 requirements with known hits gives the exact recall and precision; bracketed and bare IDs match; `none` handled; a blank cell is refused; sealed rows untouched without the flag. |
| **TP-05** Side-by-side export | [S] | §6. | Output has one row per open requirement and contains no sealed `packet_id`. |

Order: TP-01 → TP-02; TP-03 → TP-04 → TP-05. The two chains are independent.
TP-01 to TP-03 can only be run on real data once the owner has chosen the corpus; until then they
are tested on fixtures and say so in the PR.
