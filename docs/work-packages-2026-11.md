# Work packages — built to be carried out by less capable models

Companion to [next-level-plan-2026-11.md](next-level-plan-2026-11.md). The plan says *what* and
*why*; this file cuts it into cards that say *exactly what to touch and how to know it is done*.

Every card carries a tag:

| Tag | Who can do it |
|---|---|
| **[S]** | A small model. Every decision is already made on the card. |
| **[M]** | A mid model. Bounded judgement, clear stop conditions. |
| **[L]** | **Needs a capable model.** Open design, or a mistake would be silent. Its output is a spec that turns into [S] cards. |
| **[H]** | A human. |

An **[L]** card must never be handed to a small model "to save time". The typical failure is not a
crash; it is a plausible result that is quietly wrong.

---

## Part A — Rules for whoever implements a card

Read these before every card. They are not optional.

1. **One card per session.** Do not start a second card, and do not "improve" nearby code.
2. **Baseline first.** Before changing anything, run:
   `PYTHONPATH=mcp-server .venv/Scripts/python.exe -m pytest mcp-server/tests -q`
   Expected at the time of writing: **576 passed**. If it is not green, stop and report.
3. **Test first, and watch it fail.** Write the test named on the card, run it, and confirm it
   fails for the reason the card states. Paste that failing output into your report. A test that
   was never seen failing does not count.
4. **Then the smallest change that passes.** No new dependencies unless the card names them.
5. **Never touch:** `src/` and `crates/` (the Rust engine is frozen); anything under `gold/`;
   the frozen case files in `docs/experiments/`; `real-data-law-ro/`.
6. **Never invent a number.** Every figure in a report must come from a command you ran, with the
   command shown.
7. **Never weaken a test to make it pass**, and never delete a failing test. If an existing test
   fails and the card did not predict it, stop.
8. **Commit only the files the card lists**, by explicit path:
   `git add <path> <path> && git commit -m "<card id>: <summary>"`. Never `git add -A` or `-a`.
9. **Stop conditions on the card are real.** When one is met, stop, change nothing further, and
   report what you found. Stopping is a correct outcome.
10. **No customer or sealed data in a prompt, a log, or a commit.** Ever.
11. **A tool that scores or samples prints its denominators.** Every figure is shown with the
    number of rows and documents behind it. Where there is nothing to measure it prints `n/a`,
    never a number.
12. **Run a new tool once on the real corpus before asking for review**, and paste the output
    into the report. Unit tests do not show a skewed sample or a vacuous score.

**Report format** (use exactly these headings): `Card` · `What I changed (files)` ·
`Failing test output (before)` · `Passing output (after)` · `Full suite result` ·
`Anything surprising` · `Stopped early? why`.

---

## Part B — Shared definitions (fixed here so no card has to decide them)

### B1. `SourceItem` — the one record every reader produces

A reader turns a file into a list of these. Readers do nothing else: no model calls, no graph.

| Field | Type | Meaning |
|---|---|---|
| `source_id` | str | Stable ID of the file (its stem, or the ID inside it). |
| `release` | str or null | Document version or release, as written in the file. |
| `item_id` | str | ID of the item inside the source. If the source has none, `"{source_id}#{n}"` with `n` counting from 0 in file order. |
| `kind_hint` | str | One of `requirement`, `signal`, `message`, `parameter`, `definition`, `heading`, `text`, `table`, plus the ARXML values listed in §5 of the ARXML spec. What the *format* says, not an interpretation. |
| `text` | str | The item's own text, unmodified. |
| `heading_path` | list[str] | Titles of the enclosing sections, outermost first. Empty if unknown. |
| `attributes` | dict[str, str] | Format-level fields (status, ASIL, unit, cycle time…), keys as in the source. |
| `links` | list[{`type`, `target_id`, `role`, `context`}] | Links stated by the format itself. `type` is one of `traces_to`, `part_of`, `refers_to`. `role` is the format's own name for the link (`""` if none). `context` is a list of item IDs, `[]` except for ARXML instance references. |
| `file` | str | Path relative to the corpus root. |
| `sha256` | str | Hash of the file's bytes. |

Rules: identical input bytes give identical output, in the same order. A reader that cannot parse a
file raises; it never returns a partial or empty list in silence.

### B2. Gold file layout

```
gold/<corpus>/open/{statements,pairs,questions,testpoints}.csv
gold/<corpus>/sealed/{...}.csv
gold/<corpus>/manifest.json        # seed, file hashes, which IDs are sealed
gold/ACCESS.log                    # one line per scoring run that read sealed/
```

`<corpus>` is `law-ro` or `automotive`. CSV is UTF-8 with a header row. Column names are exactly
those in the two labelling guides, lower-case with underscores, preceded by `packet_id`,
`source_id`, `item_id`, `passage`.

### B3. What counts as a match when scoring

- **Statement match:** same `source_id` **and** `item_id`, same `kind`, and subject agreement — both subjects
  lower-cased and whitespace-collapsed, then either equal or token-level F1 ≥ 0.8.
- **Pair labels** collapse for scoring: `same` and `same only here` count as *same*;
  `same, other release`, `part of`, `realises` and `different` count as *not same*; `unclear`
  rows are excluded and their count is reported.
- **Question match:** the system's cited passages include **every** `where` entry (recall = 1) for
  a full hit. Unanswerable questions are hits only if the system declines.
- **Uncertainty:** every figure is reported with a 95% bootstrap interval, resampling by
  **source document**, 2,000 resamples, seed 20261112.

---

## Part C — Phase 0: the yardstick

### G-01 [S] Statement packet generator
- **Goal:** produce the empty Sheet 1 for a labeller from a corpus folder.
- **New file:** `scripts/make_gold_packet.py`; test `mcp-server/tests/test_gold_packet.py`.
- **Behaviour:** arguments `--corpus-root`, `--corpus {law-ro,automotive}`, `--seed`, `--items 150`,
  `--out`. Read `SourceItem`s (law: one item per article, using the existing `_ARTICLE` pattern in
  `kee/legal_rules.py`; automotive: from the readers in Part D). Keep items whose `text` is 80 to
  1,500 characters. Order source files by `sha256(seed + file path)`; walk files in that order
  taking at most 4 items per file until `--items` is reached. Within a file, order the eligible
  items by `sha256(seed + file path + item_id)` and take the first 4 — **not** the first 4 in file
  order, which on the law corpus yields almost only articles 1–4 (review finding F5). Mark
  every fourth *file* as sealed. Write the CSVs with label columns empty, and `manifest.json`.
- **Test first:** same seed twice → byte-identical output; different seed → different selection;
  no file contributes more than 4 items; sealed and open share no `source_id`.
- **Do not:** call any model; use the statement splitter `source_units` to choose passages (that
  would hide the splitter's own misses from the gold set).
- **Stop if (exit non-zero, print the counts):** fewer than 40 distinct source files were reached;
  fewer than `--items` rows were produced; the sealed sheet is empty.

### G-02 [M] Pair packet generator
- **Goal:** the empty Sheet 2, with enough hard cases to be informative.
- **Depends on:** G-01 (uses the same sampled files).
- **Behaviour:** collect entity mentions from the sampled files only, using the existing extraction
  receipts if present, otherwise capitalised noun phrases and identifiers found by regex. Embed
  mention names with the configured embedder. Emit 200 pairs: 80 with cosine ≥ 0.86, 60 with cosine
  in [0.70, 0.86), 30 random, and 30 that are string-identical but from different documents. Each
  row carries both sentences and both document IDs.
- **Diversity:** draw over distinct *name pairs* — one occurrence pair per name pair, chosen by the
  seeded hash — and let no single name appear in more than 3 rows. Drop any name that occurs in
  more than 20% of the sampled documents (page furniture) and print how many were dropped.
- **Test first:** band counts are exact; no pair appears twice in either order; a pair never puts
  one mention against itself; no name pair repeats; no name appears more than 3 times.
- **Stop if:** any band cannot be filled — report the shortfall, do not top it up from another band.

### G-03 [S] Scorer
- **New file:** `scripts/score_gold.py`; test `mcp-server/tests/test_score_gold.py`.
- **Behaviour:** implements B3 exactly. Reads a gold folder and a system-output JSON; prints
  precision, recall and F1 for statements, pair F1, question hit rate, each with its interval and
  its counts (gold rows, predicted rows, documents).
- **Which sheets:** by default `open/` only (a flat folder counts as open). With `--sealed`,
  `sealed/` only. Never both in one run.
- **Nothing to measure:** a metric with no gold rows prints `n/a`; precision with no predictions
  prints `n/a`. Never 1.0.
- **Refuse, naming the sheet and the count,** when any gold row has a blank `kind`, a pair label
  outside the guide's list, or an answerable question with an empty `where`.
- **Test first:** a hand-built gold of 6 rows and a system output with known hits gives the exact
  expected precision and recall; subject F1 of 0.79 is a miss and 0.80 is a hit; `unclear` rows
  are excluded and counted.
- **Do not:** print, log or write any individual row from `sealed/`.

### G-04 [S] Sealed-set guard
- **Behaviour:** `--sealed` is the **only** way the scorer reads `sealed/`. It appends
  `timestamp, git commit, user, metric summary` to `gold/ACCESS.log` **before** printing; if the
  log cannot be written, nothing is scored.
- **Test first:** scoring without the flag gives the same result whether or not `sealed/` exists. Add a test that walks every `.py` under `mcp-server/` and `scripts/` and fails
  if any file other than `score_gold.py` contains the string `gold/` followed by `sealed`.
- **Test first:** add a throwaway file that opens `gold/x/sealed/a.csv`; the test must go red;
  delete the file; green.

### G-05 [H] Labelling — per the two guides.

### G-06 [M] Baseline report
- Run the current system on the open set, score it, and write `docs/baseline-2026-11.md` with the
  command lines and the five numbers. Sealed set is scored **once**, at the end, by the owner.
- **Stop if:** any number looks better than 0.9 — on this system that more likely means a scoring
  bug than a good result. Report it instead of celebrating.

---

## Part D — Phase 1: automotive inputs, no model

### R-01 [H] Choose the corpus and confirm it may be used. Record the decision in `gold/automotive/README.md`.

### R-02 [S] DBC reader
- **New file:** `mcp-server/grap_mcp/readers/dbc.py`; test `test_reader_dbc.py`. Dependency:
  `cantools` (allowed).
- **Behaviour:** one `SourceItem` per message (`kind_hint=message`) and per signal
  (`kind_hint=signal`, link `part_of` → its message). Attribute keys are `snake_case`.
  Message: `frame_id` (hex string), `is_extended_frame`, `length`, `cycle_time`, `senders`.
  Signal: `unit`, `factor`, `offset`, `min`, `max`, `length`, `start_bit`, `byte_order`,
  `is_signed`, `is_float`, `is_multiplexer`, `multiplexer_ids`, `multiplexer_signal`, `receivers`,
  `value_table` as JSON.
- **Test first:** a 2-message fixture gives the expected item count, IDs and one `part_of` link per
  signal; a corrupt file raises.
- **Do not:** import anything from the VSS-engine project.

### R-03 [M] Specification PDF reader
- **New file:** `readers/spec_pdf.py`. Dependency: `pymupdf` (allowed).
- **Behaviour:** extract text page by page; find items of the form `[XXX_Name_12345]` followed by
  bracketed body text and an optional trailing `(ID, ID)` trace list; emit one `requirement` item
  each with `traces_to` links; track `heading_path` from numbered headings.
- **Test first:** a 3-item text fixture (not a real PDF) through the item parser gives 3 items with
  the right IDs, bodies and trace links; an item split across a page break is still one item.
- **Stop if:** on a real document, fewer than 90% of `[..._NNNNN]` tokens found by a plain regex
  become items. Report the count and three examples that failed. Do not loosen the parser by guess.

### R-04 [M] ReqIF reader
- **New file:** `readers/reqif.py`. Standard library XML only.
- **Behaviour:** one item per SPEC-OBJECT; attributes from its ATTRIBUTE-VALUEs; `heading_path`
  from the SPEC-HIERARCHY; `links` from SPEC-RELATIONs (`traces_to` unless the relation type name
  contains "refine" or "satisf", which are also `traces_to`; anything else `refers_to`).
- **Stop if:** the sample file uses XHTML attribute values with embedded objects — report it.

### R-05 ARXML reader — **spec done:** [spec-arxml-reader.md](spec-arxml-reader.md)
The [L] part is complete. It yields cards AR-01 … AR-09 ([S] and [M]), listed in §10 of the spec
with their tests and measured expected values. Two items remain for the owner and a capable model
(§11 of the spec): confirming the software-component shapes on real files, and merging elements
split across files.

### R-06 [S] Wire readers into ingestion
- **File:** `mcp-server/grap_mcp/ingest.py` — extend `_ALL_EXTS` and dispatch by extension to the
  readers. Existing `.md/.txt/.json` behaviour must be byte-for-byte unchanged.
- **Node content:** text = `item_id`, then `kind_hint`, then `text`, then `key=value` per
  attribute, one per line. Label = `kind_hint`. An item's own text is often empty.
- **Test first:** an existing ingest test still passes untouched; a `.dbc` fixture now yields
  items, and every node contains its own name.

### R-07 [S] Fail loudly on the stub embedder
- **File:** `mcp-server/grap_mcp/kee/embeddings.py`, function `get_embedder`.
- **Behaviour:** when the chosen backend cannot be loaded, raise `RuntimeError` naming the backend
  and the fix — unless `GRAP_EMBED_ALLOW_STUB=1`, in which case fall back as today and log a
  WARNING. `GRAP_EMBED_BACKEND=hashing` stays allowed without the flag. An unrecognised
  backend name raises and lists the valid names — it must never fall through to the stub. Change the default HTTP
  model from `nomic-embed-text` to `bge-m3`.
- **Test first:** unreachable endpoint with the flag unset raises; with the flag set returns the
  hashing embedder.
- **Expected side effect:** existing tests that rely on the silent fallback will fail. Fix by
  setting `GRAP_EMBED_ALLOW_STUB=1` once, in `mcp-server/tests/conftest.py`. 
- **Stop if:** more than 25 existing tests fail after that.

### R-08 [S] Format links become graph edges
- Implements schema §6. `links` on a `SourceItem` become edges with properties
  `{<link type>: true, "origin": "format", "role": <role>}` — the relation is the property
  **name**. `context` is not written to the engine; it is returned to the caller.
- Items are keyed by `(source_id, item_id)`. Node label = `kind_hint`. A link whose target is not
  in the corpus creates a placeholder node with label `unresolved` — never dropped.
- Returns counts: items, links, unresolved targets.
- **Test first:** two items, one linking to a missing ID → 3 nodes, 1 edge, 1 `unresolved`; two
  sources each with an item of the same ID keep their links apart.

### R-09 [M] Identifier-first resolution
- **File:** `kee/working_graph.py`, `_resolve_outcome`. Before the embedding step: if the name is
  an identifier, resolve by exact string only and **skip** the similarity merge entirely.
- **What counts as an identifier:** a name with no whitespace and at least one of — an underscore,
  a slash, a dot, a digit, or an uppercase letter after its first character; or a bracketed ID
  such as `[SWS_Com_00123]`. A phrase that *contains* an identifier is exempt from merging too.
- **Test first:** forced to identical vectors, each of these pairs stays two nodes:
  `Com_SendSignal` / `Com_SendSignalGroup`, `VehSpdFL` / `VehSpdFR`, `BrakePedalPosition` /
  `BrakePedalPressure`, `vehicle_speed` / `vehicle_speed_raw`, `/ISignal/SigA` / `/ISignal/SigB`.
  The same identifier seen twice is one node.

---

## Part E — Later phases

Cards below are deliberately coarse. Each **[L]** must be done before its **[S]** children exist.

| Card | Tag | What | Why it needs that level |
|---|---|---|---|
| X-01 Document context in the extraction prompt | **[M]** | Prefix each chunk with source title and heading path; quotes still validated against the chunk only | Changes every prompt: needs the gold baseline and a version bump; a small model will forget the quote rule |
| X-02 Does the quote support the claim? | **[M]** | Second, cheap check on flagged claims | Needs a threshold chosen on the open set, not guessed |
| T-01 Batching server and concurrency | **[M] + [H]** | Serve through vLLM; concurrent requests | Environment-specific (WSL, drivers); failures are not code bugs |
| E-01 Entity resolution | **spec done** | See [spec-entity-resolution.md](spec-entity-resolution.md): mentions stored as seen, decisions revocable, exact rules first, similarity in shadow mode until fitted on gold. Cards E-10 … E-20; only E-18 is [L] and needs the gold pairs | — |
| V-01 Automotive schema | **spec approved** | See [spec-automotive-schema.md](spec-automotive-schema.md): 22 node types, 30 relations. Cards S-01 … S-05 are ready ([S] and [M]); S-06 is [L] and waits for E-01 | — |
| C-01 Confidence and abstention on claims | **[L]** | Calibrated score, explicit "don't know" | Statistical; easy to produce numbers that look calibrated and are not |
| Q-01 Retrieval evaluation and reranking | **[L]** then [S] | Only after gold exists | Interpreting ablations |
| P-01 Procedure runtime | **spec approved** | See [spec-procedure-runtime.md](spec-procedure-runtime.md): eight operations, checked before running, one bounded refine loop, an event per step. Cards P-10 … P-16 are ready; P-17 needs the corpus | — |
| S-06 Statements from prose | **spec done** | See [spec-statements.md](spec-statements.md): the model marks parts, the code copies them verbatim. Cards ST-01 … ST-07 | — |
| Gold tooling for automotive and Sheet 4 | **spec done** | See [spec-testpoint-tooling.md](spec-testpoint-tooling.md). Cards TP-01 … TP-05 | — |

## Part F — Order

`G-01, G-03, G-04, R-02, R-07` can all start now, in parallel, by small models.
Then `R-01 [H]` → `R-03, R-04, R-06, R-08, G-02` → `G-05 [H]` → `G-06` → everything in Part E.
