# Review — second batch (PRs 16, 17, 18, 19, 20, 22)

**Reviewer:** Claude. **Date:** 2026-11-13. **Method:** each branch checked out and run; every
finding reproduced by a command. All six are green in CI and pass the full suite.

Findings marked *spec* are gaps in the card, corrected in the same change as this review.

| PR | Card | Verdict |
|---|---|---|
| 19 | AR-07 inventory tool | **Approve — merge** |
| 20 | R-04 ReqIF reader | **Approve after two small additions** |
| 22 | G-02 pair packet | **Changes requested** — the packet wastes the labeller's time |
| 17 | R-09 identifier-first | **Changes requested** — misses most automotive identifiers (*spec*) |
| 18 | R-06 wire readers in | **Changes requested** — most DBC nodes come out empty |
| 16 | R-08 format links | **Changes requested** — predates the schema and contradicts it (*spec*) |

---

## PR 19 — AR-07 inventory tool: approve

Run on both main fixtures. Every token in the output is a number, a fixed label of the tool, or an
upper-hyphen name; a scan for anything else found nothing. The non-standard bucket is in. Safe to
run on confidential files and share.

## PR 20 — R-04 ReqIF reader: approve after two additions

Strict and well covered. A relation whose target is absent is kept as a link, which is right.

- **F10 (*spec*).** No refusal of files containing `<!DOCTYPE` or `<!ENTITY`. The ARXML spec has
  it; ReqIF files come from the same outside parties. Add the same refusal.
- **F11 (*spec*, to confirm with the owner).** `item_id` is the `IDENTIFIER` attribute, which in
  exports from requirement tools is an internal unique ID. The ID people write in documents is
  normally carried in the attribute named `ReqIF.ForeignID`. Keep `item_id` as it is — links depend
  on it — and add `attributes["native_id"]` from `ReqIF.ForeignID` when present. Not verified
  against a real export; the owner should confirm on one.

## PR 22 — G-02 pair packet: changes requested

Real-corpus run, real embedding model, 2 min 29 s:
`cosine_high=80/80 cosine_mid=60/60 random=30/30 identical=30/30 n_mentions=2836 n_docs=54
n_open=151 n_sealed=49`. Bands are exact, the cut-offs are the card's, and the sealed sheet is a
quarter of the rows — the worry about a small open sheet does not materialise on real data.

- **F12. The same few names fill the packet (*spec*).** 200 rows hold only 131 distinct name
  pairs. `MONITORUL OFICIAL` against itself appears **17 times**; the `Fondul Proprietăţii` pair
  12 times; one name appears in 39 rows. Pairs are drawn per occurrence, so frequent names
  dominate. A labeller would answer the same question a dozen times.
  **Required:** draw over distinct name pairs — one occurrence pair per name pair, chosen by the
  seeded hash — and let no single name appear in more than 3 rows.
- **F13. Page furniture is treated as names (*spec*).** `EMITENT GUVERNUL Publicat`,
  `Bacau EMITENT GUVERNUL Publicat`, `Partea I` are header boilerplate caught by the
  capitalised-phrase rule. **Required:** drop any name that occurs in more than 20% of the sampled
  documents, and print how many were dropped.

*Not a finding:* the one test that passed against the empty stub is a static guard; it has nothing
to fail on until code exists.

## PR 17 — R-09 identifier-first: changes requested

- **F14 (*spec*). The default pattern only recognises `Upper_Snake` and bracketed IDs.**
  Reproduced with an embedder that returns identical vectors for everything:

  | Pair | Result |
  |---|---|
  | `Com_SendSignal` / `Com_SendSignalGroup` | separate, as intended |
  | `VehSpdFL` / `VehSpdFR` | **merged** |
  | `BrakePedalPosition` / `BrakePedalPressure` | **merged** |
  | `vehicle_speed` / `vehicle_speed_raw` | **merged** |
  | `/ISignal/SigA` / `/ISignal/SigB` | **merged** |

  Most DBC and ARXML names are CamelCase without an underscore, so the rule misses the case it
  exists for. The pattern was mine.
  **Required:** a name is an identifier when it contains **no whitespace** and at least one of:
  an underscore, a slash, a dot, a digit, or an uppercase letter after its first character; or it
  is a bracketed ID. Add the four merged pairs above as tests.
- The implementation uses `search`, so a phrase *containing* an identifier is also exempt from
  similarity merging. That is stricter than the card and correct; keep it.

## PR 18 — R-06 wire readers into ingestion: changes requested

- **F15. A reader item is ingested as its `text` alone.** For a DBC that is the comment, and most
  signals have none. Reproduced on `full_features.dbc`: **9 of 12 nodes have empty text**, and no
  node contains its own signal or message name, so nothing is findable.
  **Required:** node text = `item_id`, then `kind_hint`, then `text`, then `key=value` for each
  attribute, one per line. Node label = `kind_hint`. Add a test that every node from the DBC
  fixture contains its own name.
- Existing `.md`, `.txt` and `.json` behaviour is unchanged, as required.

## PR 16 — R-08 format links: changes requested

Written before the schema existed; three points now contradict it.

- **F16 (*spec*). The relation is stored as a value** (`relation_type: "part_of"`). The schema
  (§6) stores it as the property **name** with value `true`, which is what the engine prunes on.
  **Required:** `{link.type: True, "origin": "format", "role": link.role}`.
- **F17 (*spec*). `context` is written as a fourth edge property.** Each property name costs one
  of the engine's 64. Keep context out of the engine; return it to the caller.
- **F18. Items are keyed by `item_id` alone.** Two DBC files with a message of the same name
  resolve each other's links. **Required:** key by `(source_id, item_id)`.
- **F19. Node label is the item's ID**, so the placeholder label `unresolved` collides with any
  item actually called that. **Required:** label = `kind_hint`; placeholders keep `unresolved`.
- **F20 (rule 11).** The function returns nothing. **Required:** return counts — items, links,
  unresolved targets.

---

## Pattern

Same as the first batch: the implementations follow their cards and the weak points are in the
cards. Three of this batch's cards were written before the schema and the entity-resolution spec,
and it shows. Cards issued from now on cite the section of the spec they implement.
