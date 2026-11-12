# Labelling guide — automotive gold set

**For:** the owner, as AUTOSAR expert and labeller.
**Purpose:** the reference that the graph, and later the generated test specifications, are scored
against. Automotive is the product goal, so this set outranks the law one.
**Expected effort:** about 25 hours.

The example requirements in this guide are **invented to show the shape**. They are not quotations
from any specification.

## Ground rules

1. **Corpus first, and only cleared material.** Use documents you are entitled to use and that may
   be processed by a local model: for example AUTOSAR requirement and software specifications for
   three to five modules, plus a public DBC or ARXML sample. **No customer document enters the
   corpus, the gold set or any model prompt without written clearance.**
2. **Label blind.** Do not look at what the tool extracted from a document before labelling it.
3. **Label from the document, not from experience.** If the specification is silent or wrong and
   you know better, record what the text says and put your correction in the note. The gold set
   measures reading, not engineering judgement — except in Sheet 4, where judgement is the point.
4. **No AI assistance while labelling**, and no pasting of gold rows into any assistant, including
   during development. A quarter of the set is sealed by script before you start and is only ever
   read by the scoring script.
5. **Passages are chosen by a seeded random script**, not by you and not by a developer. Label
   what you are given; mark unusable rows `broken` rather than replacing them.
6. **Pin the release.** Every row names the document and its release. The same module in two
   releases is two sources.

---

## Sheet 1 — Statements (about 150 specification items)

One row per assertion. An item with several "shall" clauses gets several rows.

| Column | What to write |
|---|---|
| **kind** | See the table below, or `unclear`. |
| **subject** | The element the statement constrains: module, API, parameter, signal, state. Copied from the text. |
| **trigger / condition** | The "when / while / if" part, copied exactly. Empty if unconditional. |
| **required behaviour** | The response the text demands, copied exactly. |
| **values and timing** | Return values, error codes, limits, timing, copied exactly. |
| **exception** | Any "unless / except" clause. |
| **refers to** | Every identifier the item cites: requirement IDs, API names, parameter names. |
| **traces up to** | The upstream requirement IDs the item says it satisfies. |
| **configuration dependency** | Any parameter or switch that must be set for the statement to apply. |
| **sure?** / **note** | As usual. |

| Kind | Use it when the item… |
|---|---|
| **requirement** | demands observable behaviour ("shall") |
| **configuration constraint** | restricts parameter values, multiplicity or combinations |
| **definition** | fixes the meaning of a term, type or abbreviation |
| **state or mode rule** | defines a state, a transition or what is allowed in a state |
| **error handling** | says what is detected, reported or returned on a fault |
| **informative** | explains, gives rationale or an example, and demands nothing |
| **other** | heading, table caption, change history |

Hard cases:

- **"shall" inside a note or rationale** is `informative` unless the document says notes are
  normative.
- **A requirement that only applies when a switch is on**: kind `requirement`, with the switch in
  *configuration dependency* — not a `configuration constraint`.
- **A sentence that both returns a value and reports an error**: two rows.
- **"may"** is not a requirement; label `informative` and say in the note that it is a permission.
- **A table row that states behaviour** counts as a statement; quote the cells.

Shape of a filled row (invented):

> *[SWS_Xyz_00042] If the module is not initialized, Xyz_Transmit shall return E_NOT_OK and, if
> development error detection is enabled, shall report XYZ_E_UNINIT.* (traces to SRS_BSW_00406)

Row 1 — kind `error handling` · subject *Xyz_Transmit* · condition *If the module is not
initialized* · behaviour *shall return E_NOT_OK* · traces up to *SRS_BSW_00406*.
Row 2 — kind `error handling` · subject *Xyz_Transmit* · condition *If the module is not
initialized* · behaviour *shall report XYZ_E_UNINIT* · configuration dependency *development error
detection is enabled*.

---

## Sheet 2 — Same thing or not? (about 200 pairs)

Each row shows two names with the sentence and document each came from.

| Answer | Meaning |
|---|---|
| **same** | One element, written differently: abbreviation, short name versus long name, spelling. |
| **same, other release** | The same element as specified in a different release. Kept separate because behaviour may differ. |
| **different** | Distinct elements. |
| **part of** | One contains or owns the other: a container and its parameter, a PDU and a signal in it, a module and its API. Say which way. |
| **realises** | Different layers of one thing: a system signal and the signal that carries it, an abstract interface and its implementation. |
| **unclear** | Not decidable from what is shown. |

The pairs will deliberately include near-misses. Typical traps to rule on consistently: a signal
versus the signal group that contains it; an API and its service identifier; a parameter short
name versus its full container path; identically named parameters in two modules; a term used
loosely in prose versus its glossary definition.

---

## Sheet 3 — Questions (about 100)

You write these. Each must be answerable — or deliberately not — from the corpus alone.

| Column | What to write |
|---|---|
| **question** | As an engineer would ask it. |
| **type** | One of the six below. |
| **answer** | Short. |
| **where** | Document, release and item ID of every passage needed. |
| **note** | Traps, configuration assumptions. |

| Type | Count | Example shape |
|---|---:|---|
| **Direct** — one item answers it | 25 | "What does … return when …?" |
| **Conditional** — depends on state, mode or configuration | 20 | "Is … allowed while … is active?" |
| **Trace** — up or down the requirement chain | 15 | "Which items implement requirement …?" |
| **Cross-module** — needs items from two specifications | 15 | "Who is notified when … fails, and through which call?" |
| **Impact** — what is affected by a change | 10 | "What depends on parameter …?" |
| **Not answerable** | 15 | plausible, but the corpus does not say; answer `not in the documents` |

---

## Sheet 4 — What must a test verify? (about 40 requirements)

This sheet is the one that points at the product goal. For each sampled requirement, write what a
competent test specification has to contain. Here your engineering judgement **is** the reference.

| Column | What to write |
|---|---|
| **testable as written?** | `yes`, `partly`, or `no` — and why not. |
| **preconditions** | State, mode and configuration the unit must be in. |
| **stimulus** | What the test does. |
| **expected observable result** | What must be seen, and where: return value, callback, signal, stored value. |
| **pass criterion** | Including tolerances and timing. |
| **variants** | Configuration variants or boundary values that each need their own case. |
| **negative cases** | What must *not* happen, and the fault cases the requirement implies. |
| **needs from elsewhere** | Other items, parameters or interfaces a tester must read to write this test. List their IDs. |

Guidance:

- Write verification points, not test code and not a full procedure.
- *Needs from elsewhere* is the most valuable column: it is exactly what the graph must retrieve.
  A generated test specification that misses one of these is wrong however fluent it is.
- If a requirement is untestable as written, say what is missing. That is a finding about the
  specification and a case the tool must learn to flag rather than paper over.

---

## Consistency check

A week after finishing, relabel about fifteen of your own Sheet 1 rows and five Sheet 4 rows
without looking at the first pass. The disagreement rate is the ceiling against which the tool is
judged; scoring the tool against a standard its own expert does not meet would be meaningless.
