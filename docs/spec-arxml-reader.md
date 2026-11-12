# Spec — ARXML reader (card R-05)

**Status:** specification, ready to implement. **Date:** 2026-11-12.
**Implements:** card R-05 of [work-packages-2026-11.md](work-packages-2026-11.md). Produces
`SourceItem` records (Part B1 of that file, as amended in §9 below).
**Capability:** this spec was the **[L]** part. The cards in §10 are **[S]** and **[M]**.

The design was prototyped against nine real ARXML files before this was written. Every count in
§8 is a measured result of that prototype, not an estimate. The files are the `cantools` project's
test fixtures (MIT licence), already vendored in VSS-engine under `tests/fixtures/arxml/`.

---

## 1. The design decision

**The reader is generic. It does not know AUTOSAR's element list.**

The obvious approach — a table of element kinds to extract — is exactly how references get dropped
in silence: anything not in the table disappears, and later releases add element kinds every year.
Instead the reader relies on three facts that hold for the whole 4.x schema:

1. Anything that can be referred to has a `SHORT-NAME` child. Its identity is the chain of
   `SHORT-NAME`s from the root: `/ISignal/Signal1`.
2. **Every reference carries a `DEST` attribute**, whatever its tag is called.
3. Wrapper elements without a `SHORT-NAME` are transparent to identity.

So: **every element with a `SHORT-NAME` becomes an item, and every element with a `DEST` becomes a
link.** Nothing is filtered by name. A small table (§5) only *labels* items for convenience; an
element missing from the table is still read, still linked, and labelled `element`.

One format-specific exception exists, for ECU configuration values (§6), because there the
meaningful name lives in a reference rather than in a `SHORT-NAME`.

---

## 2. Scope

### In scope
- AUTOSAR **4.x** XML: root element `AUTOSAR` in namespace `http://autosar.org/schema/r4.0`.
- A **set of files** as one source. References may resolve across files.
- All identifiable elements and all references, including instance references.
- ECU configuration parameter and reference values (§6).

### Deliberately out of scope — the reader must not attempt these
| Not done | Why |
|---|---|
| AUTOSAR 3.x and any other namespace | A 3.x file read with 4.x assumptions yields a plausible, wrong, near-empty result. It is refused (§3). |
| Computing anything: absolute bit positions, scaling factors, byte-order conversion | Interpretation belongs to the consumer. The raw values are preserved as attributes. |
| Evaluating variants | An item under a variation point is kept and flagged; the condition is not evaluated. |
| Merging the scalar attributes of one element split across files | First definition wins and the split is recorded (§4.4). |
| Validating against the XSD | Not a validator. |
| Interpreting ECU configuration against its module definitions | Definitions are usually not shipped; values are read as written. |

---

## 3. Refusals — the whole file set is rejected, with a named reason

| Condition | Error text must contain |
|---|---|
| The bytes contain `<!DOCTYPE` or `<!ENTITY` | `dtd-not-allowed` |
| Not well-formed XML | `not-xml` |
| Root is not `{http://autosar.org/schema/r4.0}AUTOSAR` | `unsupported-dialect` and the root tag found |
| A file is larger than 500 MB | `too-large` |
| After reading the whole set, there is no identifiable element other than packages | `vacuous` |

A refusal names the file. There is no partial result on refusal.
**Localised damage is not a refusal**: a dangling reference or a malformed value is recorded and
counted (§4.3) and the rest of the set is read. This is the opposite of `cantools`, which rejects a
whole system for one bad reference.

---

## 4. Algorithm

Two passes over the file set. Files are processed in **sorted order of their relative path**, so
output is deterministic.

### 4.1 Pass 1 — index and collect

Walk each file depth-first, carrying four values down the tree:

- `path` — the identity so far (starts as the empty string);
- `owner` — the path of the nearest enclosing identifiable element;
- `packages` — the list of enclosing package paths, outermost first;
- `iref` — the tag of the enclosing instance reference, or none.

At each element, in this order:

1. If it has a `SHORT-NAME` child: `path = path + "/" + short_name`; record `index[path] = tag`;
   set `owner = path`. If the tag is `AR-PACKAGE`, append `path` to `packages`.
2. If it is an `AR-PACKAGE` with a direct `REFERENCE-BASES` child: record that package's bases
   (§4.2) and **do not walk into** `REFERENCE-BASES`.
3. If it has a `DEST` attribute: record a reference
   `(owner, role = tag, dest = DEST, text, base = BASE attribute or none, packages, iref)`.
4. If its tag ends in `-IREF`: set `iref = tag` for its children.
5. Recurse into children.

`SHORT-NAME` text and reference text are stripped of surrounding whitespace. Tags are compared
without their namespace.

### 4.2 Relative references

A reference whose text starts with `/` is absolute. Otherwise it is relative to a **reference
base**, declared on a package:

```xml
<REFERENCE-BASE>
  <SHORT-LABEL>SystemSignalBase</SHORT-LABEL>
  <IS-DEFAULT>false</IS-DEFAULT> <IS-GLOBAL>false</IS-GLOBAL>
  <BASE-IS-THIS-PACKAGE>false</BASE-IS-THIS-PACKAGE>
  <PACKAGE-REF DEST="AR-PACKAGE">/SystemSignal</PACKAGE-REF>
</REFERENCE-BASE>
```

The base path is the declaring package itself when `BASE-IS-THIS-PACKAGE` is `true`, otherwise the
`PACKAGE-REF` text. To find the base for a relative reference, walk its `packages` list from the
**innermost** package outwards and take the first base that matches:

- with a `BASE="label"` attribute: a base whose `SHORT-LABEL` equals the label;
- without one: a base with `IS-DEFAULT` = `true`;
- a base declared on an **outer** package applies only if its `IS-GLOBAL` is `true`.

Target path = base path + `/` + reference text. If no base matches, the reference is
`unresolvable_relative`. **Never guess a base.**

*Verification status:* this rule resolves all three relative references in the fixtures (one named
base, two default bases). The `IS-GLOBAL` rule for outer packages is taken from the standard's
description and is **not exercised by any fixture**; card AR-03 adds a synthetic test for it.

### 4.3 Pass 2 — classify every reference into exactly one category

| Category | Condition | What is emitted |
|---|---|---|
| `resolved` | target path is in the index and its tag equals `DEST` | link to the item |
| `dest_mismatch` | target path is in the index, tag differs from `DEST` | link to the item, **and** an entry in the report |
| `unresolved_definition` | `DEST` starts with `ECUC-` and ends with `-DEF`, and the target is not an indexed item whose tag ends with `-DEF` | link to a placeholder |
| `dangling` | target path is not in the index (any other `DEST`) | link to a placeholder |
| `unresolvable_relative` | relative reference with no matching base | no link; report entry |
| `empty` | no text | no link; report entry |

A definition reference is checked **first**: a `DEST` ending in `-DEF` may only resolve to an item
whose tag ends in `-DEF`. (In the ECU-extract fixture one definition path happens to coincide with
a value container's path; without this rule it would be linked to the wrong thing.)

A **placeholder** is a `SourceItem` with `kind_hint = "unresolved"`, `item_id` = the target path,
`attributes = {"expected_kind": DEST}`, empty `text`. One placeholder per distinct path.

**The conservation rule.** After pass 2:

```
references_seen == resolved + dest_mismatch + unresolved_definition
                 + dangling + unresolvable_relative + empty
```

The reader asserts this before returning. It is the single most important line in the
implementation: it is what makes a dropped reference impossible rather than unlikely.

### 4.4 The same path in more than one place

| Case | Behaviour |
|---|---|
| Same path, both `AR-PACKAGE` | Normal. Packages merge; nothing is recorded. |
| Same path, same tag, not a package | First occurrence (in file order) supplies `text` and `attributes`. Children and references of **both** occurrences are kept. Recorded under `split_elements` with both files. |
| Same path, different tags | First occurrence wins. Recorded under `path_conflicts`. Later occurrence's children are still indexed. |

### 4.5 Instance references

An element whose tag ends in `-IREF` holds several `DEST`-bearing children. It becomes **one** link:

- `target_id` = the path of the child whose tag starts with `TARGET-`;
- `context` = the paths of the other children, in document order;
- `role` = the `-IREF` tag.

Each child is still classified individually under §4.3 and counted. If there is not exactly one
`TARGET-` child, the instance reference is recorded under `malformed_irefs` and its children are
emitted as ordinary links instead.

*Verification status:* **no fixture contains an instance reference.** The shape above is from the
standard. Card AR-05 tests it on a synthetic file, and card AR-07 checks it against a real file
before it is relied on.

---

## 5. What each item carries

| `SourceItem` field | Value |
|---|---|
| `source_id` | Caller-supplied name of the file set. |
| `release` | From `xsi:schemaLocation` of the item's file (§7). `null` if absent or unrecognised. |
| `item_id` | The absolute path. |
| `kind_hint` | From the table below; `element` if the tag is not listed. |
| `text` | First non-empty of: `DESC` text, `LONG-NAME` text, `SHORT-NAME`. For multi-language text take language `EN` if present, else the first. |
| `heading_path` | The enclosing package short names, outermost first. |
| `attributes` | See below. Always includes `tag` (the element's tag) and `short_name`. |
| `links` | One `part_of` link to the enclosing identifiable element (none at the top level), plus one `refers_to` link per reference owned by the item, with `role` and, for instance references, `context`. |
| `file`, `sha256` | The file of the first occurrence. |

**Attributes — generic flattening.** For each item, walk its subtree but **stop at** any child that
has its own `SHORT-NAME`, any element with `DEST`, and `REFERENCE-BASES`. Every remaining element
that has text and no child elements becomes an attribute. Key = the tags from the item down to that
element, joined with `.`, the item's own tag omitted. If a key repeats, append `[0]`, `[1]`… in
document order to **all** occurrences. Depth limit 10; at most 300 attributes per item; if either
limit is hit set `attributes["_truncated"] = "true"` and count it in the report. If the subtree
contains a `VARIATION-POINT` element, set `attributes["_has_variation_point"] = "true"`.

This preserves, uninterpreted, things like `START-POSITION`, `PACKING-BYTE-ORDER`, `LENGTH`,
`IDENTIFIER`, and the numerator and denominator values of a scaling.

**`kind_hint` table.** ✔ = tag observed in the fixtures. ○ = tag name from the standard, not in any
fixture; confirm with card AR-07 before trusting the label. The label never affects whether an
item or its links are read.

| `kind_hint` | Tags |
|---|---|
| `package` | ✔ `AR-PACKAGE` |
| `ecu` | ✔ `ECU-INSTANCE` |
| `network` | ✔ `CAN-CLUSTER`, `CAN-PHYSICAL-CHANNEL`, `CAN-COMMUNICATION-CONTROLLER` · ○ `LIN-CLUSTER`, `FLEXRAY-CLUSTER`, `ETHERNET-CLUSTER` and their channels and controllers |
| `message` | ✔ `CAN-FRAME` · ○ `LIN-UNCONDITIONAL-FRAME`, `FLEXRAY-FRAME` |
| `pdu` | ✔ `I-SIGNAL-I-PDU`, `MULTIPLEXED-I-PDU`, `CONTAINER-I-PDU`, `SECURED-I-PDU`, `NM-PDU` · ○ `N-PDU`, `DCM-I-PDU`, `GENERAL-PURPOSE-PDU`, `GENERAL-PURPOSE-I-PDU`, `USER-DEFINED-PDU` |
| `signal` | ✔ `I-SIGNAL`, `SYSTEM-SIGNAL`, `I-SIGNAL-GROUP` · ○ `SYSTEM-SIGNAL-GROUP` |
| `relation` | ✔ `I-SIGNAL-TO-I-PDU-MAPPING`, `PDU-TO-FRAME-MAPPING`, `CAN-FRAME-TRIGGERING`, `PDU-TRIGGERING` · ○ `I-SIGNAL-TRIGGERING`, `ASSEMBLY-SW-CONNECTOR`, `DELEGATION-SW-CONNECTOR`, `SENDER-RECEIVER-TO-SIGNAL-MAPPING`, `SWC-TO-ECU-MAPPING` |
| `scaling` | ✔ `COMPU-METHOD` |
| `unit` | ✔ `UNIT` |
| `datatype` | ✔ `SW-BASE-TYPE` · ○ `APPLICATION-PRIMITIVE-DATA-TYPE`, `APPLICATION-RECORD-DATA-TYPE`, `APPLICATION-ARRAY-DATA-TYPE`, `IMPLEMENTATION-DATA-TYPE`, `DATA-CONSTR` |
| `system` | ✔ `SYSTEM` |
| `component` | ○ any tag ending in `-SW-COMPONENT-TYPE`; `SW-COMPONENT-PROTOTYPE` |
| `port` | ○ `P-PORT-PROTOTYPE`, `R-PORT-PROTOTYPE`, `PR-PORT-PROTOTYPE` |
| `interface` | ○ any tag ending in `-INTERFACE` |
| `data_element` | ○ `VARIABLE-DATA-PROTOTYPE`, `PARAMETER-DATA-PROTOTYPE`, `ARGUMENT-DATA-PROTOTYPE`, `CLIENT-SERVER-OPERATION` |
| `behavior` | ○ `SWC-INTERNAL-BEHAVIOR`, `RUNNABLE-ENTITY`, any tag ending in `-EVENT` |
| `mode` | ○ `MODE-DECLARATION-GROUP`, `MODE-DECLARATION` |
| `config_module` | ✔ `ECUC-MODULE-CONFIGURATION-VALUES`, `ECUC-VALUE-COLLECTION` |
| `config_container` | ✔ `ECUC-CONTAINER-VALUE` |
| `unresolved` | placeholder (§4.3) |
| `element` | everything else |

---

## 6. ECU configuration values — the one special case

Parameter values have no `SHORT-NAME`; their name is the last segment of a definition path:

```xml
<ECUC-TEXTUAL-PARAM-VALUE>
  <DEFINITION-REF DEST="ECUC-ENUMERATION-PARAM-DEF">/…/ComIPdu/ComIPduDirection</DEFINITION-REF>
  <VALUE>RECEIVE</VALUE>
</ECUC-TEXTUAL-PARAM-VALUE>
```

For every `ECUC-CONTAINER-VALUE` item, in addition to the generic rules:

- For each direct child of `PARAMETER-VALUES` that has a `DEFINITION-REF` and a `VALUE`:
  attribute `param.<last path segment of DEFINITION-REF>` = `VALUE` text. Repeats get `[n]`.
- For each direct child of `REFERENCE-VALUES` that has a `DEFINITION-REF` and a `VALUE-REF`:
  the link created for that `VALUE-REF` gets `role` = `<last path segment of DEFINITION-REF>`
  instead of `VALUE-REF`.

The `DEFINITION-REF`s are still recorded and classified as references like any other.

---

## 7. Release detection

The namespace is the same for all of 4.x and says nothing about the release. Read
`xsi:schemaLocation` and take the schema file name:

- `AUTOSAR_4-2-2.xsd` style → `4.2.2` (replace `-` with `.`).
- Five-digit style → table: `00042` R17-03 · `00043` R17-10 · `00044` 4.3.1 · `00045` R18-03 ·
  `00046` 4.4.0 · `00047` R19-03 · `00048` 4.5.0 · `00049` R20-11 · `00050` R21-11 ·
  `00051` R22-11 · `00052` R23-11 · `00053` R24-11 · `00054` R25-11.
- Anything else → `null`. Never infer a release from the namespace or the file name.

The value is recorded, not acted on: the reader behaves identically for every 4.x release.

---

## 8. The read report, and measured expectations

`read_arxml(paths, source_id)` returns `(items, report)`. The report is a dictionary:

`files`, `identifiables`, `packages`, `references_seen`, one count per category of §4.3,
`base_declarations`, `split_elements`, `path_conflicts`, `malformed_irefs`,
`attributes_truncated`, `variation_points`, and `tag_counts` (tag → number of items).

Lists in the report (`dest_mismatch`, `dangling`, …) hold at most 50 entries each plus a total, so
a damaged 50 MB file cannot produce a report larger than itself.

**Expected results on the fixtures** — measured with the prototype, 2026-11-12. These are the
acceptance values for the cards.

| File | Result |
|---|---|
| `system-4.2.arxml` | 157 identifiables = 22 packages + 135. 125 references, **all `resolved`**, including 3 relative ones. 2 base declarations. |
| `ecu-extract-4.2.arxml` | 17 identifiables = 1 package + 16. 69 references: 7 `resolved`, 55 `unresolved_definition`, 7 `dangling`. |
| `system-dangling-reference-4.2.arxml` | **Loads.** 4 identifiables, 1 reference, `dangling`, one placeholder. |
| `system-missing-factor-4.2.arxml` | **Loads.** 16 identifiables, 5 references, all `resolved`. |
| both of the first two together | 174 identifiables, 194 references: 132 `resolved`, 55 `unresolved_definition`, 7 `dangling`. |
| `system-3.2.3.arxml`, `system-DAI-3.1.2.arxml`, `system-bad-root-tag-4.2.arxml`, `system-illegal-namespace-4.2.arxml`, `system-illegal-version-4.2.2.1.0.arxml` | **Refused**, `unsupported-dialect`. |

Selected `tag_counts` for `system-4.2.arxml`: `I-SIGNAL` 19, `SYSTEM-SIGNAL` 13, `CAN-FRAME` 8,
`CAN-FRAME-TRIGGERING` 8, `I-SIGNAL-I-PDU` 7, `I-SIGNAL-TO-I-PDU-MAPPING` 23,
`PDU-TO-FRAME-MAPPING` 7, `COMPU-METHOD` 5, `ECU-INSTANCE` 3.

Example identities that must come out exactly: `/Cluster/Cluster0/Pch0/message1` (a frame
triggering, four levels deep through transparent wrappers) and
`/ISignalIPdu/message1/Signal1` (a signal-to-PDU mapping).

---

## 9. Amendment to `SourceItem` (work-packages Part B1)

- `kind_hint` gains the values in §5.
- A link is `{type, target_id, role, context}`. `role` is the format's own name for the link
  (string, may be empty). `context` is a list of item IDs, empty except for instance references.
- Readers written earlier (DBC, PDF, ReqIF) set `role` to `""` and `context` to `[]`.

---

## 10. Cards

Rules of Part A in the work-packages file apply to every card. All code goes in
`mcp-server/grap_mcp/readers/arxml.py`; tests in `mcp-server/tests/test_reader_arxml.py`.

| Card | Tag | Does | Test to write first, and what it must show |
|---|---|---|---|
| **AR-01** Fixtures | [S] | Copy the nine files above and their `README.md` from VSS-engine `tests/fixtures/arxml/` to `mcp-server/tests/fixtures/arxml/`. No code. | File hashes equal the originals. |
| **AR-02** Refusals and index | [S] | §3 and steps 1–2, 5 of §4.1. Returns the index. | The five refused files raise with `unsupported-dialect`; `system-4.2` gives 157 identifiables, 22 packages, and both example identities of §8. A 30-byte file containing `<!DOCTYPE` raises `dtd-not-allowed`. |
| **AR-03** References | [M] | Steps 3 of §4.1, all of §4.2 and §4.3, the conservation assertion. | All reference counts of §8 for the first four loadable files. A synthetic file with a global base on an outer package resolves; the same with `IS-GLOBAL` false gives `unresolvable_relative`. **Then delete one category from the sum and see the assertion fire; restore it.** |
| **AR-04** Items and attributes | [S] | §5: build `SourceItem`s, flattening, `part_of`, placeholders. | `/ISignalIPdu/message1/Signal1` has attributes `START-POSITION` and `PACKING-BYTE-ORDER` and a `refers_to` link with role `I-SIGNAL-REF`; the dangling fixture yields exactly one `unresolved` item. |
| **AR-05** Instance references | [M] | §4.5. | Synthetic file: one `-IREF` with two context children and one target gives one link with a two-entry `context`. An `-IREF` with no `TARGET-` child lands in `malformed_irefs`. |
| **AR-06** Configuration values | [S] | §6. | In the ECU extract, the container `/MyEcu/Com/ComConfig/Message3` has attribute `param.ComIPduDirection` = `RECEIVE` and a link with role `ComPduIdRef` to a placeholder; `/MyEcu/Com/ComConfig/Message2` has three links with role `ComIPduSignalRef`, to `Signal2`, `Signal3` and `Signal4`. |
| **AR-07** Inventory tool | [S] | `scripts/arxml_inventory.py`: prints, for a file set, only tag names, `DEST` values, role names, instance-reference child shapes and their counts — **never a short name, a path or a value**. | Output for `system-4.2` contains no string from the file's `SHORT-NAME`s. |
| **AR-08** Multi-file and duplicates | [M] | §4.4, sorted file order, the two-file expectation of §8. | The two-file counts; a synthetic pair defining the same non-package path with different tags yields one `path_conflicts` entry. |
| **AR-09** Wire-in | [S] | Register `.arxml` in ingestion (card R-06). | A folder with the two main fixtures ingests to 174 items plus placeholders. |

Order: AR-01 → AR-02 → AR-03 → AR-04 → (AR-05, AR-06, AR-07, AR-08 in any order) → AR-09.

### Stop conditions for every AR card
- A count in §8 cannot be reproduced. **Do not adjust the expectation.** Report the difference.
- The conservation assertion fails on any input. Report the input's category counts.
- On a real file set, `dest_mismatch` exceeds 1% of references, or `malformed_irefs` is non-zero.
  Both mean the standard's shape differs from what this spec assumed.

---

## 11. What still needs a capable model or the owner

| Item | Why |
|---|---|
| **Confirm §4.5 and the ○ rows of §5 on real files** [H + L] | No public fixture contains software components or instance references. The owner runs AR-07 on real files — its output is safe to share because it contains no names — and a capable model compares it with this spec. |
| **`IS-GLOBAL` semantics** [L] | Taken from the standard's wording, not from a file. Confirm against the Generic Structure Template. |
| **True merging of split elements** [L] | §4.4 keeps the first definition. If real deliveries split scalar attributes across files, a merge rule is needed and it is not obvious. |
| **Which relations collapse into edges in the graph** [L] | The reader emits mapping and triggering objects as items of kind `relation`. Whether the graph shows them as nodes or folds them into edges is a schema decision (plan Phase 4). |
| **Memory for very large sets** [M] | The reader parses each file whole. Past a few hundred megabytes a streaming parse is needed; the 500 MB refusal makes the limit explicit meanwhile. |
