# Graph-Augmented Ontology Extraction — Design

**Date:** 2026-06-10
**Status:** Approved design (pre-implementation)
**Scope:** Phase 1 of a phased effort. Phase 2 (persistent-KG-as-product) is sketched as a hook, not specified here.

## Problem

The current Knowledge Extraction Engine (`mcp-server/grap_mcp/kee/`) extracts an ontology in three stateless stages:

1. `pipeline.read_chunks` — split docs into ~800-char chunks.
2. `extract.extract_fragments` — **one independent LLM call per chunk** (`SYSTEM_PROMPT` + chunk text → JSON `{outcomes, relations}`). Each chunk is extracted blind: the model never sees what other chunks produced or what is already known.
3. `reconcile.reconcile` — **after** all extraction, embedding-based greedy clustering dedups names/characteristics; relations deduped by exact string key.

This "blob → static system prompt → deduce entities/relations → merge at the end" shape limits reliability:

- **Entity fragmentation** — the same real-world entity appears under synonyms across chunks and is only (imperfectly) merged at the end by embedding clustering.
- **Ungrounded relations** — the model can assert relations between concepts that nothing supports, with no structural check.
- **Missed coverage** — a chunk mentioning a known entity under a new alias has no way to link to it; the connection is lost.

## Goal

Lean into Grap: build and continuously update a live knowledge graph **inside** the extraction loop, and feed the relevant slice of that graph back into each LLM call — so extraction is informed by correlations already established, and refined dynamically. Optimize three reliability dimensions (in priority order, per stakeholder):

1. **Coverage / recall** — graph context surfaces entities/links a stateless pass misses.
2. **Relation grounding** — the model only commits relations it can ground in the text and in known entities.
3. **Entity consistency** — one node per real-world entity, resolved live, with aliases tracked.

Offline determinism and provenance are retained as **engineering constraints** (so the pipeline stays testable and auditable), not as headline goals.

### Decisions locked during brainstorming

- **KG role:** Both, phased. Phase 1 = context provider that hardens the existing candidate-ontology artifact. Phase 2 = the persistent, queryable Grap graph becomes the product; ontology YAML becomes one exported view.
- **Loop shape:** Adaptive / budgeted. Single streaming pass by default; a second "re-look" call is spent on a chunk only when the graph signals a conflict or a high-value new correlation, under a hard global budget.
- **Mechanism:** Approach **B** (working-memory graph with retrieved context block), with Approach **A** (vocabulary conditioning) baked in as a subset, and Approach **C** (inline entity-linking) reserved as a future *retrieval step inside* B if Phase-1 recall proves insufficient — not as a separate architecture.
- **Placement:** Loop orchestration in Python (MCP skin; it is I/O-bound on LLM calls). Retrieval rides existing Rust core primitives (`vector_search`, `search_and_traverse`, `entity_index`). **No new Rust in Phase 1.**

## Architecture & module boundaries

The new loop slots beside the existing `kee` pipeline; nothing in the current path is rewritten. Four units, each independently testable:

- **`kee/working_graph.py` — `WorkingGraph`** (the heart). Wraps a live in-memory `IndexedKnowledgeGraph` + an `EntityIndex` sidecar. Pure orchestration over pygrap, **no LLM**. Responsibilities:
  - `context_for(chunk_text) -> Context` — retrieve the relevant known subgraph.
  - `upsert_fragment(fragment, source) -> Signals` — resolve-or-create entities, add grounded edges, record provenance.
  - `to_candidate_ontology(version) -> dict` — emit the **same dict schema** `reconcile` produces today.
- **`kee/graph_extract.py` — the loop** (`graph_extract_ontology`). Drives chunks through retrieve → augment → extract → upsert; owns the augmented prompt and the adaptive budget. Depends on `extract.py` + `working_graph.py`.
- **`kee/extract.py`** — reused as-is for defensive JSON parse/normalize; gains one helper, `build_user_prompt_with_context`.
- **`kee/pipeline.py`** — `extract_ontology` gains a `graph_augmented=True` branch routing to the new loop; the old stateless path stays available for fallback/comparison.

`reconcile.py` is retained as a final safety-net dedup over the already-resolved graph, and as the old path's engine. The `WorkingGraph` is built as a *real* graph from day one, so Phase 2 is "persist + open + extend," not a rewrite.

### Unit contracts

| Unit | Does | Used via | Depends on |
|---|---|---|---|
| `WorkingGraph` | live KG + entity resolution + relation grounding + provenance + signals + export | `context_for`, `upsert_fragment`, `to_candidate_ontology` | pygrap (`IndexedKnowledgeGraph`, `vector_search`, `search_and_traverse`, `add_node`, `add_edge`, `merge_nodes`), `EntityIndex`, injected `embedder` |
| `graph_extract` | the adaptive loop + augmented prompt + budget | `graph_extract_ontology(chunks, provider, embedder, ...)` | `extract.py`, `working_graph.py` |
| `extract.py` (extended) | JSON parse/normalize + context prompt | `extract_json`, `normalize_fragment`, `build_user_prompt_with_context` | — |
| `pipeline.py` (extended) | route old vs graph-augmented | `extract_ontology(..., graph_augmented=True)` | both paths |

## Data flow (per chunk)

Single streaming pass, one LLM call per chunk by default:

1. **`context_for(chunk)`** — embed the chunk, `vector_search` the live graph for similar known concepts, `resolve` candidate surface mentions through the alias index, pull immediate neighbors via `search_and_traverse`. Output: a compact `Context` — canonical entity names + their characteristics, the relations already known among them, and the relation-type vocabulary seen so far (the last is Approach A, baked in).
2. **Augmented extract** — `build_user_prompt_with_context` prepends a `KNOWN ENTITIES / KNOWN RELATIONS / RELATION VOCABULARY` block to the chunk, with the instruction: *reuse these canonical names where they apply; only assert relations grounded in this text.* Same defensive JSON parse/normalize as today.
3. **`upsert_fragment(frag, source)`** — resolve-or-create each entity, add grounded edges, record provenance. Returns `Signals`.
4. **Branch** — no signal (or budget exhausted) → next chunk. A conflict or new cross-cluster correlation **and** budget remaining → one **focused re-look** (re-extract the same chunk with a narrowed context), then upsert again.
5. After all chunks → **`to_candidate_ontology()`** emits the existing dict/YAML schema.

The shift from today: retrieval + resolution happen **inside** the loop and feed the model, instead of every chunk being extracted blind and merged by embeddings only at the very end.

## Entity resolution & relation grounding

Two policies inside `upsert_fragment`.

### Resolve-or-create (→ entity consistency + alias recall)

**Terminology.** An *entity* here is a concept node, **created only from a fragment `outcome.name`** — relation endpoints never create nodes (see Relation grounding). An outcome's `characteristics` attach to its entity node: on resolve-or-create they are accumulated onto the node and deduped (reusing reconcile's string clustering at export), so `to_candidate_ontology` can emit `required_characteristics` per outcome. Because every node originates as an outcome, the export's `process_outcomes` is exactly the set of live nodes.

For each extracted entity name, in order:

1. **Normalize** the surface form (`tokenize`, reusing reconcile's normalizer).
2. **Exact alias hit** in `EntityIndex` → use that node.
3. **Embedding match** — embed the surface form, `vector_search`; if top score ≥ `merge_threshold` → use that node and `register_alias(surface_form → node)`. (The `stress_merge_nodes` pattern, applied live.)
4. **Else create** — `add_node(text, label, embedding)`, register both canonical and surface form as aliases.

An entity resolves to **one** node the moment it reappears under any variant; every variant is recorded as an alias. This replaces post-hoc clustering with live resolution, and the alias index lets a later chunk's "the test plan doc" link to the existing "Test Plan" node instead of spawning an orphan (the coverage win).

### Relation grounding (→ no hallucinated relations)

A relation `{from, type, to}` is admitted **only if both endpoints resolve to an existing graph entity OR to an outcome named in this same fragment** — relation endpoints are *resolve-only*, never resolve-or-create:

- Both endpoints ground → add a typed edge (`add_edge`, relation type carried as the single edge property `{"rel": type}`) with source provenance.
- An endpoint matches neither an existing entity nor a same-fragment outcome → the relation is **parked** (logged with its source for audit), not added, and no node is created for it.
- Conflict policy is first-writer-wins: if an edge already exists between the two nodes with a *different* type, the new assertion is parked (and raised as a conflict signal), never overwriting.

This makes "a relation between things that don't exist" structurally impossible to commit, while parking (vs hard-dropping) keeps an audit trail.

### Provenance

Every node carries the set of source chunks that mentioned it; every edge the chunk that asserted it — feeding the existing `provenance` field in the exported ontology and giving Phase 2 an audit trail for free.

### Tunable & risk

The single knob is `merge_threshold` (reuse the existing `sim`, default `0.86`). Too low → distinct entities wrongly merged; too high → duplicates survive. Both directions are covered in tests.

## Adaptive budget & signals

Spend a second LLM call **only** where the graph says it is worth it, with a hard global cap.

### Signals (emitted by `upsert_fragment`)

- **Conflict** — the fragment asserts something the graph already contradicts:
  - (a) a relation `(from, to)` whose `type` differs from an edge already between those two resolved nodes, or
  - (b) an **ambiguous** entity surface form — two existing nodes both score ≥ `merge_threshold` and are mutually close, so resolution is uncertain.
- **Correlation** — the fragment introduces a **bridge**: a relation linking two entities currently in *disconnected components* of the working graph (detected with a union-find/component check over the small graph). High-information; worth confirming before committing a structural change.

### Budget

A single global counter, `max_relooks = ceil(relook_ratio * n_chunks)` (default `relook_ratio = 0.2`). Each re-look decrements it; at zero, signals are recorded but no re-look fires. Worst-case cost is **1.2× the baseline call count** and fully predictable — no convergence loops.

### The re-look

A focused re-extraction of the *same* chunk with a narrowed context (the conflicting edge / ambiguous candidates / the two bridged neighborhoods) plus a sharper instruction (*"these two concepts may be the same / this link is new — confirm or correct, grounded in the text"*). The result re-enters the same `upsert_fragment`. If it still conflicts, keep the graph's existing state and **park** the disputed item (provenance preserved) rather than thrash.

Signals are computed from graph structure (edges, components, embedding scores) — no LLM call to *decide* whether to re-look, only to *perform* it. Adaptivity is free; only the payoff costs a call.

Knobs, both injected and defaulted: `relook_ratio` (`0.2`) and reuse of `merge_threshold` for the ambiguity test.

## Determinism & testing

**Determinism.** Provider and embedder stay injected — `StubProvider` + `HashingEmbedder` — so the loop runs offline and reproducibly. The streaming pass is order-dependent, but chunks are read in sorted order and stub+hashing make the run bit-for-bit deterministic. The budget is integer accounting. Real LLM providers plug into the same `provider.complete` seam with no loop changes.

**Tests (each unit in isolation):**

- `WorkingGraph` (no LLM, `HashingEmbedder` only):
  - (a) two surface variants of one concept resolve to **one** node carrying **both** aliases;
  - (b) a relation with an ungroundable endpoint is **parked**, not added;
  - (c) a grounded relation creates exactly one typed edge with provenance;
  - (d) `merge_threshold` swept both directions — too-low wrongly merges, too-high leaves duplicates.
- `graph_extract` (with a **context-aware `StubProvider`** that echoes a known entity name when the context block contains it — proving augmentation links rather than orphaning):
  - (e) a conflict triggers exactly **one** re-look and decrements the budget;
  - (f) budget exhaustion records the signal but fires no re-look;
  - (g) worst-case call count ≤ `1.2×` baseline.
- Back-compat:
  - (h) `to_candidate_ontology` output validates against the **same schema** the existing tool / `to_yaml` / adopt path consumes.

## Output & back-compat

The exported dict matches `reconcile`'s schema (`version`, `process_outcomes`, `mapping_hints`, `provenance`, `relations`). `reconcile.reconcile` stays as a final safety-net dedup over the already-resolved graph and as the old path's engine. The MCP `extract_ontology` tool gains `graph_augmented=True` (default on, with a flag to fall back to the stateless path). Aspice-gui wizard steps are unchanged.

## Phase-2 hook (persistent KG as product)

Because `WorkingGraph` *is* a real `IndexedKnowledgeGraph` + `EntityIndex`, Phase 2 is additive: `save_to_dir` the graph, persist the entity sidecar, and let a later run **open** and **extend** it (incremental-ingest paths already exist). The ontology YAML becomes one exported view; nothing in Phase 1 needs reworking.

## Scope / non-goals (YAGNI)

- ❌ No new Rust core in Phase 1 (revisit only if a retrieval hot path shows up in profiling).
- ❌ No multi-pass or convergence loops — adaptive single-pass + bounded re-looks only.
- ❌ No inline entity-linking component (Approach C) unless Phase-1 recall proves insufficient; if needed, it enters *as the retrieval step inside `context_for`*, not a new architecture.
- ❌ No new ontology schema — reuse the existing one.

## Open questions for implementation planning

- Exact `Context` rendering budget (how many known entities/relations to include before the prompt bloats) — start with a small cap (e.g. top-8 by vector score) and tune.
- Whether `WorkingGraph` should hold its own `EntityIndex` on a temp path (like `stress_merge_nodes`) or an in-memory variant — prefer temp-dir to reuse existing code; confirm during planning.

## Implementation status

Phase 1 implemented (2026-06-10) on branch `feat/graph-augmented-extraction`:
`kee/working_graph.py` (`WorkingGraph`, `Context`, `Signals`), `kee/graph_extract.py`
(`graph_extract_ontology` + bounded re-look budget), `build_user_prompt_with_context`
in `kee/extract.py`, and the `graph_augmented` branch in `kee/pipeline.py` (default
on; legacy stateless path via `graph_augmented=False`). Output schema unchanged.
Both resolved open questions as planned: temp-dir `EntityIndex`, top-`context_cap`
(default 8) retrieval. Full suites green (mcp-server 174, aspice-gui 8).

One refinement landed during implementation: the bridge-correlation signal only
fires when **both** endpoints already had an edge (pre-existing structure) — a first
edge between two freshly-created nodes is not a bridge. Phase 2 (persist + open +
extend) remains future work.
