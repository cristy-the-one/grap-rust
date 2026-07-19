# Review: Grok's "battle-plan" work (commits `4a96de8`..`fe0886f`, 2026-07-09 →)

> **Status (2026-07-19): all items resolved.** P0 fixed in `b1509b3` (suite
> 170F/29E → green), P1/P2 in `bde0b3f` (+ `GRAP_LLM_EXTRA_BODY` for local
> llama.cpp models), P3 + workflow hardening in the follow-up commit: adoption
> now really verifies `concept_hashes` (sha256 tamper-evidence, refusal on
> mismatch, audited) and core-verifies projected nodes; provider indirection
> unified on `s.get_provider`/`s.get_embedder` (D8); extraction projections no
> longer set `kg_path` (workdir-clobber risk); runtime artifacts gitignored;
> `KEE_WORKFLOW.md` documents the four-graph improvement loop with local LLMs.
> Suite: 211 passing; offline demo and live Bonsai-27B `--real` run both green.

**Scope.** 55 commits, ~1,200 insertions, almost all in `mcp-server/`, plus a small
Rust addition (`src/shard.rs`, `crates/pygrap/src/sharded_graph.rs`, `tests/content_hash.rs`).
Reviewed by reading the aggregate diff, cross-checking against the Rust bindings, and
running the test suite at HEAD and at the pre-Grok baseline (`4a96de8^`).

**Headline numbers.**

| | pre-Grok (`4a96de8^`) | HEAD |
|---|---|---|
| pytest (mcp-server/tests) | **195 passed, 0 failed** | **170 passed, 29 failed, 23 errors** |

The test suite was green before this work and is badly broken now. Nothing else in this
review matters as much as that fact: the changes were landed without running the tests.

---

## 1. Verdict in one paragraph

The *ideas* are mostly sound — a state class instead of a global dict, content-hash
provenance from the Rust core into KEE, a propose → review → adopt governance loop,
pagination on unbounded lists. The *execution* is unreliable: a broken compatibility
shim took out a third of the test suite, a default-format flip breaks three tools
outright at their new default, wire schemas don't match the data they encode, several
"features" are dead code that computes something and throws it away, and the reasoner
picked up a self-reinforcing scoring loop that quietly undermines the project's core
"deterministic governance" promise. The Rust content-hash work is the one part that is
genuinely well done end-to-end.

---

## 2. What's good (keep it)

- **Rust content-hash plumbing** (`src/shard.rs:400`, `crates/pygrap/src/sharded_graph.rs:115`,
  `tests/content_hash.rs`). `node_content_hash` / `verify_node` on `ShardRouter` and the
  pygrap bindings are clean, idiomatic, and covered by four decent tests including
  save/load round-trips and the deleted-node/bad-id edge cases. Best work in the batch.
- **`GrapServerState` as a direction** (`mcp-server/grap_mcp/server.py:62`). Replacing the
  mutable module-level dict with a class is the right call; the implementation needs fixes
  (below) but don't revert the concept.
- **The governance loop shape** — `extract_ontology` (propose, versioned) →
  `list/get_proposed_ontology` → `adopt_proposed_ontology` with mandatory rationale and
  audit event. This matches the project's philosophy (LLM proposes, human adopts).
- **`concept_hashes` persistence** in the KEE working graph and its SQLite store
  (`graph_store.py` migration, `working_graph.py` save/load) is coherent and survives
  round-trips; the exact-hash short-circuit before vector similarity in
  `WorkingGraph._resolve_outcome` is a reasonable dedup fast-path.
- **Pagination** on `get_audit_log`, `list_runs`, `query_events` — overdue and harmless.
- **`telemetry.py` itself** is a tidy little module (opt-in, locked, atomic pulse writes).
- **`KEE_DEMO_CLIENT_TUTORIAL.md`** and the demo rework are useful onboarding assets.

---

## 3. Critical defects (fix before anything else)

### C1. The `_state` dict-protocol shim does not work → 52 test failures/errors

`server.py:164`:

```python
_state.__getitem__ = lambda k: getattr(_state, k, None)
_state.__setitem__ = lambda k, v: setattr(_state, k, v)
```

Python looks up dunder methods **on the type, not the instance**, so `_state["kg"] = x`
still raises `TypeError: 'GrapServerState' object does not support item assignment`.
The comment says this exists to support test monkeypatches — it doesn't; every test using
`monkeypatch.setitem(s._state, ...)` fails. This single mistake accounts for the bulk of
the 29 failures + 23 errors.

**Fix (5 lines):** define real methods on the class:

```python
class GrapServerState:
    def __getitem__(self, k): return getattr(self, k, None)
    def __setitem__(self, k, v): setattr(self, k, v)
    def get(self, k, d=None): return getattr(self, k, d)
```

(and delete the three instance-attribute lambdas).

### C2. `_get_workspace()` now wipes a freshly loaded KG

`_get_workspace()` calls `_state.reset()` on first open. But `load_knowledge_graph` /
`create_knowledge_graph` set the KG **first** and only then touch the workspace (to write
the audit event). In a fresh session the sequence is: KG loaded → workspace opens →
`reset()` → **KG silently discarded**. `test_ingest.py::test_ingest_feeds_reasoner` fails
with exactly this ("Load a Knowledge Graph first" after a successful create). The demo
only survives because it happens to open the workspace before creating the KG.

**Fix:** don't reset volatile state inside a lazy getter. If reset-on-open is wanted,
do it explicitly in a `reset_session` tool, or reset *before* any tool logic runs —
never in the middle of one.

### C3. The compact-wire default flip breaks three tools and corrupts a fourth

`encoding/__init__.py` changed `default_format()` from `"json"` to `"compact"`. Problems,
in increasing severity:

1. **Breaking API change for every MCP client** — tools now return a `#MUNCH/1` string
   instead of a dict by default, and the module's own test
   (`test_json_is_default_passthrough`) still asserts the JSON default. The docstring
   used to say "compact is opt-in **until a client decoder ships**" — no decoder shipped;
   the precondition was simply deleted.
2. **`get_blast_radius` and `find_orphans` error out at their default settings.**
   The Rust bindings return plain ints (`Vec<u32>` → `list[int]`,
   `crates/pygrap/src/indexed_graph.rs:646,663`), but `encode_response` does
   `row.get(c)` on every row — `AttributeError` on an int, caught by the tool's blanket
   `except`, returned as `{"status": "error"}`. The tools are dead unless the caller
   knows to pass `wire_format="json"`.
3. **`find_cycles`** — same problem (`list[list[int]]`); the schema comment even says
   "represent as stringified for wire POC" but no stringification code exists.
4. **`detect_gaps` silently loses its data.** The schema declares columns
   `["outcome_id", "characteristic", "score"]` but the real gap dicts use
   `missing_characteristic` and `confidence` (`reasoner.py:256`). Compact output encodes
   two columns of `None`. Tellingly, the demo itself calls
   `s.detect_gaps(wire_format="json")` — the incompatibility was noticed and worked
   around in the demo instead of being fixed.

**Fix:** revert `default_format()` to `"json"` (one line) until (a) a client decoder
exists, (b) every schema is validated against the actual tool output shape in a test
that round-trips through `pygrap.wire`, ideally property-based per tool. The schemas for
int-list tools need either dict-wrapping (`{"id": n}`) or scalar-list support in the
encoder before they can ship.

### C4. `to_yaml` header broke the pipeline contract test

The new header comment line contains the word "provenance", and
`test_kee_pipeline` asserts `"provenance" not in yaml_text` (the loadable file must not
contain provenance *data*). Trivial, but it's another test that would have failed on the
first local run.

---

## 4. Design concerns (worth a decision, not just a patch)

### D1. The session-boost feedback loop makes gap detection non-reproducible

`reasoner._lexical_signals` now: (a) records every hit node into the session journal, and
(b) multiplies scores ×1.25 for nodes already in the journal. So running `detect_gaps`
twice in a row can produce different scores — the first run's accesses boost the second
run's hits. This is a self-reinforcing loop, and it contradicts the system's central
claim (stated in `demo_kee.py` itself): *"Governance stays deterministic even when the
extractor isn't."* Explanations are persisted as versioned, auditable records — they
should not depend on what else happened in the process that hour.

**Proposal:** session boost is a fine idea for *interactive exploration* tools
(`get_context_bundle`, `suggest_queries`). Exclude it from **production runs**: pass an
explicit `session_boost=False` from `_run_gap_detection`, or gate it on run kind. At
minimum, record `session_boosted` into the persisted explanation (it's currently only in
the transient signal dict) so audits can see it.

### D2. Default reasoner weights changed silently, with two conflicting values

`DEFAULT_WEIGHTS` moved from `{text 0.7, graph 0.3}` to `{text 0.6, graph 0.2,
centrality 0.2}` — a production scoring change with no eval, no test update, no doc.
Worse, workspaces with a persisted config lack `centrality_weight`, and the inline
fallback there is `0.15`, not `0.2` — so fresh installs and existing installs score
differently. Pick one number, define it in one place, and note the change somewhere a
human will read it.

### D3. `get_role()` writes on read

Reading the role persists it into the workspace config, including when it came from the
`GRAP_ROLE` env var — so a one-off `GRAP_ROLE=admin python ...` **permanently** writes
`role: admin` into the governed config. An env override should win for the process, not
be committed to disk. Persist only in `set_role`.

### D4. `set_role` now requires admin — bootstrap paradox

Default role is `operator`; `set_role` requires `admin`. The only way to ever become
admin is the env var (which, per D3, then gets persisted). If that's the intended
bootstrap story, write it down in the tool docstring; otherwise this locks users out of
role management entirely.

### D5. Instrumentation is copy-pasted 12+ times and double-counts

The same four-line `try: import session/telemetry ... except: pass` block appears in at
least a dozen places (`server.py`, `reasoner.py`, `working_graph.py`,
`graph_extract.py`). Meanwhile the `tiered` decorator *already* records telemetry for
every tool call, and `encode_response` records again, and some tools
(`load_ontology`, `adopt_proposed_ontology`, `enforce_retention`, `get_proposed_ontology`)
record a *third* time inline — so per-tool call counts are inflated 2–3×.

**Proposal:** one decorator owns telemetry (keep it in `tiered`), delete every inline
`_tel.record` in tool bodies. For the session journal, add a tiny helper
(`session.try_record_query(...)`) or a decorator, and delete the copy-pasted blocks.
`working_graph.upsert`'s `record_node_access(0, "working_graph_upsert")  # placeholder`
is literally junk data — remove it.

### D6. Dead and "theater" code that claims more than it does

- `ingest.py`: computes `kg.get_node_content_hash(nid)` per node and **discards it**
  (the comment admits it: "for now just computed"). Pure per-node overhead sold in the
  commit message as "capture core content hash in main ingest".
- `adopt_proposed_ontology`: the "verify concept_hashes" block increments `verified`
  on mere *presence* of the hash attribute — verifies nothing — then logs
  "sampled %d" as if it did. Either call `verify_node`/compare hashes for mapped nodes,
  or delete the block; a fake verification in a governance path is worse than none.
- `get_proposed_ontology`: contains a `try: pass except: pass` block for hashes.
- `reconcile.py`: the "highest-leverage exact dedup by content hash" is sha256-hashing
  strings to detect *string equality* — `list(dict.fromkeys(names))` does the identical
  job with no hashing and no "same primitive as the core" mystique.

### D7. `list_proposed_ontologies` is half-built and has a shadowing bug

`page, meta = paginate(...)` is later overwritten by `meta = getattr(v, "metadata", ...)`
inside the loop, so the returned `**meta` spreads the *last node's metadata dict*, not the
pagination info. The body's own comments concede the primary path doesn't really work
("list_versions is PG-focused"; the audit fallback is what actually fires). Also, the
audit fallback re-paginates a *different* list with the same cursor — cursors from one
shape are meaningless against the other.

**Proposal:** rename the inner variable, and either extend the workspace binding with a
real `list_ontology_versions()` (small Rust/pyo3 addition — the data is there) or make
the tool honestly audit-log-based and document that.

### D8. Mixed indirection for provider/embedder factories

`server.py` imports `get_provider` both as a from-import (line 33, used at line 644) and
via module attribute (`llm.get_provider`, line 385). The demo monkeypatches the module
attribute, which only intercepts one of the two paths. Pick one indirection point
(module-attribute lookup) everywhere, as the comment at line 32 already promises.

---

## 5. Process observations

- **55 commits, none apparently test-verified.** Many are one-to-six-line commits with
  escalating labels ("highest-leverage", "massive agent token win", "non-banal"). Several
  commit messages claim things the diff doesn't do (C3, D6). Prefer fewer, verified
  commits; the label belongs in the code's behavior, not the message.
- **No CI gate for `mcp-server/tests`.** A GitHub Actions job running
  `pytest mcp-server/tests` (with `pygrap` built once and cached) would have caught all
  of section 3 on the first push. This is the single highest-value process fix.
- **`.venv` can't run the tests.** It lacked `pytest`, `pyyaml`, and `mcp` (I installed
  `pytest`, `pyyaml`, `mcp`, `fastmcp` to produce the numbers above). Add a
  `requirements-dev.txt` or a `[project.optional-dependencies] dev` extra in
  `mcp-server/pyproject.toml` so "how do I run the tests" has one answer.
- **Repo hygiene:** runtime artifacts are accumulating untracked — `_grap_pulse.json`
  (root *and* `mcp-server/`), `grap-workspace/`, `out/`, `terminals/`, `demo - Copy/`.
  Add `.gitignore` entries for `_grap_pulse.json`, `grap-workspace/`, `out/`,
  `terminals/`; delete or relocate `demo - Copy/`.

---

## 6. Prioritized action list

| # | Action | Effort | Files |
|---|--------|--------|-------|
| P0-1 | Revert `default_format()` to `"json"` | 1 line | `encoding/__init__.py` |
| P0-2 | Real `__getitem__`/`__setitem__`/`get` methods on `GrapServerState`; delete instance lambdas | ~8 lines | `server.py` |
| P0-3 | Remove `_state.reset()` from `_get_workspace()` | 1 line | `server.py` |
| P0-4 | Fix/relax the `to_yaml` header vs. test conflict | 2 lines | `kee/pipeline.py` or test |
| P0-5 | Run `pytest mcp-server/tests` until green; add CI job | small | `.github/workflows` |
| P1-1 | Fix wire schemas: `detect_gaps` column names; int-list tables for blast/orphans/cycles (or drop those schemas) + round-trip tests per schema | medium | `encoding/schemas.py`, tests |
| P1-2 | Gate session boost out of production `detect_gaps` runs | small | `reasoner.py`, `server.py` |
| P1-3 | Deduplicate instrumentation: telemetry only in `tiered`; session helper; remove inline copies and the node-0 placeholder | medium | server/reasoner/kee |
| P2-1 | `list_proposed_ontologies`: fix `meta` shadowing; back it with a real workspace listing | medium | `server.py` (+ pyo3) |
| P2-2 | Role handling: no write-on-read; document (or redesign) admin bootstrap | small | `server.py` |
| P2-3 | Reconcile weight defaults (0.2 vs 0.15) into one constant; note the scoring change | small | `reasoner.py` |
| P2-4 | Delete dead code: ingest hash no-op, fake adopt verification, empty try blocks, sha256 string-dedup | small | ingest/server/reconcile |
| P3 | Make adopt verification real: map proposal `concept_hashes` to projected nodes and call `verify_node` | medium | `server.py`, `working_graph.py` |

A realistic sequence: P0 items are one short session and restore a green suite; P1 makes
the wire/telemetry story honest; P2/P3 are cleanups you can fold into normal work.
