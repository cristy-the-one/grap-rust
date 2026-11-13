# Spec — statements from prose (card S-06)

**Status:** specification, ready to implement. **Date:** 2026-11-13.
**Decides:** how the text of a requirement or specification item becomes typed claims in the
graph, linked to the things they mention.
**Implements:** [spec-automotive-schema.md](spec-automotive-schema.md) §8 "Statements".
**Depends on:** entity-resolution cards E-11 (tables), E-13 (normalise), E-14 and E-15 (exact
linking). **Feeds:** the procedure runtime ([spec-procedure-runtime.md](spec-procedure-runtime.md)).

This is a design. Nothing in it is measured; §8 says how it will be.

---

## 1. The principle

**The model points; the code copies.** The model never writes the content of a statement. It
chooses a kind and marks which parts of the source text are the subject, the condition, the
required behaviour, and so on. Every part must be a verbatim piece of the source, checked by
offset. Anything that is not is rejected and listed for review — never repaired.

This is the approach that worked on the law corpus (labels and literal subjects instead of a
generated ontology), generalised. It keeps conditions and exceptions attached to their rule,
because they are never re-worded.

---

## 2. Input and output

**Input:** `SourceItem`s whose `kind_hint` is `requirement` or `text`.

**Output per item:**
- zero or more **statements** (§3);
- **mentions** for every name a statement points at (stored in the `mentions` table of the
  entity-resolution spec);
- after linking, the statement's **edges** (§6).

An item that yields no statement gets one statement of kind `other`, so "nothing here" is a
recorded result and not a silence.

---

## 3. A statement

| Field | Content |
|---|---|
| `statement_id` | `{source_id}|{item_id}|{n}`, `n` counting from 0 in text order |
| `source_id`, `item_id` | the item it came from |
| `unit` | character span of the sentence or table row it was read from |
| `kind` | one of the seven kinds in the schema file — read from the file, never retyped |
| `subject` | span — who or what is constrained |
| `condition` | span or empty — the "when / while / if" part |
| `required_behaviour` | span or empty — what is demanded |
| `values_and_timing` | list of spans — return values, codes, limits, times |
| `exception` | span or empty — the "unless / except" part |
| `origin` | `rule` or `model` |
| `extractor` | name and version of the rule set, or model and prompt version |

Every span is `(start, end)` into the item's `text`. The text itself is not copied into the
statement; it is read back from the item.

Statements are stored in a new table `statements` in the extraction store. Like mentions, rows are
never edited; an item's rows are replaced only when that item is re-extracted.

---

## 4. Stage 1 — units and certain labels, no model

1. **Split the item into units.** A unit is a sentence, a list entry, or a table row. Reuse the
   existing sentence splitter; do not split inside a bracketed identifier, a number, or an
   abbreviation.
2. **Label what is certain** (origin `rule`):

| Pattern in the unit | Kind |
|---|---|
| no verb, or only a heading, caption or change-history line | `other` |
| "shall" or "must", outside a note or rationale | `requirement` (spans still come from Stage 2) |
| inside a block marked note, rationale or example | `informative`, whatever words it contains |
| "may" as the only modal | `informative` |
| "X is defined as …", "X means …", a glossary row | `definition` |

A unit with no certain label goes to Stage 2 with no kind. A unit labelled `other` or
`informative` by rule does **not** go to the model at all.

The rule table is data, not code: one file, one row per pattern, each with an example that must
match and one that must not.

---

## 5. Stage 2 — the model marks the parts

One request per item, or per group of small items up to a character budget. The request carries
the units, numbered, and the kinds from the schema file.

**The reply, enforced by a JSON schema:** for each unit number, a list of statements, each with
`kind` and the fields of §3 **as quoted text**.

**Checks on every statement, in code:**

1. Every quoted field occurs verbatim in its unit. The first occurrence gives the span. A field
   that does not occur → the statement is rejected with reason `not-verbatim`.
2. `subject` is required for every kind except `other` and `informative`.
3. `required_behaviour` is required for `requirement`, `error_handling`, `state_or_mode_rule` and
   `configuration_constraint`.
4. A rule-given kind from Stage 1 wins over the model's kind; the disagreement is counted.
5. Every unit sent must come back with at least one statement. A missing unit is retried once,
   then listed for review with reason `no-reply`.

**One unit can give several statements.** "…shall return E_NOT_OK and shall report X" is two.
The model decides the split; the code only checks that each one's parts are verbatim.

**The source text is data.** The prompt says so, and a unit that contains instructions is still
only quoted, never obeyed.

Rejected statements are kept in a review list with the unit, the raw reply and the reason. They
are not written to the graph.

---

## 6. From statements to edges

**Mentions.** For each statement, these become mentions (type hint in brackets):
the `subject` span; every identifier found inside any span by the identifier rule of card R-09;
every span in `values_and_timing` that is an identifier.

**Linking** is done by the entity-resolution stages. This spec adds nothing to them.

**Edges**, once a mention is linked to an entity — decided by this table and nothing else:

| The mention is in… | …and the entity is a… | Edge from the statement |
|---|---|---|
| `subject` | anything, when kind is `definition` and it is a `Term` | `defines` |
| `subject` | anything else | `about` — the first linked mention only |
| `condition` | `State` | `applies_in` |
| any field | `Parameter` | `configured_by` |
| `required_behaviour` or `values_and_timing` | `Fault` | `raises` |
| anywhere else | anything | `refers_to`, with `role` = the field name |

Also: `stated_in` to the item, always; and `has_statement` from the `Requirement`, when the item
is one.

A mention that links to nothing stays as text on the statement and is counted. **A statement with
no linked subject is still written**, with no `about` edge, and counted as `subject_unlinked` —
that count is the main health number of this stage.

Every edge has `origin` equal to the statement's origin and no `confidence` until card C-01.

---

## 7. What a run prints

`items` · `units` · `units_by_rule` · `units_to_model` · `statements` by kind · `rejected` by
reason · `kind_disagreements` · `mentions` · `mentions_linked` · `subject_unlinked` ·
`edges` by relation. Each with its denominator (rule 11).

A second run on unchanged items makes no model call and changes nothing.

---

## 8. How it is accepted

| Check | Data | Pass |
|---|---|---|
| The guide's hard cases | five fixtures, one per case in the automotive labelling guide: "shall" inside a note; a requirement that applies only when a switch is on; a sentence that returns a value and reports an error; "may"; a table row | each gives exactly the rows the guide prescribes |
| Verbatim rule | a stub model that returns one re-worded field | that statement is rejected with `not-verbatim`; the others in the reply are kept |
| Silence | a stub model that omits a unit | one retry, then `no-reply` in the review list |
| Idempotence | any fixture | second run: zero model calls, identical tables |
| Against the gold set | Sheet 1, open sheet | reported through `score_gold.py`; no threshold is set here — the first run **is** the baseline |

The last row needs card ST-06, which writes the system output in the form the scorer reads.

---

## 9. Cards

Rules of Part A apply. Code goes in `mcp-server/grap_mcp/kee/statements/`.

| Card | Tag | Does |
|---|---|---|
| **ST-01** Table | [S] | The `statements` table of §3 with accessors; no in-place update; replace per item. |
| **ST-02** Units and certain labels | [M] | §4, with the rule table as a data file. Test: each rule's match and non-match example. |
| **ST-03** Model marking | [M] | §5: request, JSON schema, the five checks, the review list. Tested with the stub provider only. |
| **ST-04** Mentions | [S] | §6 first part: statements → rows in `mentions`. |
| **ST-05** Edges | [M] | §6 table. Test: one fixture per row of the table, and the `subject_unlinked` count. |
| **ST-06** Output for the scorer | [S] | Write `{"statements": [{source_id, item_id, kind, subject}]}` with `subject` as the quoted text. |
| **ST-07** Live run | **[M] + owner** | Run on real items with the local model; paste §7's counts. Needs the corpus. |

Order: ST-01 → ST-02 → ST-03 → ST-04 → ST-05 → ST-06. ST-05 needs E-14 and E-15 merged.

---

## 10. Left open

- **Prompt wording and model choice.** Decided by measurement on the open gold sheet, not here.
- **Splitting very long items.** The character budget per request is a setting; its value comes
  from the first live run.
- **Confidence.** Absent until card C-01.
