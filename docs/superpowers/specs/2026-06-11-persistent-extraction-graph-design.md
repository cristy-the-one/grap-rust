# Persistent Extraction Graph (Phase 2) — Design

**Date:** 2026-06-11
**Status:** Approved design (pre-implementation)
**Builds on:** Phase 1 — `docs/superpowers/specs/2026-06-10-graph-augmented-ontology-extraction-design.md` (committed on `master`: `WorkingGraph`, `graph_extract_ontology`, resolve-or-create, relation grounding, signals).

## Problem

Phase 1 builds a graph *during one extraction run* and throws it away: `WorkingGraph` holds its state (concepts, characteristics, relations, provenance) in memory, never saves, and deletes its owned temp dir. Re-running over the same corpus re-extracts everything from scratch.

What persists today, and what does not:
- **Persists:** the pygrap KG (`with_merge_policy(path)` loads-if-exists; `save()`/`save_to_dir()` write nodes, embeddings, edges, vector index) and the `EntityIndex` sqlite (aliases).
- **Does NOT persist:** all of `WorkingGraph`'s extraction metadata — `chars`, provenance (`node_sources`, `_rel_sources`), relation types, `rel_vocab`, `parked`, and the outcome-node set.
- **No edge enumeration:** pygrap exposes `edge_count` and relation-filtered `neighbors_by_relation`, but **no way to list all edges** — so relations cannot be reconstructed by walking the graph in Python. A metadata sidecar is therefore required.
- **No `remove_edge`:** pygrap can tombstone a node (`remove_node`) but cannot delete a single edge between two still-live nodes.

## Goal

Make the extraction graph a **persistent, queryable product**: save / open / extend a durable graph across runs, keeping it faithful to the current sources, with the candidate-ontology YAML as one exported view. Reuse `incremental.py`'s content-hash manifest so unchanged docs are never re-extracted.

### Decisions locked during brainstorming

1. **Re-ingest = sync with retraction.** Track each chunk's contributions by provenance. New chunk → extract & add. Changed chunk (content hash differs) → retract its old contributions, then re-extract. Removed doc → retract its contributions. The graph stays faithful to current sources.
2. **Sidecar is the source of truth; pygrap + EntityIndex are rebuilt projections.** A `GraphStore` (sqlite) holds the authoritative state. Retraction edits the store (cheap). On save, the pygrap KG + EntityIndex are regenerated from the live store — so retracted edges and orphaned concepts simply aren't re-projected, sidestepping the missing `remove_edge`. **No new Rust.** Durable identity is the `concept_id` / canonical name, not the churny pygrap node id.
3. **Query surface = reuse existing KG tools.** On open, load the projection into the server's `_state["kg"]` so `graph_neighbors`, `resolve_entity`, `get_blast_radius`, `search_and_traverse`, and analytics work for free. Add only the missing lifecycle tools: open / extend / export.

Offline determinism (injected `StubProvider` + `HashingEmbedder`) and provenance remain engineering constraints.

## Architecture & components

Five units; the durable graph lives at a caller-specified `workdir`. Phase-1 logic is extended, not rewritten.

- **`kee/graph_store.py` — `GraphStore`** (new, source of truth). Versioned sqlite at `<workdir>/graph_store.db` (the `EntityIndex` migration pattern). Pure persistence: load-all / save-all of `WorkingGraph`'s durable state + `retract_chunk`. No graph logic.
- **`kee/working_graph.py` — `WorkingGraph` (extended)**. Re-keyed from pygrap node id to stable `concept_id` (a thin `_node_of`/`_concept_of` map bridges the ~4 pygrap boundary calls). Gains load-from / save-to `GraphStore`, per-chunk contribution tracking, `retract_chunk`, and `_project` (rebuild pygrap + EntityIndex from the store).
- **`kee/graph_extract.py` (extended)** — `extend_graph_ontology(workdir, docs_path, provider, embedder, …)`: open → diff manifest → retract changed+removed → run the Phase-1 budgeted loop over new+changed → update manifest → save. Returns sync stats + ontology.
- **`server.py` — lifecycle tools**: `open_extraction_graph`, `extend_extraction_graph`, `export_ontology` (thin wrappers, "full" tier, using `get_provider`/`get_embedder`).
- **Projection** — regenerate pygrap KG + EntityIndex from the live store on open and save.

### Unit contracts

| Unit | Does | Key interface | Depends on |
|---|---|---|---|
| `GraphStore` | persist/restore the durable state (no graph logic) | `open(path)`, `load()→state`, `save(state)`, `manifest()`/`set_hash()` | sqlite (stdlib) |
| `WorkingGraph` (ext.) | live graph logic + persistence + retraction + projection | `open(workdir, embedder, …)`, `upsert_fragment`, `retract_chunk`, `save()`, `to_candidate_ontology`, `context_for` | pygrap, `EntityIndex`, `GraphStore`, embedder |
| `graph_extract` (ext.) | the extend orchestration | `extend_graph_ontology(workdir, docs_path, provider, embedder, …)` | `WorkingGraph`, `extract`, `incremental.chunk_hash` |
| `server.py` tools | MCP lifecycle | `open_extraction_graph`, `extend_extraction_graph`, `export_ontology` | `graph_extract`, `WorkingGraph`, `get_provider`/`get_embedder` |

## Data flow (one `extend` run)

1. **`open(workdir)`** — load the `GraphStore` into an in-memory `WorkingGraph` and `_project()` it into a live pygrap KG + `EntityIndex` (so `vector_search` resolution and the query tools have a populated graph). Fresh `workdir` → empty.
2. **Diff** — `read_chunks(docs_path)`, `incremental.chunk_hash` each, compare to the stored manifest → **new** (unseen id), **changed** (hash differs), **removed** (in manifest, absent now).
3. **Retract** — for every changed + removed chunk, `retract_chunk`: delete its characteristics, edges, and provenance from the in-memory truth; a concept whose provenance becomes empty is dropped with its edges.
4. **Extend** — run the Phase-1 budgeted loop over **new + changed** chunks; context comes from the current (post-retraction, accumulated) graph.
5. **Manifest** — record new hashes for new/changed; forget removed.
6. **Save** — persist the `GraphStore` **first** (truth durable), then `_project()` (rebuild pygrap + EntityIndex from the live store) and `kg.save()`.

Unchanged chunks are never sent to the LLM — the cross-run efficiency win.

## `GraphStore` schema & retraction model

Sqlite, versioned migrations. This is the persisted shape of `WorkingGraph`'s state; retraction runs in-memory and is mirrored here on save.

```
concepts(concept_id INTEGER PK, canonical TEXT)
aliases(alias TEXT, concept_id INT, PK(alias, concept_id))
characteristics(concept_id INT, text TEXT, PK(concept_id, text))
edges(edge_id INTEGER PK, from_id INT, to_id INT, type TEXT, UNIQUE(from_id, to_id))
chunks(chunk_id TEXT PK, content_hash TEXT)                          -- change-detection manifest
concept_sources(concept_id INT, chunk_id TEXT, PK(concept_id, chunk_id))
char_sources(concept_id INT, text TEXT, chunk_id TEXT, PK(concept_id, text, chunk_id))
edge_sources(edge_id INT, chunk_id TEXT, PK(edge_id, chunk_id))
parked(from_concept TEXT, type TEXT, to_concept TEXT, chunk_id TEXT, reason TEXT)
meta(key TEXT PK, value TEXT)                                        -- rel_vocab(JSON), schema_version
```

`UNIQUE(from_id, to_id)` on `edges` enforces first-writer-wins on type (Phase-1 semantics, now durable).

**`retract_chunk(chunk_id)`** (in-memory; mirrored on save):
1. Drop this chunk's `char_sources`; any characteristic with no remaining source → delete it.
2. Drop this chunk's `edge_sources`; any edge with no remaining source → delete the edge.
3. Drop this chunk's `concept_sources`; any concept with **no remaining provenance** → delete the concept, its aliases, characteristics, and any edge touching it (cascade those edges' sources).
4. Drop this chunk's `parked` rows.

A fact survives exactly as long as some current chunk still asserts it. `rel_vocab` is recomputed from surviving edges at save.

**In-memory additions to `WorkingGraph`:** it already has `node_sources` (concept provenance) and `_rel_sources` (edge provenance); Phase 2 adds `char_sources` and a per-chunk reverse index built during `upsert_fragment`, so `retract_chunk` is O(chunk's contributions).

## Identity across projection rebuilds

Because the projection is rebuilt, **pygrap node ids are not stable** — durable identity is the `concept_id`.

- `GraphStore` assigns a monotonic `concept_id` per concept (stable, never reused, gaps allowed after deletion).
- `WorkingGraph` keys all state by `concept_id` and keeps a thin bidirectional map `_node_of[concept_id] ↔ _concept_of[node_id]`, consulted only at the pygrap boundary calls (`add_node`, `vector_search`, alias `resolve`, projection).

**`_project()`** (on open and save):
1. Fresh on-disk pygrap KG at `<workdir>/graph` + `EntityIndex.clear()`.
2. For each live concept in `concept_id` order: `nid = kg.add_node(canonical, embedding=embed(canonical))`; record both map directions; register every alias → `nid` in `EntityIndex`.
3. For each live edge: `kg.add_edge(_node_of[from], _node_of[to], 1.0, {"rel": type})`.
4. `kg.save()`.

Concept embeddings are recomputed as `embed(canonical)` at projection (deterministic; keeps vector bulk out of the store). **Rejected alternative:** create+tombstone replay to force `node_id == concept_id` — every retracted concept would linger as a tombstone in every future projection (unbounded bloat).

## MCP lifecycle tools (`server.py`)

- `open_extraction_graph(path)` → project the store; set `_state["kg"]` to the projected KG and point `resolve_entity` at `<path>/entities.db`; return counts (concepts, edges, chunks).
- `extend_extraction_graph(path, docs_path, model=None, sim=0.86, relook_ratio=0.2, max_files=None)` → run `extend_graph_ontology`, persist, refresh `_state["kg"]`; return `{added, changed, removed, concepts, edges, relooks, parked}`.
- `export_ontology(path, out_path=None)` → `to_candidate_ontology` + `to_yaml`; optionally write `out_path`.

Thin wrappers, "full" tier, using `get_provider`/`get_embedder` (matching the existing `extract_ontology` tool). Determinism tests target the `kee`-level functions with injected stubs; server tools get signature/smoke coverage.

## Error handling

- Corrupt/missing store → treat as fresh (like `with_merge_policy`'s load-or-create), logged.
- Provider error on a chunk (no fragment) → non-fatal; the chunk's hash is **not** recorded in the manifest, so it's retried next run. A valid-but-empty extraction **is** recorded (nothing to extract there).
- Save ordering (store first, then project) guarantees the truth survives a projection failure; the next `open` re-projects.

## Determinism & testing

`kee`-level tests inject `StubProvider` + `HashingEmbedder`; server tools get signature/smoke tests.

- **`GraphStore`**: save→load round-trip (identical concepts/edges/chars/provenance/manifest); schema version/migration.
- **Retraction**: change a chunk → old facts gone, new present, untouched intact; remove a doc → its sole-source concept removed but a shared concept survives; an edge asserted by two chunks survives retracting one and vanishes when both are retracted.
- **`extend` end-to-end** over {unchanged, changed, new, removed}: manifest diff correct; **unchanged chunk triggers zero provider calls** (assert `StubProvider.calls`); ontology reflects the sync.
- **Projection consistency**: reopen yields identical `to_candidate_ontology`; `kg.edge_count()` == live edge count; a known alias resolves via the projected `EntityIndex`.
- **Cross-run accumulation**: run A, then B extends → B's context includes A's concepts (context-aware stub links rather than orphaning).

## Scope / non-goals (YAGNI)

- ❌ No new Rust — projection rebuild sidesteps `remove_edge`.
- ❌ No new ontology-query MCP tools — reuse existing KG tools via `_state["kg"]`.
- ❌ Single opened-graph handle; no multi-graph registry (open replaces the current one).
- ❌ No automatic file-watching (`watch_paths` exists but wiring it is out of scope; `extend` is explicit).
- ❌ No embedding persistence in the store (recompute at projection).
- ❌ Single-writer assumption (one `extend` at a time); no locking beyond sqlite's.

## Open questions for implementation planning

- Whether `GraphStore` and the projected `EntityIndex` should share one sqlite file or stay separate (`graph_store.db` vs `entities.db`). Lean: separate, because the projected `EntityIndex` is disposable/rebuilt while `graph_store.db` is the truth — keeping them apart avoids accidentally treating a projection as authoritative. Confirm during planning.
- Exact `extend` behavior when `docs_path` is a single file vs a directory (mirror `read_chunks`, which already handles both).
