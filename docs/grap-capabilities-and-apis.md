# grap & grap-MCP — Capabilities, APIs & Architecture

**An adopter's guide for building local, (optionally) AI-assisted Knowledge, Process, and Temporal (evidence/trace) graphs.**

> **One engine, three graphs.** `grap` is a Rust-native, read-optimized graph engine that stores everything as **nodes + property-weighted edges**. On top of it, `pygrap` (Python bindings) and `grap_mcp` (an MCP server) let you drive three *kinds* of graph from the same primitive:
> - **Knowledge Graph (KG)** — *what do we know?* (documents, entities, relations, hybrid text+vector+graph retrieval)
> - **Procedure / Process Graph (PG)** — *how do we do it?* (versioned, content-addressed execution logic projected into the graph)
> - **Temporal Graph (TG)** — *what happened, when, and why?* (append-only audit log + run records + immutable evidence/explanation artifacts, reconstructable on demand)
>
> Only the *meaning* differs; the storage, search, and persistence are shared.

This document is written for engineers and teams who want to **embed grap (the "rusty core")**, **script it from Python (`pygrap`)**, and/or **run the governed MCP surface (`grap_mcp`)** into their own pipelines. It focuses on *capabilities*, *APIs*, and the *architecture the Rust core enables* — independent of the bundled ASPICE reference application.

**Companion docs:** [`README.md`](../README.md) (engine quick-start), [`docs/architecture-grap-mcp.md`](architecture-grap-mcp.md) (the KG/PG/TG thesis + ASPICE narrative), [`mcp-server/CAPABILITIES.md`](../mcp-server/CAPABILITIES.md) (live MCP tool reference), [`core_graph_design.md`](../core_graph_design.md) (the engine design spec).

---

## 1. How to read this / which layer do you need?

You can adopt grap at three altitudes. They stack, and you can stop at any level.

| You want to… | Adopt | Language | What you get |
|---|---|---|---|
| Embed a fast graph + hybrid search into a Rust service | **`grap` crate** | Rust | Max performance, full control, no Python/IPC |
| Build KGs, ingest docs, run hybrid retrieval from notebooks/pipelines | **`pygrap`** | Python | The whole engine + lifecycle `Workspace` with a Pythonic API |
| Give an agent (or a team) a **governed, auditable** way to build & query graphs | **`grap_mcp`** | MCP (any client) | Roles, audit log, versioning, validation, reasoning, AI-assisted extraction |

```
Agents & Humans
      │  (MCP protocol)
      ▼
grap_mcp (Python)        ← governance · audit · validation · reasoning · KEE (AI extraction)
      │
      ▼
pygrap (PyO3 bindings)   ← engine + lifecycle Workspace, exposed to Python
      │
      ▼
grap (Rust engine)       ← the durable authority: graph + BM25 + vector + on-disk + sharding + Workspace
      │
      ▼
Filesystem workspace     ← content-addressed blobs · JSON records · JSONL audit log
```

Everything is **local-first**: data lives on your filesystem, embeddings/LLMs can be fully local (Ollama / sentence-transformers), and nothing phones home.

---

## 2. The architecture the "rusty core" makes possible

The engine is purpose-built for one workload: *store rich text nodes, retrieve them by traversing property-weighted relationships, with aggressive pruning and hybrid ranking.* The design choices (from [`core_graph_design.md`](../core_graph_design.md)) are what make local, large-corpus graph work fast.

| Mechanism | What it is | Why it matters for you |
|---|---|---|
| **Hot topology / cold blobs** | Adjacency + masks live in RAM as arena-allocated `Vec`s (`NodeId`/`EdgeId` handles); full text lives in a separate chunked/mmap `BlobStore` with zero-copy `&str` reads | Traverse millions of edges without touching text; load huge corpora via mmap without blowing RAM |
| **O(1) bitmask pruning** | Every edge carries a **64-bit property mask**; a node-level `outgoing_property_mask` lets traversal reject whole subtrees in constant time | "Only follow `requires`/`cites`/`depends_on` edges" is a bit-test, not a scan — 3–8× faster under selective filters |
| **BM25 seed selection** | An incremental inverted index over blob content finds high-quality *starting points* before graph expansion | Hybrid retrieval: text relevance picks seeds, the graph supplies structure/strength |
| **Vector / ANN index** | Pluggable `VectorIndex` trait — brute-force always, **HNSW** behind the `ann-hnsw` feature | Semantic seeds + lexical seeds + graph strength fused in one query |
| **On-disk segmented text index** | Always-on (no feature flag): WAL + tiered merge + true incremental `vacuum()` + background workers; `save_to_dir`/`load_from_dir` with automatic backend detection | Durable, append-friendly indexes that survive restarts and merge in the background |
| **Sharding + ghost edges** | `ShardedGraph` spreads nodes across shards by consistent hashing; cross-shard references become transparent **ghost edges**; explicit plan/execute rebalancing | Scale one logical graph horizontally with distributed traversal |
| **Content-addressed `Workspace`** | A lifecycle store: VersionStore + ActivationStore + RunStore + append-only AuditLog, all content-hashed | Turns the engine into a *reproducible* system: freeze logic + weights + knowledge, replay any run |
| **`Telemetry` trait** | No-op-by-default hooks (`record_traversal_nodes_visited`, `record_mask_prune`, `record_remote_fetch`, `record_rebalance`) | Wire the engine into Prometheus/OTel without the library depending on any metrics crate |
| **Soft deletes + node merge** | Tombstoned nodes; `merge_nodes(keep, absorb)` redirects edges, blends strengths, merges properties | Entity resolution / dedup without rebuilding the graph |

**Persistence is versioned and atomic.** `save_to_dir` stages into `.grap-new/` and commits by renaming `manifest.json` last; CRC32 checksums detect corruption; `load_meta`/`GraphMeta` inspect a saved graph (version, size, checksums) without loading it.

**Feature flags** (Rust crate; all additive, core works with none):

| Feature | Brings in |
|---|---|
| `fulltext` | BM25 inverted index + phrase search + hybrid fusion |
| `mmap` | Memory-mapped blob store (zero-copy reads) — `memmap2`, `crc32fast` |
| `sharding` | `ShardedGraph` + consistent hashing + ghost edges + rebalancing |
| `ann-hnsw` | `HNSWVectorIndex` + `HNSWParams` |

---

## 3. grap core — capabilities & API

### 3.1 The three Rust entry points

| Type | Availability | Use it for |
|---|---|---|
| `KnowledgeGraph` | always | Low-level arena graph: nodes, property-weighted edges, traversal with masks |
| `IndexedKnowledgeGraph` | `fulltext` | `KnowledgeGraph` + BM25 + hybrid `search_and_traverse`; in-memory **or** `new_on_disk()` |
| `ShardedGraph` | `sharding` | A multi-shard cluster facade with the same search surface + rebalancing |

```rust
use grap::{FusionWeights, IndexedKnowledgeGraph, PropertyValue};

let mut g = IndexedKnowledgeGraph::new();

let topic      = g.graph.properties.register("topic");
let confidence = g.graph.properties.register("confidence");

let rust      = g.add_node("Rust guarantees memory safety without garbage collection.", Some("Rust"));
let ownership = g.add_node("Every value has exactly one owner.", Some("Ownership"));

g.add_edge(rust, ownership, 0.95, vec![
    (topic,      PropertyValue::Label("memory_safety".into())),
    (confidence, PropertyValue::Float(0.98)),
]);

// Hybrid retrieval: BM25 seeds → property/strength-pruned traversal → fused ranking
let results = g.search_and_traverse(
    "memory safety",
    5,                                   // max BM25 seeds
    &["topic"],                          // required edge properties (bitmask filtered)
    0.6,                                 // min cumulative strength
    3,                                   // max depth
    20,                                  // max results
    FusionWeights { text: 0.6, graph: 0.4 },
);
```

### 3.2 Core graph API (selected)

**`KnowledgeGraph`** — `graph.rs`
- `add_node(text, label, embedding?) -> NodeId`, `add_node_with_embedding(...)`
- `add_edge(source, target, strength, props: Vec<(PropertyTag, PropertyValue)>) -> EdgeId`
- `read_blob(node) -> &str` (zero-copy), `read_embedding(node) -> Option<Vec<f32>>`, `node_label(node)`
- `remove_node(node) -> bool` (soft delete / tombstone), `is_node_deleted(node)`, `live_node_count()`
- Mutations (`graph_ops.rs`): `merge_nodes(keep, absorb) -> MergeReport`, `update_node_content(id, text, embedding?)`

**Properties** — `properties.rs`
- `PropertyValue` ∈ { `Float`, `Int`, `Label(String)`, … } ; `PropertyFilter` for ranges/equality
- `PropertyRegistry::register(name) -> PropertyTag` (dense tag; **max 64** distinct property types — the bitmask limit), `build_mask(names) -> u64`

**Traversal / query** — `query.rs`
- Fluent `QueryBuilder` via `graph.find()`: `.from_node(...)` / `.from_nodes(...)`, `.with_property(name)`, `.where_property(name, value)`, `.where_in_range(name, min, max)`, `.weight_by_property(name)`, `.min_strength(s)`, `.depth(d)`, `.limit(n)`, `.execute()` / `.execute_with_text()`
- Direct helpers: `neighbors_by_relation(node, prop, value)`, `predecessors_by_relation(...)`, `traverse_by_relation(node, prop, value, depth, max_results)`

**Vector search** — `vector_index.rs`
- `VectorIndex` trait: `add_vector`, `remove_vector`, `search(query, k) -> Vec<(NodeId, f64)>`, `save_to_dir`/`try_load_from_dir`
- Implementations: `BruteForceVectorIndex` (always), `HNSWVectorIndex` + `HNSWParams` (`ann-hnsw`)

**Persistence** — `serialize.rs`, `on_disk_index.rs`
- `save_to_dir(dir)` / `load_from_dir(dir)` (recommended); classic `save_to_disk` / `load_mmap` (`mmap`)
- On-disk text engine: `OnDiskTextIndex`, `OnDiskIndex`, `OnDiskSegment`, `SegmentInfo`, `TieredMergePolicy`, `MergeWorkerHandle`; `start_background_workers(interval)`
- `grap-cli --graph-dir <dir> start-workers --interval 1500` for long-running merge/vacuum

**Sharding** — `shard.rs`
- `ShardedGraph::new(shard_count)`, `add_node`/`add_edge` (auto ghost edges), `stats()`, `plan_rebalance(target)` → `execute_rebalance(plan)`, `save_to_dir`/`load_from_dir`

### 3.3 Building each graph *type* with the core

- **Knowledge graph:** add nodes (text + optional label + optional embedding), connect with typed, strength-weighted edges, retrieve with `search_and_traverse` (lexical) or `search_and_traverse_hybrid` (lexical + vector + graph).
- **Process graph:** model steps as nodes and data-flow/reference as edges; freeze the whole procedure as an immutable **version** via the lifecycle `Workspace` (next section) so it is reproducible.
- **Temporal / evidence graph:** the lifecycle `Workspace` records every state change as an immutable, content-addressed artifact with typed links — this *is* your trace graph.

### 3.4 Lifecycle `Workspace` (the temporal/evidence backbone, in Rust)

`grap::Workspace` (re-exported from `lifecycle`) gives you content-addressed versioning, activations, run records, and an append-only audit log — the primitives behind reproducible, auditable graphs. The same surface is exposed in Python (§4.3) and via the MCP audit domain (§5). Key concepts: `Version` (content-addressed, `Draft`→`Promoted`), `Activation` (freezes a logic version + governed weights + KG/ontology version), `RunRecord` (production or experiment), `AuditEvent` (typed `EventLink`s), `ReconstructionReport`, `RetentionReport`.

---

## 4. pygrap — the recommended way to build graphs

For most real-world ingestion, exploration, and pipelines, use the Python bindings. They expose the full engine (hybrid text + vector + graph search, property-weighted traversal, on-disk persistence, sharding) **and** the lifecycle `Workspace`.

**Install (from source):**
```bash
pip install maturin
maturin develop --manifest-path crates/pygrap/Cargo.toml --release
```

### 4.1 `pygrap.IndexedKnowledgeGraph` — single on-disk graph

Constructor is **path-based / on-disk** and supports a tiered-merge policy. It is a context manager.

| Method | Purpose |
|---|---|
| `IndexedKnowledgeGraph(path, merge_policy=None)` | Open/create an on-disk graph (optionally LSM tiering) |
| `with_merge_policy(path, segments_per_tier)` | Construct with background tiered merging |
| `add_node(text, label=None, embedding=None) -> int` | Add a node; returns its id |
| `add_edge(source, target, strength, properties=None)` | Typed, weighted edge (props as a dict) |
| `get_node_text(id)` / `get_node_label(id)` | Read content |
| `update_node(id, text, embedding=None)` | Replace content in place |
| `merge_nodes(keep, absorb) -> MergeReport` | Entity resolution / dedup |
| `neighbors_by_relation(node, property_name, value)` | Outgoing neighbors by edge property |
| `predecessors_by_relation(node, property_name, value)` | Incoming neighbors by edge property |
| `traverse_by_relation(node, property_name, value, depth, max_results)` | Multi-hop BFS along one relation |
| `search_and_traverse(query, max_seeds, required_properties, min_strength, max_depth, max_results, text_weight, graph_weight)` | Lexical + graph hybrid |
| `search_and_traverse_hybrid(query, vector=None, …, text_weight, vector_weight, graph_weight, traversal_weight_property=None)` | Lexical + **vector** + graph hybrid |
| `vector_search(embedding, top_k)` | Standalone ANN (HNSW/brute-force) |
| `start_background_workers(interval_ms)` | Background merge/vacuum |
| `save()` / `save_to_dir(path)` | Persist |
| `node_count()` / `node_id_space()` / `edge_count()` / `is_node_deleted(id)` | Introspection |

```python
import pygrap

with pygrap.IndexedKnowledgeGraph("my-graph", merge_policy=3) as g:
    a = g.add_node("Every value has exactly one owner.", label="Ownership")
    b = g.add_node("References must not outlive their referent.", label="Borrowing")
    g.add_edge(a, b, 0.9, properties={"topic": "memory_safety", "confidence": 0.97})

    hits = g.search_and_traverse_hybrid(
        "who owns a value", vector=None,
        max_seeds=8, required_properties=["topic"],
        min_strength=0.5, max_depth=3, max_results=20,
        text_weight=0.5, vector_weight=0.2, graph_weight=0.3,
    )
    for h in hits:
        print(h)
    g.save()
```

### 4.2 `pygrap.ShardedGraph` — horizontal scale

Node ids are `(shard, local_id)` tuples. Adds `plan_rebalance(target_shards)` / `execute_rebalance_to(target_shards)`, `start_background_maintenance(interval_ms)`, `cleanup_ghost_edges()`, `ghost_edge_count()`, and `stats()`. Same `search_and_traverse` / `search_and_traverse_hybrid` / `vector_search` surface.

### 4.3 `pygrap.Workspace` — versioning, activation, runs, audit (Temporal Graph)

This is the reproducibility/trace layer in Python. Open with `Workspace.open(root)`.

| Group | Methods |
|---|---|
| **Publish (versions)** | `publish_pg(content, level, rationale=None, metadata=None) -> Version`, `publish_ontology(content, metadata=None)`, `promote(version_hash, rationale)`, `list_versions()`, `detect_change(pg_logic_id, current)`, `get_record(hash)` |
| **Activate (freeze)** | `activate(activation_id, pg_logic_id, pg_version, weights=None, kg_version=None) -> Activation`, `set_production(activation_id)`, `current_production(pg_logic_id)` |
| **Runs** | `start_run(run_id, kind, …, scope)`, `complete_run(run_id, status, gaps_found, explanation_hashes=None, summary=None)`, `get_run(run_id)`, `list_runs(kind=None)`, `reconstruct(run_id) -> ReconstructionReport` |
| **Evidence artifacts** | `record_explanation(content, metadata=None)`, `record_validation(pg_hash, content, ok, metadata=None)` |
| **Audit (event graph)** | `record_event(event_type, subject, details=None, rationale=None)`, `read_audit()`, `query_events(event_type=None, since=None, until=None)`, `events_for_run(run_id)`, `events_linked_to(kind, id)` |
| **Retention** | `enforce_retention(production_cutoff_ms, experiment_cutoff_ms) -> RetentionReport` |

`reconstruct(run_id)` reports exactly which logic version, weights, and knowledge a past run used — and flags any missing artifact. That is the "evidence/trace" guarantee.

### 4.4 Drop-in templates & a real corpus

- [`templates/pygrap-arbitrary-data/`](../templates/pygrap-arbitrary-data/) — recursive ingestion, chunking, embeddings, hybrid search, Docker.
- [`templates/pygrap-arbitrary-data-extended/`](../templates/pygrap-arbitrary-data-extended/) — adds YAML frontmatter → edge properties + `--require` filtering.
- [`real-data-law-ro/`](../real-data-law-ro/) — ~2,000 Romanian legal acts ingested as nodes with embeddings, two-pass citation extraction, a directed `cites` graph, interactive hybrid explorer. The best end-to-end showcase of the Python surface.

---

## 5. grap-MCP — the governed, AI-assisted surface

`grap_mcp` is a Python MCP server that wraps `pygrap` and adds the things you need to operate graphs *as a team or with an agent*: **roles, an audit trail, content-addressed versioning, publish-time validation, a hybrid reasoner, and a Knowledge Extraction Engine** for AI-assisted ontology/relation building. Every **mutating** capability emits a Workspace audit event, so "what happened and why" is always answerable.

Start it: `python -m grap_mcp.server` (see [`mcp-server/`](../mcp-server/)). Point any MCP client at it.

### 5.1 Capability surface (8 domains)

The exact, live list is always available via `list_capabilities`. As of this writing:

**Knowledge**
| Tool | Does |
|---|---|
| `create_knowledge_graph(path)` / `load_knowledge_graph(path)` | Create / open an on-disk KG (becomes current) |
| `ingest_documents(path)` | Chunk + add `.md`/`.txt` docs as real evidence nodes (optionally embedded) |
| `extract_ontology(path, out_path=None, sim=0.86, model=None, max_files=None)` | **AI** — KEE proposes an ontology from documents (LLM + embedding reconcile), versioned for human review |
| `load_ontology(path)` | Parse an ontology YAML, project it into the KG, version it |
| `project_relations(relations)` / `build_legal_graph(path, …)` / `legal_graph_from_relations(path, top)` / `graph_neighbors(node_id, types=None)` | Project extracted relations as typed edges; navigate the KG |

**Procedure-graph lifecycle**
| Tool | Does |
|---|---|
| `load_procedure_graph(path)` | Load + project a PG YAML into the graph |
| `publish_pg(level="draft", rationale=None)` | Content-address it as an immutable version (runs validation, non-blocking) |
| `promote_pg(version_hash, rationale)` | Gate: **refused if validation has errors** |
| `activate(activation_id, version_hash, weights=None, set_as_production=False, kg_version=None)` | Freeze (version + governed weights + KG version) |
| `set_production(activation_id)` / `get_production()` / `list_versions()` | Manage the production pointer |

**Execution & reasoning**
| Tool | Does |
|---|---|
| `run_procedure(scope="all")` | Execute the active PG against the KG (tracked run) |
| `detect_gaps(scope="all")` | Hybrid gap detection (structural + lexical signals), persists run + explanations |
| `get_explanation(target)` | Sectioned, reproducible explanation for a result target |

**Experimentation & quarantine**
| Tool | Does |
|---|---|
| `run_experiment(scope="all", hypothesis=None)` | Same run with full audit parity, **quarantined** from production metrics |
| `get_quality_metrics()` | Production-only quality view (experiments excluded by construction) |
| `adopt_experiment(run_id, activation_id, rationale)` | Promote an experiment's version through the gated path |

**Validation** — `validate_procedure_graph(path)`, `diff_procedure_graphs(path_a, path_b)`, `get_validation(version_hash)`
**Governance** — `get_config()`, `set_weight(name, value, rationale)`, `set_retention(key, days, rationale)`, `enforce_retention()`
**Auth** — `get_role()`, `set_role(role)` (`viewer < operator < admin`; reads open, lifecycle/exec needs operator, governance/retention needs admin)
**Audit & introspection (Temporal Graph)** — `get_audit_log()`, `get_status()`, `list_runs(kind=None)`, `get_run(run_id)`, `reconstruct_run(run_id)`, `query_events(event_type=None, run_id=None)`, `list_capabilities()`

### 5.2 Knowledge Extraction Engine (KEE) — local, AI-assisted graph building

`grap_mcp/kee/` turns raw documents into structured graph material with **pluggable, local-first** AI backends. Pipeline ([`kee/pipeline.py`](../mcp-server/grap_mcp/kee/pipeline.py)):

```
read_chunks(docs)  →  extract_fragments(LLM)  →  reconcile(embeddings, sim)  →  candidate ontology (YAML, versioned)
```

- **LLM providers** ([`kee/llm.py`](../mcp-server/grap_mcp/kee/llm.py)): `OpenAICompatProvider` (any OpenAI-compatible `/chat/completions` — defaults to **Ollama** at `http://localhost:11434/v1`, model `llama3.1`), and a deterministic `StubProvider` for offline tests/demos. `get_provider(model=None)` selects one.
- **Embedders** ([`kee/embeddings.py`](../mcp-server/grap_mcp/kee/embeddings.py)): `LocalEmbedder` (sentence-transformers), `OpenAICompatEmbedder` (Ollama/OpenAI `/embeddings`, default `nomic-embed-text`), `HashingEmbedder` (deterministic, dependency-free fallback). `get_embedder()` chooses via `GRAP_EMBED_BACKEND`.
- **Reconciliation** ([`kee/reconcile.py`](../mcp-server/grap_mcp/kee/reconcile.py)): greedy single-pass clustering by cosine similarity collapses near-duplicate concepts; `stress_merge_nodes` materializes strings as pygrap nodes and union-finds near-duplicates via `merge_nodes`.
- **ConceptRegistry** ([`kee/registry.py`](../mcp-server/grap_mcp/kee/registry.py)): persistent **cross-run entity dedup** — `resolve_many(names)` maps raw names to canonical concepts so repeated ingests don't fan out duplicates.
- **Validation** ([`kee/validate.py`](../mcp-server/grap_mcp/kee/validate.py)): `validate_ontology(onto)` returns `{ok, errors, warnings}` before anything is adopted.

The output is a **proposed, versioned candidate** — it never becomes active until a human loads it. That keeps AI assistance in the loop without surrendering governance.

### 5.3 Procedure-graph format & the reasoner

- **PG format** ([`grap_mcp/procedure_graph.py`](../mcp-server/grap_mcp/procedure_graph.py), [`docs/procedure-graph-format.md`](procedure-graph-format.md)): a human-edited flat YAML — steps with `operation`, `inputs`, `outputs`, `references`, `guards`, `notes`. `project_to_grap` turns steps into nodes, `outputs.*` into data-flow edges, and `kg:` references into edges to resolved KG nodes.
- **Reasoner** ([`grap_mcp/reasoner.py`](../mcp-server/grap_mcp/reasoner.py)): **hybrid** — combines a structural signal with a mapping-hint-weighted BM25 search over *real* KG content, deterministically excluding system-projected scaffolding (ontology + PG-step nodes) by node id so they're never mistaken for evidence. Results carry sectioned, reproducible explanations (structured + statistical + rendered snapshot).

### 5.4 Configuration & environment

| Env var | Meaning |
|---|---|
| `GRAP_WORKSPACE` | Lifecycle workspace root (defaults to a `.grap-*` dir) |
| `GRAP_ROLE` | Starting role (`viewer`/`operator`/`admin`; default `operator`) |
| `GRAP_EMBED_BACKEND` | `ollama` / `http` / `openai` / `local` — chooses the embedder |
| LLM base URL / model | OpenAI-compatible endpoint (default Ollama `…:11434/v1`, `llama3.1`) |

Governed config (`get_config`) holds prioritization **weights** and **retention** windows; both are changed only with a mandatory, audited rationale, and weights are **frozen into an activation** for reproducibility.

---

## 6. End-to-end recipes

**A. Build a local KG from your documents (no server).**
```python
import pygrap
with pygrap.IndexedKnowledgeGraph("kb", merge_policy=4) as g:
    # chunk your .md/.txt, embed (optional), add_node + add_edge per relation
    ...
    g.save()
```
Or use [`templates/pygrap-arbitrary-data/`](../templates/pygrap-arbitrary-data/) as a starting point.

**B. AI-assisted ontology/relations (fully local).** Run Ollama (`llama3.1` + `nomic-embed-text`), then via MCP: `create_knowledge_graph` → `ingest_documents` → `extract_ontology` (proposes a versioned candidate) → review → `load_ontology`.

**C. Process graph with reproducibility.** `load_procedure_graph` → `validate_procedure_graph` → `publish_pg` → `promote_pg` (gated) → `activate` (freezes weights + KG version) → `run_procedure` / `detect_gaps`.

**D. Audit & reproduce a past run (Temporal Graph).** `list_runs` → `get_run(run_id)` → `reconstruct_run(run_id)` (reports the exact logic/weights/knowledge and any missing artifact) → `query_events(run_id=…)` for the full timeline.

---

## 7. Capability ↔ API cross-reference

| MCP capability | pygrap / engine call(s) |
|---|---|
| `ingest_documents`, `create/load_knowledge_graph` | `IndexedKnowledgeGraph(...)`, `add_node`, `add_edge`, `save` |
| `detect_gaps` / `run_procedure` | `search_and_traverse*`, `neighbors_by_relation`, traversal helpers |
| `publish_pg` / `promote_pg` / `activate` | `Workspace.publish_pg` / `promote` / `activate` |
| `run_procedure` / `run_experiment` | `Workspace.start_run` + `complete_run` (+ `record_explanation`) |
| `reconstruct_run` | `Workspace.reconstruct` |
| `query_events` / `get_audit_log` | `Workspace.query_events` / `read_audit` / `events_for_run` |
| `enforce_retention` | `Workspace.enforce_retention` |
| `extract_ontology` | `kee.pipeline.extract_ontology` (LLM + embedder + reconcile) |

---

## 8. Where to go next

- **Engine quick-start & feature flags:** [`README.md`](../README.md)
- **The KG/PG/TG thesis (and ASPICE reference app):** [`docs/architecture-grap-mcp.md`](architecture-grap-mcp.md)
- **Live MCP tool reference:** [`mcp-server/CAPABILITIES.md`](../mcp-server/CAPABILITIES.md) · runnable walkthrough: [`mcp-server/demo_aspice_e2e.py`](../mcp-server/demo_aspice_e2e.py)
- **Engine design spec:** [`core_graph_design.md`](../core_graph_design.md) · on-disk index: [`docs/design-0.2-on-disk-index-and-deletes.md`](design-0.2-on-disk-index-and-deletes.md) · ANN: [`docs/design-ann-index.md`](design-ann-index.md)
- **KEE deep dive:** [`docs/knowledge-extraction-engine.md`](knowledge-extraction-engine.md) · **PG format:** [`docs/procedure-graph-format.md`](procedure-graph-format.md)
- **Install:** [`INSTALL.md`](../INSTALL.md) · **pygrap:** [`crates/pygrap/README.md`](../crates/pygrap/README.md)

---

*grap — because sometimes you need the graph, the text, and the ability to prune both at the same time.*
