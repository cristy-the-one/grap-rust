# Review — third batch (PRs 25, 26, 27, 29, 30, 31, 33, 35, 36)

**Reviewer:** Claude. **Date:** 2026-11-13. **Method:** each branch checked out and run; every
finding reproduced by a command. All nine are green in CI.

Findings marked *spec* are gaps in the card or spec, corrected in the same change as this review.

| PR | Card | Verdict |
|---|---|---|
| 29 | AR-02 refusals and index | **Approve — merge** |
| 31 | AR-03 references | **Approve — merge** |
| 35 | AR-04 items and attributes | **Approve after three small changes** |
| 25 | S-01 schema as data | **Approve — merge** |
| 27 | S-02 validator | **Approve after two small changes** |
| 33 | E-11 resolution tables | **Approve — merge after 30** |
| 36 | E-12 mention backfill | **Approve — merge after 33** |
| 30 | E-10 several relations per pair | **Changes requested** — fixed in the store, still lost before it gets there (*spec*) |
| 26 | S-05 edge encoding | **Do not merge** — breaks the existing extraction path (*spec*) |

---

## PRs 29, 31, 35 — the ARXML reader

Every acceptance value in `spec-arxml-reader.md` §8 was re-measured on the top of the stack and
matches:

| File | Result |
|---|---|
| `system-4.2.arxml` | 157 identifiables, 22 packages, 125 references all resolved, 2 base declarations |
| `ecu-extract-4.2.arxml` | 17 identifiables, 69 references: 7 resolved, 55 unresolved definitions, 7 dangling |
| `system-dangling-reference-4.2.arxml` | loads; 1 dangling reference, 1 placeholder |
| `system-missing-factor-4.2.arxml` | loads; 5 references resolved |
| both main files together | 174 identifiables, 194 references: 132 / 55 / 7 |
| five non-4.x files | refused, `unsupported-dialect` |

A count of `DEST` attributes made separately, straight from the XML, equals `references_seen` on
every file. Both example identities come out exactly; the mapping object carries `START-POSITION`
and `PACKING-BYTE-ORDER`; all nine quoted tag counts match; a file with a DTD is refused.

Three small things in PR 35:

- **F21. The conservation check is an `assert`.** Run with Python's optimise flag it is skipped:
  reproduced, a deliberate violation was not raised under `-O`. The one check that makes a dropped
  reference impossible must not be removable by a command-line switch. **Required:** raise
  `ArxmlError`.
- **F22 (*spec*). The check cannot fail as written.** The six counts are tallied from the same list
  whose length they are compared with. What can actually go wrong is a reference never being
  collected. **Required:** count `DEST` attributes in a separate plain pass over each file
  (skipping `REFERENCE-BASES`) and require that to equal `references_seen`. Spec §4.3 corrected.
- **F23. The return value and report differ from spec §8.** It returns `(index, report)` with the
  items inside the report, and the report holds every reference in full. **Required:** return
  `(items, report)`; keep at most 50 entries per list in the report, as specified.

*Minor:* `SHORT-NAME` appears in the attributes next to `short_name`. Drop the duplicate.

## PR 25 — S-01 schema as data: approve

An independent table of every allowed combination, written from the spec without looking at the
file, was compared with the loaded schema over all 22 × 30 × 22 = 14,520 combinations.
**Zero differences.** 22 node types, 30 relations, 19 entity types.

## PR 27 — S-02 validator: approve after two changes

- **F24. About ten milliseconds per call.** The 14,520 checks above took over two minutes. The
  projection will ask once per edge. **Required:** load the schema once and answer from a
  set lookup.
- **F25. An unknown type or relation answers "not allowed".** A misspelt type name is then
  indistinguishable from a forbidden edge and is silently rejected. **Required:** raise on a name
  the schema does not contain.

## PR 33 — E-11 resolution tables: approve

Append-only and no-update rules are enforced by database triggers, not only by the accessors,
which is stronger than the card asked.

## PR 30 — E-10 several relations per pair: changes requested

- **F26 (*spec*). The second relation never reaches the store.** The store key now includes the
  relation, and the card's test passes at that level. But `WorkingGraph` keeps its own
  one-relation-per-pair map (`_edge_types`), and provenance (`edge_sources`) is keyed by pair too.
  Reproduced end to end: a fragment stating `EcuA sends FrameX` and `EcuA receives FrameX` leaves
  **one** relation in memory, one in the store, one after reopening. The card named only the
  table; the limit is in three places.
  **Required:** key `_edge_types`, `edge_sources` and the retraction paths by
  `(from, to, relation)`. Test through `WorkingGraph`: both relations survive `upsert`, `save`
  and `open`, and retracting one chunk removes only its own relation. This is now an **[M]** card.

## PR 26 — S-05 edge encoding: do not merge

- **F27 (*spec*). It crashes the existing extraction path.** The relation becomes an engine
  property name, and the engine allows 64. Extraction still produces free-text verbs — the law
  pilot had 418. Reproduced: the 65th distinct verb raises
  `GraphError: maximum of 64 property types exceeded`.
  The schema spec assumed a closed vocabulary but the card did not say what to do with a verb
  outside it. Schema rule 2 already has the answer: nothing is dropped for not fitting.
  **Required:** a relation in the schema is written as `{<relation>: true}`. Any other verb is
  written as `{"refers_to": true, "role": "<verb>"}`. Test: 70 distinct free verbs are accepted and
  each is retrievable by its role. Depends on S-01 for the list of names.
- Rebase on PR 30 once that is fixed; both edit the same lines of `working_graph.py`.

## PR 36 — E-12 mention backfill: approve

Run twice on a copy of a real local store (`.logs/qualified-rules-preview`): 11 receipts holding
51 name entries gave `mentions=49 n_repeated=2 n_unlocatable=0`; the second run reported
`added=0`. A direct `UPDATE` on the table is refused by the database.

**One fact to plan around, not a defect.** The large pilot stores under `pilot-law-ro/` — one has
9,934 concepts — were written before receipts existed and have no `extractions` table. On those the
backfill correctly reports `n/a` with zero receipts. Their mentions cannot be rebuilt; they would
have to be re-extracted. The entity-resolution spec said existing stores migrate without a model
call, which is true only for stores that have receipts. Spec corrected.

---

## Merge order

1. **29 → 31** (stacked; use the asynchronous merge).
2. **25**.
3. **35** and **27** after their small changes.
4. **30** after F26, then **33**, then **36**.
5. **26** last, after F27 and a rebase.
