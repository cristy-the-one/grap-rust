# Spec — automotive graph schema (card V-01)

**Status:** approved by the owner on 2026-11-13, with the decisions recorded in §11. Nothing is
implemented yet.
**Decides:** which kinds of things the knowledge graph holds for the automotive goal, which
relations may connect them, how each is identified, and how reader output maps onto them.

This is a design, not a measurement. Its test is §9: eight questions a test engineer asks, each
with the path through the graph that must answer it. If a question cannot be answered by its path,
the schema is wrong, whatever else is true of it.

Everything here follows from two constraints in §1 and the standards' own vocabulary.

---

## 1. Two constraints that shape everything

**The engine allows 64 edge property names per graph, in total.** Relation types are edge
properties, and the engine's fast pruning works on property *names*. So the relation vocabulary
must be small and closed. This schema uses **30 relations and 3 metadata names — 33 of 64.** The
law pilot produced 418 free-text verbs; that cannot be stored this way at all.

The owner offered to raise the limit if it proves too tight. **Decision: keep it.** It is not
tight — 31 names remain free — and a small closed vocabulary is what this schema wants anyway. The
engine is frozen, and the limit is a bit-mask width in its core. Revisit only if the procedure
graph and the event log together need more than the 31 that remain.

**A node in the engine has only text, one label and an embedding. It has no attributes.** So the
engine graph is a retrieval view. Attributes, provenance and edge details live in the extraction
store (SQLite), which stays the authority — as the September audit already concluded.

Three rules follow:

1. **Closed vocabulary.** A node type or relation not in this file does not exist. Adding one is a
   change to this spec, not to code.
2. **Nothing is dropped for not fitting.** An item with no matching type becomes an `Element`; a
   link with no matching relation becomes `refers_to` with its original role. Both are counted.
3. **Where a standard has a word, use it.** AUTOSAR's names for architecture and communication;
   the owner's goal document for the test side. No invented synonyms.

---

## 2. Three tiers

| Tier | What it holds | Trust |
|---|---|---|
| **Evidence** | The sources and the items read from them, verbatim | Fact: this text exists in this file |
| **Entity** | The things the sources talk about: signals, components, requirements… | As good as its origin (§6) |
| **Claim** | What a source *says* about entities, with its conditions | A proposal until reviewed |

A requirement is an **entity** (it has an identifier and can be traced). What it demands is one or
more **claims**. Keeping these apart is what lets one requirement carry several "shall" clauses,
each with its own condition — exactly how Sheet 1 of the labelling guide is filled in.

---

## 3. Node types (22)

| Tier | Type | What it is | Typical origin |
|---|---|---|---|
| Evidence | `Source` | A document or file set, with its release | every reader |
| Evidence | `Item` | One item read from a source, text unmodified | every reader |
| Claim | `Statement` | One assertion: kind, subject, condition, required behaviour, values, exception | text extraction |
| Entity | `Requirement` | An identified requirement or specification item, at any level | ReqIF, specification PDF |
| Entity | `TestConcept` | What must be verified for a requirement: stimulus, expected result, pass criterion | owner / generation |
| Entity | `Template` | A reusable test-specification or checklist structure | owner |
| Entity | `TestCase` | An existing concrete test, kept for reuse | owner / import |
| Entity | `Ecu` | An ECU instance | ARXML, DBC node |
| Entity | `Component` | A software component type, composition, or basic-software module | ARXML |
| Entity | `Interface` | A port interface or an API | ARXML, specification |
| Entity | `InterfaceElement` | A data element, operation or argument of an interface | ARXML, specification |
| Entity | `Network` | A cluster or physical channel | ARXML, DBC file |
| Entity | `Frame` | A frame on a network (a DBC "message") | ARXML, DBC |
| Entity | `Pdu` | A protocol data unit | ARXML |
| Entity | `Signal` | A system signal, bus signal, or signal group | ARXML, DBC |
| Entity | `DataType` | A data type, scaling, unit or value constraint | ARXML |
| Entity | `Parameter` | A configuration container, a configuration parameter, or a calibration value | ARXML, specification |
| Entity | `State` | A mode or a state | ARXML, specification |
| Entity | `Fault` | A diagnostic trouble code, diagnostic event, or development error code | specification, ARXML |
| Entity | `DiagnosticService` | A diagnostic service, data identifier, routine or input-output control | ARXML, specification |
| Entity | `Term` | A glossary term or abbreviation, valid in a stated scope | any |
| Entity | `Element` | **Fallback:** an item no other type fits | ARXML mostly |

Several types cover more than one thing. Which one is recorded as the attribute `kind`, taken from
the source's own word (the ARXML tag, the DBC keyword). For example `Signal.kind` is one of
`system_signal`, `i_signal`, `dbc_signal`, `signal_group`.

**How the goal document's words map here**

| Goal document | This schema |
|---|---|
| Requirement | `Requirement` plus its `Statement`s |
| Signal | `Signal` |
| Interface | `Interface`, `InterfaceElement` |
| Constraint | a `Statement` whose kind is a constraint — not a node type of its own |
| Test Concept | `TestConcept` |
| Template | `Template` |
| Glossary term | `Term` |

---

## 4. Relations (30)

Read each row as *source* —relation→ *target*. An edge whose ends do not fit the row is **not
written**; it goes to a review list and is counted (§7).

### Evidence
| Relation | From → To | Meaning |
|---|---|---|
| `in_source` | `Item` → `Source` | the item was read from this source |
| `stated_in` | any entity or `Statement` → `Item` | this is where it is defined or asserted |
| `mentions` | `Item` → any entity | the item's text names this entity |

### Structure and claims
| Relation | From → To | Meaning |
|---|---|---|
| `part_of` | see below | containment |
| `has_statement` | `Requirement` → `Statement` | the requirement makes this assertion |
| `about` | `Statement` → any entity | the subject the statement constrains — exactly one |
| `refers_to` | any → any | **Fallback.** A stated reference with no better relation. `role` is mandatory. |

`part_of` is allowed for: `Signal` → `Pdu`, `Frame` or `Signal` (group); `Pdu` → `Frame` or `Pdu`;
`InterfaceElement` → `Interface`; `Parameter` → `Parameter` or `Component`; `Component` →
`Component`; `State` → `State`; `Requirement` → `Requirement`; `DiagnosticService` →
`DiagnosticService`; `Element` → `Element`.

### Requirements and test
| Relation | From → To | Meaning |
|---|---|---|
| `traces_to` | `Requirement` → `Requirement` | the first satisfies or refines the second (lower → upper) |
| `allocated_to` | `Requirement` → `Component` or `Ecu` | who must fulfil it |
| `depends_on` | `Requirement` → `Requirement`; `Component` → `Component` | a dependency the source states explicitly |
| `verifies` | `TestConcept` or `TestCase` → `Requirement` or `Statement` | what the test covers |
| `instantiates` | `TestCase` → `TestConcept` | the case realises the concept |
| `uses_template` | `TestConcept` or `TestCase` → `Template` | the structure it is written in |

### Architecture
| Relation | From → To | Meaning |
|---|---|---|
| `provides` | `Component` → `Interface` or `DiagnosticService` | offers it (a provide port, an implemented API, a supported service) |
| `requires` | `Component` → `Interface` | needs it (a require port, or a called API) |
| `connects_to` | `Component` → `Component` | a connector links them |
| `runs_on` | `Component` → `Ecu` | mapped to this ECU |
| `configured_by` | `Component` or `Statement` → `Parameter` | behaviour depends on this parameter |

### Communication and data
| Relation | From → To | Meaning |
|---|---|---|
| `attached_to` | `Ecu` → `Network` | the ECU is on this network |
| `sends` | `Ecu` or `Component` → `Frame`, `Pdu` or `Signal` | transmits it |
| `receives` | `Ecu` or `Component` → `Frame`, `Pdu` or `Signal` | receives it |
| `transmitted_on` | `Frame` or `Pdu` → `Network` | travels on this network |
| `realised_by` | `InterfaceElement` → `Signal`; `Signal` → `Signal` | the same information one layer down |
| `has_type` | `Signal`, `InterfaceElement` or `Parameter` → `DataType`; `DataType` → `DataType` | its type, scaling or unit |

### Behaviour
| Relation | From → To | Meaning |
|---|---|---|
| `applies_in` | `Statement` or `DiagnosticService` → `State` | holds, or is available, only in this mode, state or session |
| `transitions_to` | `State` → `State` | a transition |
| `raises` | `Component`, `Statement` or `DiagnosticService` → `Fault` | reports, sets or reads out this fault |

### Terminology and versions
| Relation | From → To | Meaning |
|---|---|---|
| `defines` | `Statement` → `Term` | the statement gives the term its meaning |
| `alias_of` | `Term` → any entity | the term names this entity, in the term's scope only |
| `corresponds_to` | X → X (same type) | the same element in another release |

---

## 5. Identity

Every node has a **key**: `type | scope | native_id`.

- `native_id` is the identifier the source itself gives: the ARXML path, the requirement ID, the
  DBC message or signal name, the term as written.
- `scope` is the `source_id`, followed by `@release` when the source has one.
- **Two nodes are the same only if their keys are equal.** No similarity matching for anything that
  has a native identifier. This is card R-09, generalised.
- The same element in two releases is **two nodes**, joined by `corresponds_to`. Behaviour can
  differ between releases, so merging them would be wrong.
- Things with no native identifier — entities met only in prose — are resolved by the
  entity-resolution design (card E-01), never by this schema. Until then they are `Term`s scoped to
  their source.

In the engine, a node's label is its type and its text is `native_id`, then its long name and
description. Everything else is in the store.

---

## 6. What an edge carries

Three properties in the engine, besides the relation itself:

| Property | Values | Purpose |
|---|---|---|
| `origin` | `format` · `rule` · `model` · `human` | **How far to trust it.** `format`: read from a structured file. `rule`: a deterministic text rule. `model`: proposed by a language model. `human`: a reviewer set it. |
| `confidence` | number, or absent | Absent means *not calibrated*. Never a made-up number. `format` edges are 1.0. |
| `role` | text | The source's own name for the link: the ARXML reference tag, the ReqIF relation type. Mandatory on `refers_to`. |

Everything else about an edge lives in the store, keyed by the edge: the evidence items, the
attributes of a folded mapping object (§8), and context for instance references.

**Two things from the goal document are deliberately not edge properties.**
*Visit count* is derived from the event log; writing to the graph on every read would make a
read-only query change the graph. *Validity over time* is expressed by `scope` and
`corresponds_to` for now; the names `valid_from` and `valid_to` are reserved.

**Encoding.** The relation is the property **name**, with value `true` — not the current
`{"rel": "<verb>"}`. That is what lets the engine prune by relation in constant time.

**Only the 30 relations of §4 may be property names.** Text extraction still produces free verbs,
and the engine refuses the 65th distinct name. Any verb that is not one of the 30 is written as
`{"refers_to": true, "role": "<verb>"}` — rule 2 of §1 applied to edges. Nothing is lost and the
name budget cannot be exhausted.

---

## 7. What happens to things that do not fit

| Case | Result | Counted as |
|---|---|---|
| An item with no type in §8 | an `Element`, with its original tag as `kind` | `untyped_items` |
| A link with no relation in §8 | `refers_to`, with `role` | `fallback_links` |
| A link whose ends break the table in §4 | not written; listed for review | `rejected_links` |
| A link to something not in the corpus | an edge to a placeholder, as the readers already do | `unresolved` |

A projection run prints all four counts with its totals. If `untyped_items` or `fallback_links`
exceeds 20% of its total on real data, stop: the schema is missing something, and that is a
finding for this spec, not something to patch in code.

---

## 8. From reader output to the graph

✔ = the shape is confirmed on the ARXML fixtures or by a merged reader. ○ = from the standard; to
be confirmed with the inventory tool (card AR-07) on real files.

### Items → node types

| Reader | Item | Node type · `kind` |
|---|---|---|
| any | the file or file set | `Source` |
| any | every item | `Item`, plus the typed node below when there is one |
| DBC ✔ | message | `Frame` · `dbc_message` |
| DBC ✔ | signal | `Signal` · `dbc_signal` |
| DBC | the file itself | also a `Network`; senders and receivers become `Ecu` |
| ARXML ✔ | `ECU-INSTANCE` | `Ecu` |
| ARXML ✔ | `CAN-CLUSTER`, `CAN-PHYSICAL-CHANNEL` | `Network` |
| ARXML ✔ | `CAN-FRAME` | `Frame` |
| ARXML ✔ | `I-SIGNAL-I-PDU`, `MULTIPLEXED-I-PDU`, `CONTAINER-I-PDU`, `SECURED-I-PDU`, `NM-PDU` | `Pdu` |
| ARXML ✔ | `SYSTEM-SIGNAL`, `I-SIGNAL`, `I-SIGNAL-GROUP` | `Signal` |
| ARXML ✔ | `COMPU-METHOD`, `UNIT`, `SW-BASE-TYPE` | `DataType` |
| ARXML ✔ | `ECUC-MODULE-CONFIGURATION-VALUES`, `ECUC-CONTAINER-VALUE` | `Parameter` |
| ARXML ○ | tags ending `-SW-COMPONENT-TYPE` | `Component` |
| ARXML ○ | tags ending `-INTERFACE` | `Interface` |
| ARXML ○ | `VARIABLE-DATA-PROTOTYPE`, `CLIENT-SERVER-OPERATION`, `ARGUMENT-DATA-PROTOTYPE` | `InterfaceElement` |
| ARXML ○ | `MODE-DECLARATION-GROUP`, `MODE-DECLARATION` | `State` |
| ARXML ○ | application and implementation data types, `DATA-CONSTR` | `DataType` |
| ARXML ○ | diagnostic trouble codes and diagnostic events | `Fault` |
| ARXML ○ | diagnostic services, data identifiers, routines | `DiagnosticService` |
| ARXML | everything else, including runnables and events | `Element` |
| ReqIF | specification object | `Requirement` |
| Specification PDF | bracketed, numbered item | `Requirement` |
| Text extraction | one assertion | `Statement` |

### Mapping objects fold into edges

ARXML describes many relations as objects in their own right. An object is **folded into one edge**
when it joins exactly two things; its own path is kept as the edge's evidence and its values as
edge attributes in the store, so nothing is lost.

| ARXML object | Becomes |
|---|---|
| ✔ `I-SIGNAL-TO-I-PDU-MAPPING` | `Signal` —`part_of`→ `Pdu` (start position, byte order) |
| ✔ `PDU-TO-FRAME-MAPPING` | `Pdu` —`part_of`→ `Frame` |
| ✔ `CAN-FRAME-TRIGGERING` | `Frame` —`transmitted_on`→ `Network` (identifier) |
| ✔ `PDU-TRIGGERING` | `Pdu` —`transmitted_on`→ `Network` |
| ✔ `SYSTEM-SIGNAL-REF` on an `I-SIGNAL` | `Signal` (system) —`realised_by`→ `Signal` (bus) |
| ✔ `COMPU-METHOD-REF`, `UNIT-REF`, `BASE-TYPE-REF` | —`has_type`→ `DataType` |
| ○ ports | `Component` —`provides` / `requires`→ `Interface` (port name as `role`) |
| ○ assembly and delegation connectors | `Component` —`connects_to`→ `Component` |
| ○ sender-receiver-to-signal mapping | `InterfaceElement` —`realised_by`→ `Signal` |
| ○ component-to-ECU mapping | `Component` —`runs_on`→ `Ecu` |

An object that joins three or more things, or that other items refer to, stays an `Element`.

### Other links

| Reader link | Relation |
|---|---|
| `part_of` (containment) | `part_of` where §4 allows the pair; otherwise `refers_to` |
| `traces_to` (ReqIF relation, specification trace list) | `traces_to` |
| any other `refers_to` | `refers_to`, keeping `role` |
| DBC senders, receivers | `Ecu` —`sends` / `receives`→ `Frame` or `Signal` |

### Statements

A `Statement` carries the columns of Sheet 1 of the automotive labelling guide as attributes:
`kind`, `condition`, `required_behaviour`, `values_and_timing`, `exception`. Its `kind` is one of:
`requirement`, `configuration_constraint`, `definition`, `state_or_mode_rule`, `error_handling`,
`informative`, `other`.

Its edges: `stated_in` → the `Item`; `has_statement` ← its `Requirement`, if it has one; `about` →
its subject; then, where the text names them, `applies_in` → `State`, `configured_by` →
`Parameter`, `raises` → `Fault`, `defines` → `Term`, and `refers_to` for anything else.

---

## 9. The test of this schema — eight questions

Each must be answerable by following the path shown. These become executable checks once data
exists (card S-04). The fourth is the product goal.

| # | Question | Path |
|---|---|---|
| 1 | Which upstream requirements does this one satisfy? | `Requirement` —`traces_to`→ `Requirement` (repeat) |
| 2 | Which signals does this requirement involve, and how are they scaled? | `Requirement` —`has_statement`→ `Statement` —`about`/`refers_to`→ `Signal` —`has_type`→ `DataType` |
| 3 | On which frame, with what identifier, on which network, does that signal travel? | `Signal` —`part_of`→ `Pdu` —`part_of`→ `Frame` —`transmitted_on`→ `Network` |
| 4 | **What does a tester need in order to write the test for this requirement?** | every node within two steps of the requirement's `Statement`s, over `about`, `refers_to`, `applies_in`, `configured_by`, `raises`, `has_type`, `part_of` |
| 5 | Which parameter settings change this behaviour? | `Statement` —`configured_by`→ `Parameter` |
| 6 | What is affected if this signal changes? | `Signal` ←`about`/`refers_to`— `Statement` ←`has_statement`— `Requirement` ←`verifies`— `TestConcept` |
| 7 | Which requirements have nothing verifying them? | `Requirement` with no incoming `verifies` |
| 8 | Is this the same signal as in the other release? | `Signal` —`corresponds_to`→ `Signal` |

Question 4 is the "needs from elsewhere" column of Sheet 4 in the labelling guide. The gold answers
in that column are therefore a direct test of this schema, not only of the tool.

---

## 10. Out of scope for this version

- **Runnables, events and timing.** They are read and kept as `Element`. Typing them waits until a
  test-generation case needs them.
- **Variants.** Items under a variation point are flagged by the reader; conditions are not
  evaluated.
- **The procedure graph and the event log.** Their labels are not defined here. 31 property names
  remain free for them.
- **The ASPICE process model** already in the tool. It stays as it is, under its own labels.
- **Law.** The legal claim model stays separate; it shares only the Evidence tier.

---

## 11. Decisions (owner, 2026-11-13)

| # | Question | Decision |
|---|---|---|
| 1 | `TestConcept`, or the ISTQB term "test condition"? | **Keep `TestConcept`.** |
| 2 | Ports folded into `provides` / `requires` edges? | **Agreed.** |
| 3 | `Frame` and `Pdu`: separate or merged? | **Separate.** Where the source has no PDUs, as in a DBC, signals attach straight to the frame. |
| 4 | Specification items and customer requirements: one type? | **One type, `Requirement`.** The level is an attribute, taken from the source; no fixed list of levels is imposed. |
| 5 | Diagnostics too thin? | **Add `DiagnosticService`.** Done in §3, §4 and §8. No new relation was needed. |
| 6 | Drop `depends_on`? | **Keep, for now.** Still limited to dependencies a source states outright. |
| 7 | 20% as the alarm level in §7? | **Accepted.** |
| — | Raise the engine's 64-name limit? | **No.** See §1. |

The diagnostic rows in §8 are marked ○: their ARXML tag names come from the standard and must be
confirmed with the inventory tool on real files before the projection relies on them.

---

## 12. Cards

| Card | Tag | Does |
|---|---|---|
| **S-01** Schema as data | [S] | Transcribe §3, §4 and §8 into `mcp-server/grap_mcp/schema/automotive.yaml`, with a loader. Test: 22 node types, 30 relations, every relation's ends are known types. |
| **S-02** Validator | [S] | Given a node type pair and a relation, say allowed or not, per §4. Loads the schema once; raises on a type or relation the schema does not contain. Test: one allowed and one refused example per relation; an unknown name raises. |
| **S-03** Projection | [M] | Turn `SourceItem`s into typed nodes and edges per §5 and §8, with the four counts of §7. Test first on the ARXML fixtures: `/ISignalIPdu/message1/Signal1` yields one `part_of` edge from a `Signal` to a `Pdu`, and no node for the mapping object. |
| **S-04** The eight questions | [M] | One executable check per row of §9, on fixture data. Needs S-03. |
| **S-05** Edge encoding | [M] | Relation as property name (§6) **only for the 30 schema relations**; every other verb as `refers_to` with `role`. Migration for existing graphs. Test: 70 distinct free verbs are accepted and each is retrievable by its role. Needs S-01 for the names and E-10 for the edge key. Touches `working_graph.py`; **not** the Rust engine. |
| **S-06** Statements from text into the schema | **[L]** | Needs the entity-resolution design (E-01) for subjects that have no identifier. |

Order: S-01 → S-02 → S-03 → S-04; S-05 independently. S-03 depends on ARXML cards AR-02 to AR-04.
