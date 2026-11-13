# Spec — entity resolution (card E-01)

**Status:** specification, ready to implement in stages. **Date:** 2026-11-13.
**Decides:** how the tool works out that two pieces of text refer to the same thing — and how that
decision is stored, checked and undone.
**Depends on:** [spec-automotive-schema.md](spec-automotive-schema.md) (§5, identity).
**Unblocks:** schema card S-06.

This is a design. The only measured facts in it are the ones in §1, quoted from
`real-law-evaluation-2026-09.md`. Every threshold is left open on purpose and fitted on the gold
set (§7); none is guessed here.

---

## 1. What is wrong today

The live path is `WorkingGraph._resolve_outcome`. A new name is embedded **on its own**; if the
nearest existing concept scores at least 0.86 and passes a regex veto, the name is merged into it.

| Problem | Evidence |
|---|---|
| Only the bare name is compared — no sentence, no type, no definition | "ministry" against "minister" scored 0.968 and needed a hand-written veto |
| The result depends on the order documents arrive in | first spelling seen becomes the entity |
| A merge cannot be undone | the September report: a bad merge needs re-extraction into a new store |
| One threshold does everything | at 0.86, 3 of 8 known aliases are missed; at 0.80, false merges appear |
| The store keeps the merged result, not what was seen | `concepts` and `aliases` hold the outcome; the original occurrences survive only inside per-chunk receipts |

The three missed aliases are all abbreviations that their own document defines
("C.N.V.M." = "Comisia Naţionala a Valorilor Mobiliare"). No similarity score will ever find
those; a definition has to be read as a definition.

---

## 2. The design in one paragraph

**Store every occurrence as it was seen — a *mention* — and never change it. Decide separately,
and revocably, which entity each mention refers to.** Entities with an identifier come from the
readers and are fixed (schema §5). Resolution is then mostly *linking* mentions in prose to those
known entities, done in stages from certain to uncertain: exact rules first, similarity only for
what is left, and nothing uncertain written to the graph. Grouping is recomputed from the stored
decisions, so it can always be redone and always gives the same answer for the same inputs.

For automotive material this fits the data: most things worth linking — signals, interfaces,
parameters, requirements — already have identifiers.

---

## 3. What is stored

Four tables are added to the extraction store. Nothing existing is removed in the first stages.

| Table | One row is | Changes? |
|---|---|---|
| `mentions` | one occurrence of a name: source, item, character span, the text as written, the sentence around it, a type hint, which extractor produced it | **Never edited.** Rows for an item are replaced only when that item is re-extracted. |
| `link_decisions` | one decision about one mention and one entity: `same` or `not_same`, the method, the score, who or what decided, when | **Append-only.** The latest decision for a pair wins. |
| `constraints` | a standing rule between two entities: `must_link` or `cannot_link`, with origin and reason | Added by review and by definitions; removable. |
| `groupings` | the outcome of one resolution run: mention → entity | **Derived.** Recomputed per run; earlier runs are kept so two runs can be compared. |

An **entity** is either *identified* — it came from a reader and has a key `type | scope |
native_id` — or *discovered*: a name found only in prose, keyed `Term | source | normalised name`.

The graph is built from the latest `groupings` run. Undoing a merge means adding a decision or a
constraint and running again; no re-extraction.

---

## 4. The stages

Mentions are processed in sorted key order and each decision depends only on the stored data, never
on what was processed earlier. That is what makes the result independent of arrival order.

### Stage 0 — normalise
For comparison only; the original text is always kept.
Unicode-normalise; case-fold; for matching, treat diacritic variants as equal (the law corpus mixes
two encodings of the same letters); collapse whitespace. For identifier-like names additionally:
strip surrounding brackets and a trailing `()`; split on underscores and case changes into tokens.

### Stage 1 — exact rules *(written to the graph; origin `rule`)*
1. **Identifier match.** The normalised mention equals the `native_id`, or the last path segment,
   of an identified entity. If several entities match, prefer in this order: same source, same file
   set, same release. If that still leaves more than one, **link to none** and record the
   candidates as ambiguous.
2. **Defined alias.** The mention equals a name that a definition in scope assigns to an entity:
   a glossary entry, an "X — hereafter Y", an abbreviation list. A definition applies inside the
   source that states it, and nowhere else unless the owner marks that source as a shared glossary.
3. **Self-reference.** "this module", "the module", "prezenta lege": the entity the enclosing
   document is about, when the source declares one. A fixed phrase table per language.

A type conflict blocks any of these: a mention hinted as a signal is not linked to a component.

### Stage 2 — find candidates *(nothing written to the graph)*
For each mention Stage 1 left unlinked, collect candidate entities from three sources and take
their union: shared rare tokens; initials of a multi-word name equal to the mention; nearest
neighbours by embedding. Type-incompatible candidates are dropped.

The embedding is of the name **in context** — the mention with its sentence, the entity with its
long name and description — never the bare name.

### Stage 3 — compare each candidate pair *(shadow mode until §7 is met)*
Features: equal after normalisation; token overlap; edit similarity; initials match; contextual
embedding similarity; type compatibility; same source; and a **conflict flag** — differing numbers
or differing identifier tokens, which generalises today's regex veto and always means *not same*.

The features give a score. Two thresholds split it:

| Score | Outcome |
|---|---|
| at or above the upper threshold | `same` |
| at or below the lower threshold | `not_same` |
| between | **undecided** — goes to the review list; optionally to a language model that answers same / not same / unsure with both contexts in front of it. "Unsure" stays undecided. |

**Shadow mode.** Until the thresholds are fitted on the gold pairs, Stage 3 writes its scores to
`link_decisions` marked as proposals and writes **nothing** to the graph. The graph then contains
only Stage 1 links. That is deliberately conservative: a missed link costs a retrieval hit; a wrong
merge corrupts everything attached to it.

### Stage 4 — group *(derived)*
1. Mentions linked to an identified entity belong to it.
2. Remaining mentions with the same normalised name, in the same source, with compatible type, form
   one discovered entity.
3. Once Stage 3 is live: discovered entities joined by `same` decisions are merged transitively —
   **unless** the merged group would contain a `cannot_link` pair. Then remove the lowest-scoring
   `same` decisions, one at a time (ties broken by key order), until no violation remains.

### Stage 5 — into the graph
Each mention gives an `Item —mentions→ entity` edge. A statement's subject and references are
resolved through their mentions. A mention with no entity stays as text on its statement and is
counted as `unlinked_mentions`.

Every run prints: mentions; linked by each Stage 1 rule; ambiguous; candidates per mention; proposals
by band; undecided; unlinked; groups; constraint violations resolved.

---

## 5. What a reviewer does

The review list is a spreadsheet: the two names, each with its sentence and source, the score, and
an empty decision column — the same shape as Sheet 2 of the labelling guides. A filled row becomes
a `link_decision` with origin `human` and, where the reviewer says it holds generally, a
`constraint`. Human decisions override every other method and survive re-runs.

---

## 6. What this replaces, and what it does not touch

- **Replaced, at the end (card E-19):** the embedding path in `_resolve_outcome`, and
  `reconcile.greedy_cluster` for concepts.
- **Kept:** exact alias and content-hash matching; the regex veto, absorbed into the conflict flag.
- **Not touched:** the Rust engine; extraction prompts; the legal claim store.
- **Existing stores** are migrated without any model call: mentions are rebuilt from the receipts
  already saved per chunk, which hold every name with its quote.

One existing limit is in the way and is fixed first: the `edges` table allows **one relation per
pair of nodes**. The schema needs several (`about` and `refers_to`; `sends` and `receives`).

---

## 7. How it is accepted

| Check | Data | Pass |
|---|---|---|
| Regression | the 20 frozen law pairs | 0 of 12 distinct pairs merged; the 3 defined abbreviations now resolve through Stage 1 rule 2, given their defining sentence — **8 of 8**, up from 5 |
| Order independence | any fixture | the same grouping for three different arrival orders |
| Reversibility | any fixture | a wrong merge is undone by one constraint and a re-run, with no extraction call |
| Candidate coverage | gold pairs, open sheet | at least 95% of the pairs labelled *same* appear among the Stage 2 candidates; if not, widen Stage 2 before touching Stage 3 |
| **Going live** | gold pairs, open sheet, then sealed once | upper threshold = the lowest score with **zero** false merges on the open sheet; then scored once on the sealed sheet |

The last row is why Stage 3 starts in shadow mode: without the gold pairs there is nothing honest
to set a threshold against.

---

## 8. Cards

Rules of Part A in the work-packages file apply. New code goes in
`mcp-server/grap_mcp/kee/resolution/`.

| Card | Tag | Does | Needs gold? |
|---|---|---|---|
| **E-10** Several relations per pair | [S] | Change the `edges` key to include the relation type, with a migration. Test: two relations between the same pair both survive save and reload. | no |
| **E-11** Tables | [S] | The four tables of §3 and their accessors. Test: a decision appended later wins; `mentions` cannot be updated in place. | no |
| **E-12** Backfill | [S] | Rebuild `mentions` from stored receipts, no model call. Test: counts equal the names in the receipts; a second run adds nothing. | no |
| **E-13** Normalise | [S] | Stage 0, table-driven. Test: `[SWS_Com_00123]`, `Com_SendSignal()`, and both encodings of a Romanian letter. | no |
| **E-14** Exact rules | [M] | Stage 1 rule 1 with the scope order and ambiguity recording. | no |
| **E-15** Defined aliases and self-reference | [M] | Stage 1 rules 2 and 3. Test: the three law abbreviations, each with its defining sentence. | no |
| **E-16** Candidates | [M] | Stage 2, with the per-mention counts. | for the 95% check |
| **E-17** Scoring in shadow | [M] | Stage 3 features and score; proposals only. Report on the 20 law pairs. | no |
| **E-18** Thresholds and the model question | **[L]** | Fit thresholds on the gold pairs; decide from the size of the undecided band whether a language model is worth calling. | **yes** |
| **E-19** Grouping and switch-over | [M] | Stage 4 and 5; the working graph reads `groupings`; the old embedding merge is removed. | after E-18 |
| **E-20** Review list | [S] | Export and import of the spreadsheet in §5. | no |

Order: E-10 → E-11 → E-12 → E-13 → E-14 → E-15 → E-20, with E-16 and E-17 after E-13.
E-18 and E-19 wait for the gold pairs. Schema card S-06 can start once E-15 is merged.

---

## 9. Left open deliberately

- **Whether a language model is used at all in Stage 3.** Decided in E-18 from how many pairs fall
  between the thresholds. If the band is small, a reviewer is cheaper and more reliable.
- **Shared glossaries.** Which sources define terms for the whole corpus is the owner's call per
  corpus; the default is none.
- **Linking across releases.** `corresponds_to` between releases is by equal identifier only. An
  element renamed between releases is not detected.
- **Calibrated confidence on a link** belongs to card C-01. Until then a link's confidence is
  absent unless its origin is `rule` or `format`.
