# Spec — the procedure runtime (card P-01)

**Status:** approved for implementation. The six decisions of §10 were delegated to the spec
author by the owner and are recorded there.
**Date:** 2026-11-13.
**Decides:** what it means to *execute* a procedure graph, for one real procedure: generating a
test specification from a set of requirements.
**Depends on:** the schema projection (S-03), statements (spec-statements), exact linking (E-14,
E-15). **Is measured by:** Sheet 4 of the automotive gold set (spec-testpoint-tooling).

This is the product step described in `what-we-would-like-to-do.md`. Nothing built so far does it.

---

## 1. Where things stand

The September audit reproduced this: a procedure file containing an operation that does not exist
and a guard that is false ran to `status: ok`. Today a procedure file selects a scope for a gap
check; its `operation`, `inputs`, `outputs` and `guards` are descriptive only.

What does exist and is reused: the procedure file format and loader, versioned publishing of
procedure files, tracked runs (`start_run`, `complete_run`), and an event log with typed links
(`record_event`, `events_for_run`, `events_linked_to`).

---

## 2. Scope of this version

**One procedure, executed honestly.** Small enough to verify end to end.

In: a closed set of eight operations; data passed between steps; simple guards; one bounded
refine loop; an event per step; a structured test specification as output.

Out, on purpose: sub-procedures, parallel steps, general loops, free-form guard expressions,
a visual editor, automatic promotion of anything generated.

---

## 3. The rule that fixes the audit's finding

**A procedure file is checked completely before anything runs.** It is refused, with every
problem listed, if:

- a step's `operation` is not one of the eight in §4;
- a step lacks an input its operation requires, or names one it does not take;
- an input refers to `step-x.outputs.y` and no earlier step produces that output;
- a guard is not of the form in §6;
- two steps share an `id`.

There is no "unknown, so skip". The audit's reproduction must end in a refusal that names the
unknown operation.

---

## 4. The eight operations

Each has fixed inputs and outputs. All are deterministic except `invoke_model`.

| Operation | Takes | Gives | What it does |
|---|---|---|---|
| `select_requirements` | a scope: IDs, a source, or a parent requirement | `requirements` | resolves the scope to `Requirement` nodes; an ID that matches nothing is an error, not an empty set |
| `fetch_context` | `requirements` | `context` | for each requirement, the nodes within two steps of its statements over the relations of schema §9 question 4; returns node keys, grouped by requirement |
| `choose_template` | `requirements`, optional template ID | `template` | the named `Template`, or the one linked by `uses_template`; none found is an error |
| `compose_instructions` | `requirements`, `context`, `template`, optional `issues` | `instructions` | builds the model input from fixed text plus the items' own text; no model |
| `invoke_model` | `instructions` | `draft` | one model call with a JSON schema for the output of §5 |
| `validate_output` | `draft`, `requirements`, `context` | `issues`, `passed` | the checks of §7; no model |
| `refine` | `issues` | `issues` | passes the issues back to `compose_instructions`; counts the round |
| `emit` | `draft`, `template`, `passed` | `artifact` | stores the test specification as a version and renders it through the template |

The `context` of `fetch_context` is exactly what Sheet 4's `needs_from_elsewhere` column is scored
against. That makes the most important part of the procedure measurable before any text is
generated.

---

## 5. The output: a structured test specification

The model fills a structure; it does not write a free document.

A specification is a list of **test cases**. Each has: `id`; `verifies` (requirement IDs, and
statement IDs where a case covers one clause); `preconditions`; `stimulus`;
`expected_observable_result`; `pass_criterion`; `variant` (optional); `kind` (`positive` or
`negative`); and `uses` — the node keys from the context this case relies on.

These fields are the columns of Sheet 4, so a generated case and a labelled one line up one to one.

The template decides how that structure is laid out as a document. The structure, not the
rendering, is the stored artifact.

### 5a. Template format

A template is one Markdown file under `templates/test-spec/`, with a short header block:

```
---
id: default
name: Default test specification
level: software
---
# Test specification: {{title}}

{{#cases}}
## {{id}} ({{kind}})
Verifies: {{verifies}}
Preconditions: {{preconditions}}
Stimulus: {{stimulus}}
Expected result: {{expected_observable_result}}
Pass criterion: {{pass_criterion}}
{{/cases}}
```

- `{{name}}` is replaced by a field of the specification or, inside `{{#cases}} … {{/cases}}`, of
  the current test case. The block repeats once per case. Lists are joined with `, `.
- Nothing else is supported: no conditions, no nesting, no expressions. A placeholder that names
  no field is a load error. No template library is added.
- `level` in the header is copied onto every test case.
- One template, `default.md`, ships with the runtime so the procedure can run before the owner
  supplies real ones.

---

## 6. Guards and the one loop

A guard is `{ref, op, value}` with `op` one of `eq`, `ne`, `gt`, `lt`, `empty`, `not_empty`. A step
whose guard is false is skipped, and the skip is an event.

The only loop: when `validate_output` gives `passed = false`, `refine` sends the issues back to
`compose_instructions`, at most `max_rounds` times (default 2, set on the `refine` step). When the
rounds run out the run ends as **`failed-validation`**, the last draft is kept as a rejected
artifact, and nothing is emitted. It does not quietly emit the best attempt.

---

## 7. Validation — every check is mechanical

| Check | Fails when |
|---|---|
| Coverage | a selected requirement has no test case that `verifies` it |
| Grounding | a case's `uses` names a node that is not in the fetched context |
| No invented names | an identifier (by the rule of card R-09) appears in a case's text and does not resolve, by exact match, to a node in the context |
| Traceability | a case has an empty `verifies`, or names a requirement outside the selection |
| Completeness | a case lacks `stimulus`, `expected_observable_result` or `pass_criterion` |
| Negative cases | a requirement whose statements include an `error_handling` or an `exception` has no case of kind `negative` |

Each failure is an issue naming the case, the check and the offending value. Validation says
nothing about whether a test is *good*; it says the specification is complete, traceable and
uses only things that exist.

---

## 8. What is recorded

A run is a tracked run. Every step writes one event carrying: step ID, operation, the procedure
version, the hashes of its inputs and outputs, and links to every graph node it read or produced.
`invoke_model` also records model, prompt hash, token counts and duration. A skipped step, a
failed check and each refine round are events.

**Evidence snapshot.** `fetch_context` stores the list of node keys it returned with each node's
content hash. That is what makes "what did this run use?" answerable later, and it is what the
audit found missing: today a run records an ontology version and then reads whatever the graph
holds at that moment.

**What is claimed.** A run can be *explained* — every step, input and node is on record. It is
*replayable* only as far as the stored snapshot and a deterministic model stub allow. With a real
model the output can differ on a second run, and the record says so.

---

## 9. How it is accepted

| Check | Pass |
|---|---|
| The audit's reproduction | a file with an unknown operation and a false guard is **refused at load**, naming the operation |
| Static checks | one refused fixture per rule of §3 |
| Determinism | with the stub model, two runs give byte-identical artifacts and the same event sequence |
| Bounded loop | a stub that always fails validation ends as `failed-validation` after `max_rounds`, with no emitted artifact |
| Each validation check | one fixture that fails only that check |
| Evidence snapshot | changing a graph node after a run leaves the run's recorded hashes unchanged, and the difference is detectable |
| **Context against the gold set** | `fetch_context` output scored on Sheet 4 by `score_gold.py`: `testpoints_needs_recall`, with its interval. No threshold is set here; the first run is the baseline. |

---

## 10. Decisions (2026-11-14)

The owner delegated these six to the spec author. They are recorded as the author's choices, to be
revisited after the first real run (card P-17), when there is evidence instead of judgement.

| # | Question | Decision |
|---|---|---|
| 1 | Anything missing from the eight operations? | **No. The eight stand.** Human review happens after `emit` and is recorded as an event when it occurs; it is not a step the runtime executes. |
| 2 | More test-case fields than Sheet 4's? | **One: `level`.** It is copied from the template, never produced by the model. No priority and no environment in this version — nothing in the graph could ground them. |
| 3 | "No invented names" — strict? | **Strict.** An identifier the context lacks fails validation, even when it is correct. That turns a gap in the graph into a visible failure. |
| 4 | Negative case required when a requirement has error handling or an exception? | **Yes.** |
| 5 | Where do templates come from? | **A plain file format, defined in §5a, with one default template shipped.** The owner replaces or adds templates later. |
| 6 | When refinement runs out? | **The run fails and emits nothing.** The last draft is kept as a rejected artifact with its issues. |

---

## 11. Cards

| Card | Tag | Does |
|---|---|---|
| **P-10** Operation registry and static check | [M] | §3, §4 signatures. Fixes the audit reproduction. |
| **P-11** Interpreter | [M] | Data flow, guards, the bounded loop (§6). Stub operations only. |
| **P-12** `select_requirements`, `fetch_context` | [M] | Against the projected graph. Writes the scorer's `testpoints` output. |
| **P-13** `validate_output` | [M] | §7, one check per test. |
| **P-14** Events and evidence snapshot | [M] | §8. |
| **P-15** `choose_template`, `compose_instructions`, `emit` | [M] | Template rendering. |
| **P-16** `invoke_model` and the procedure file | [S] | The JSON schema of §5; `procedures/generate-test-spec.pg.yaml`; end-to-end with the stub. |
| **P-17** First real run | **[L] + owner** | On the real corpus with a local model; Sheet 4 scores; the side-by-side review. |

P-12 can be built and scored before any model is involved, and should be: it is the part the gold
set measures.
