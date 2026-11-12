# Grap: A Knowledge Graph Engine for Interconnected Text

**One-sentence pitch:**  
Grap is a Rust-native engine purpose-built for storing, traversing, and retrieving richly connected text documents at scale, by tightly integrating graph structure, full-text search, and vector embeddings in one coherent, production-ready system.

---

## The Problem

Most organizations working with large collections of interconnected documents face a fundamental limitation:

- Pure **vector databases** are excellent at semantic similarity but blind to explicit relationships, provenance, and structure.
- Traditional **graph databases** handle relationships well but struggle with large volumes of rich text and fast, relevance-ranked retrieval.
- Conventional **search engines** ignore the graph of references and dependencies between documents.

As a result, teams building advanced retrieval systems (especially in regulated industries, research, legal, or complex enterprise knowledge bases) are forced to stitch together multiple disparate systems — and still fall short on both performance and correctness.

---

## The Solution: Grap

Grap was designed from the ground up to solve exactly this class of problem.

It treats documents as **first-class nodes** in a property-weighted graph, while maintaining high-performance inverted indexes and vector embeddings. The engine can simultaneously reason over:

- **Structural signals** — explicit relationships with strength and rich properties
- **Textual signals** — BM25 and phrase search over full document content
- **Semantic signals** — dense vector embeddings

These three signals are fused at query time with tunable weights, and the system uses aggressive early pruning (via 64-bit property bitmasks on every edge) to make traversal extremely efficient even on large, densely connected graphs.

### Key Differentiators

- **Property bitmask pruning** — Edges carry compact property masks that allow the engine to reject entire subtrees in constant time during traversal.
- **Hot topology, cold content** — Graph structure lives in memory for fast traversal; large text and embeddings are stored efficiently in a chunked blob store (with optional memory-mapping).
- **Production-grade persistence** — Segmented on-disk inverted indexes with write-ahead logging, tiered background merging, incremental vacuum/compaction, and reliable atomic saves (including on Windows).
- **Hybrid retrieval as a first-class feature** — Not bolted on. Text, vector, and graph signals are designed to work together from the beginning.
- **Horizontal scaling via sharding** — Transparent cross-shard traversal using "ghost edges," with support for rebalancing.
- **Real relationship modeling** — Strong support for typed, weighted, property-carrying edges — ideal for citation networks, amendment chains, dependency graphs, etc.

---

## Target Use Cases

Grap shines in domains where documents are not independent artifacts, but part of a larger web of meaning:

- Legal and regulatory corpora (citation networks, amendments, repeals)
- Scientific and academic literature
- Technical documentation and standards with complex cross-references
- Enterprise knowledge management with rich metadata and relationships
- Advanced RAG systems that need both semantic recall *and* structural reasoning

---

## Python Access: pygrap (Current Reality)

A Python binding layer called **pygrap** exists and is actively used for real workloads.

**Current state (as of 2026):**

- It provides surprisingly rich access to the engine, including:
  - `IndexedKnowledgeGraph` and `ShardedGraph`
  - Hybrid search (`search_and_traverse_hybrid`) with vector and graph fusion
  - Structure-aware traversal (`traverse_by_relation`, `predecessors_by_relation`, etc.)
  - Native support for storing and searching with embeddings
  - Context managers, persistence, background maintenance, and rebalancing

- **Important reality check**: `pygrap` is **not** a published package on PyPI. It must be built and installed from source using `maturin develop` (see `crates/pygrap/README.md`).

- In practice, the Python layer is currently the most productive way to build complex ingestion pipelines, experiment with hybrid retrieval strategies, and develop real applications on top of grap.

- The Rust core (`grap` crate) remains the performance and durability foundation. The Python bindings are a high-fidelity but still-internal interface layer.

In short: **If you want to use grap seriously today for anything beyond simple experiments, you will be working with the Rust engine through the Python bindings, built from the source tree.**

---

## Summary

Grap is not another graph database, vector store, or search engine. It is a specialized engine for the specific (and increasingly common) workload of **highly interconnected text corpora**, where both structure and content matter.

The combination of aggressive structural pruning, mature on-disk indexing, true hybrid retrieval, and usable Python access makes it one of the most interesting specialized retrieval engines currently available for demanding knowledge-intensive applications.

---

*This document reflects the state of the project in mid-2026.*