# grap core — efficiency assessment for offline-RAG (KG/PG/TG)

**Goal frame:** fast, low-RAM, offline RAG over Knowledge / Procedure / Temporal graphs, exposed efficiently through the engine API, `pygrap`, and the MCP. The bulk of the data (node text) should *stay a file on disk and be read only when needed*; topology should be fast to load, traverse, trim, extend, and restructure.

Grounded in the actual core (`src/blob_store.rs`, `src/serialize.rs`, `src/index.rs`, `src/graph.rs`, `crates/pygrap/`).

---

## What's already good

- **Hot topology / cold blobs split** is the right shape: adjacency (`Node.outgoing/incoming`, `outgoing_property_mask`) lives in arena `Vec`s; node text lives in a separate `BlobStore` keyed by lightweight `BlobRef` (16 bytes).
- **O(1) masked pruning** (`Edge::matches_mask`, `outgoing_property_mask`) makes "follow only `cites` edges" a bit-test — traversal, PageRank, blast-radius, and cycle detection all reuse it.
- **On-disk text index** — the default open path (`load_from_dir`) loads `OnDiskTextIndex` segments rather than rebuilding an in-memory inverted index. Good: BM25 postings stay on disk.
- **Persisted vector index with delta sync** — `load_from_dir` fast-loads a saved ANN and only `sync_new_vectors()` for new nodes; full rebuild is the fallback.
- **Crash-safe, versioned, checksummed persistence** — staged `.grap-new/` + rename, CRC32 per chunk, `GraphMeta` versioning.

## The headline gap: node text is loaded **eagerly into RAM** on every open

The default open path every `pygrap`/ro-law/MCP user hits:

```
pygrap IndexedKnowledgeGraph(path) / with_merge_policy(path)
  -> IndexedKnowledgeGraph::load_from_dir(dir)
       -> KnowledgeGraph::load_from_disk(dir/graph)
            -> fs::read("graph.blobs")  ->  Vec<u8>  ->  Storage::Chunks   ❌ whole corpus in RAM
            -> re-hash every node's bytes to recompute content_hash        ❌ O(total bytes) at load
```

So **load time and resident memory scale with the total size of all node text**, not with the topology. For a 2,000-act legal corpus or any sizeable RAG store this is the dominant cost and directly contradicts "what can be a file loaded only when details are needed stays a file on disk."

Meanwhile the machinery to avoid this **already exists but is inert**:
- `Storage::Mmap(memmap2::Mmap)` + `BlobStore::from_mmap()` + a zero-copy `read()` arm that treats `BlobRef.offset` as a global offset into the flat map — implemented, behind the `mmap` feature.
- `graph.blobs` is already a **flat concatenation** of chunks, and `SerializableNode.blob_global_offset` already stores the **global** byte offset of each node's text.
- But `KnowledgeGraph::load_mmap()` just calls `load_from_disk()` (its own comment: *"for now we use the proven portable loader... ready for when we switch to true pointer-based reads"*), and `crates/pygrap/Cargo.toml` leaves the `mmap` feature commented out.

**Everything needed for true memory-mapped, load-on-demand blobs is present; one loader and one feature flag are missing.** That is the P0 fix below.

---

## Prioritized proposals

### P0 — True mmap-backed, read-mostly open (IMPLEMENT NOW)
Make `KnowledgeGraph::load_mmap` actually map `graph.blobs` into a `Storage::Mmap` (global offsets), enable the `mmap` feature in `pygrap`, and expose a read-mostly `open_mmap(path)`. Result: open cost becomes O(topology); node text stays on disk and is read zero-copy on demand; RAM drops from "whole corpus" to "topology + indexes". **No on-disk format change** (the flat file + global offsets are already written). Detailed plan below.

### P1 — proposed (not in this change)
1. **Blob compaction / `vacuum_blobs()`** — `update_node`/`merge_nodes` append new blobs and orphan the old ones (append-only `BlobStore`, no reclamation). For long-lived, frequently-restructured graphs this bloats `graph.blobs`. Add a compaction that rewrites only live blobs and remaps offsets (pairs with the existing text-index `vacuum`). Serves the "trim" goal.
2. **MCP exposure of large traversals** — `graph_neighbors`, the analytics tools, and `get_context_bundle` return whole result sets. Add `limit` + opaque `cursor` pagination and route them through the existing `#MUNCH` compact encoder so big KG/PG/TG answers don't blow the agent's context. (The codec exists; only `graph_neighbors` is wired today.)
3. **Guarantee the vector-index fast path** — ensure the ANN is built-and-saved by `save_to_dir` so opens never silently fall into the O(n) `rebuild_vector_index`; otherwise large-corpus opens pay an ANN rebuild.
4. **Unify the legacy loader** — `IndexedKnowledgeGraph::load_from_disk` (distinct from `load_from_dir`) still rebuilds an in-memory inverted index by scanning all blobs; fold it into the on-disk-segment path or mark it test-only.

### P2 — proposed (scale headroom)
- ~~**64-bit blob offsets / chunked mmap**~~ **DONE** — `BlobRef.offset` widened to `u64`; the flat mmap path now supports blob files well beyond 4 GiB with no on-disk format change.
- ~~**CSR adjacency**~~ **DROPPED (benchmarked, not justified).** The idea was that a compressed-sparse-row neighbor layout would improve cache locality during traversal vs per-node `Vec<EdgeId>`. The `large_n_traversal` probe in `benches/graph_benches.rs` runs the *same* bounded BFS (depth 12, 5 000-node budget, pure `traverse_by_relation`, no BM25) against arenas of 10K / 50K / 200K nodes: **27.7 µs → 32.0 µs → 31.7 µs**. There is a one-time ~15 % step crossing out of L2 into L3, then it is **flat** 50K→200K despite a 4× larger arena — per-traversal cost does not scale with graph size. CSR could at best recover that ~15 % step in the cache-resident regime and nothing at large N, which does not justify a hot-path rewrite plus on-disk-format and mutation cost (CSR is rebuild-on-edit; the arena is cheap to extend/trim, which the mission wants). The `large_n_fusion` probe then showed where the time actually goes: at 100K nodes, pure BM25 `search` = **~104 ms** vs full `search_and_traverse` = **~105 ms** — traversal + RRF fusion adds **<1 % (noise)**. *(Caveat: the synthetic corpus is homogeneous so every node matches every query term — 104 ms is a worst-case upper bound on posting-list length, not a typical selective query. The attribution — text ≫ graph — is robust regardless.)* **Conclusion: the graph engine is not the bottleneck; BM25 scoring is. Future perf work belongs in the text/fusion path, not adjacency.**

---

## P0 implementation plan — true mmap read-mostly open

**Architecture:** add a real `KnowledgeGraph::load_mmap` that maps `graph.blobs` and reconstructs `BlobRef`s with global offsets (`chunk_idx=0`, `offset=blob_global_offset`), exactly what the `Storage::Mmap` arm of `read()` expects. `IndexedKnowledgeGraph::load_mmap` already loads the on-disk text index + vector index and calls `KnowledgeGraph::load_mmap(dir/graph)`, so fixing the one delegating function makes the whole open lazy. Enable `mmap` in `pygrap` and expose `open_mmap`. **Read-mostly:** a write after an mmap open transparently falls back to an in-RAM copy (existing `BlobStore::store` behavior) — so ingestion keeps using the writable `new`/`with_merge_policy`, and query/RAG uses `open_mmap`. **No format change.**

### Task 1 — real `KnowledgeGraph::load_mmap` (core)
**Files:** `src/serialize.rs` (replace the delegating `load_mmap` at the `#[cfg(feature="mmap")]` free fn ~L803). Test: `tests/mmap_roundtrip.rs` (new, `#![cfg(feature="mmap")]`).

- [ ] **Failing test** `tests/mmap_roundtrip.rs`:
```rust
#![cfg(feature = "mmap")]
use grap::KnowledgeGraph;
use tempfile::tempdir;

#[test]
fn mmap_open_reads_blobs_from_disk_without_copying() {
    let dir = tempdir().unwrap();
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("brake-by-wire redundancy requirement", Some("REQ-1"), None);
    let h = g.node_content_hash(n).unwrap();
    g.save_to_disk(dir.path()).unwrap();

    let g2 = KnowledgeGraph::load_mmap(dir.path()).unwrap();
    assert_eq!(g2.read_blob(n), "brake-by-wire redundancy requirement"); // zero-copy from the map
    assert_eq!(g2.node_content_hash(n), Some(h));                        // recomputed via mmap read
    assert!(g2.blobs_are_mapped());                                      // new pub accessor (see below)
}
```
- [ ] **Run -> FAIL** (`load_mmap` delegates; `blobs_are_mapped` missing).
- [ ] **Implement.** Replace the free `load_mmap(dir) -> KnowledgeGraph` body. Read `graph.meta` (version check), `mmap` the `graph.blobs` file, deserialize `graph.topo`, rebuild `PropertyRegistry`, build `BlobStore::from_mmap(mmap)`, then for each `SerializableNode` build a `BlobRef { chunk_idx: 0, offset: u32::try_from(sn.blob_global_offset)?, length: sn.blob_length, uncompressed_len: sn.blob_uncompressed_len }` (same for the embedding ref). Compute `content_hash` via `blobs.read(&blob).as_bytes()` (NOT chunk slicing — there are no chunks here). Rebuild edges + `graph.deleted` exactly as `load_from_disk` does. Construct via `from_parts_with_deleted`. Concretely:
```rust
#[cfg(feature = "mmap")]
pub fn load_mmap(dir: &Path) -> Result<KnowledgeGraph, GraphError> {
    KnowledgeGraph::cleanup_stale_temp_saves(dir);
    let meta_str = fs::read_to_string(dir.join("graph.meta"))?;
    let meta: GraphMeta = serde_json::from_str(&meta_str)
        .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;
    if meta.version != CURRENT_GRAPH_FORMAT_VERSION {
        return Err(GraphError::UnsupportedVersion { found: meta.version, supported: CURRENT_GRAPH_FORMAT_VERSION });
    }
    // Map the flat blob file — bytes stay on disk, paged in on demand.
    let blob_file = File::open(dir.join("graph.blobs"))?;
    // SAFETY: the file is opened read-only; the Mmap is owned by the BlobStore for the graph's lifetime.
    let mmap = unsafe { memmap2::Mmap::map(&blob_file)? };
    if mmap.len() as u64 != meta.blob_total_bytes {
        return Err(io::Error::new(io::ErrorKind::InvalidData,
            format!("graph.blobs size {} != recorded {}", mmap.len(), meta.blob_total_bytes)).into());
    }
    let blobs = crate::blob_store::BlobStore::from_mmap(mmap);

    let (snodes, sedges, property_names): (Vec<SerializableNode>, Vec<SerializableEdge>, Vec<String>) =
        bincode::deserialize(&fs::read(dir.join("graph.topo"))?)
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))?;
    let mut properties = crate::properties::PropertyRegistry::new();
    for name in property_names { properties.register(&name); }

    let to_off = |g: u64| -> Result<u32, GraphError> {
        u32::try_from(g).map_err(|_| GraphError::Other(
            "blob offset exceeds 4 GiB; mmap mode supports blob files up to 4 GiB — use load_from_disk".into()))
    };
    let mut nodes = Vec::with_capacity(snodes.len());
    for sn in snodes {
        let blob = BlobRef { chunk_idx: 0, offset: to_off(sn.blob_global_offset)?,
                             length: sn.blob_length, uncompressed_len: sn.blob_uncompressed_len };
        let embedding = match (sn.embedding_global_offset, sn.embedding_length, sn.embedding_uncompressed_len) {
            (Some(o), Some(l), Some(u)) => Some(BlobRef { chunk_idx: 0, offset: to_off(o)?, length: l, uncompressed_len: u }),
            _ => None,
        };
        let content_hash = crate::lifecycle::hash::sha256_digest(blobs.read(&blob).as_bytes());
        nodes.push(crate::graph::Node {
            blob, embedding,
            outgoing: sn.outgoing.into_iter().map(EdgeId).collect(),
            incoming: sn.incoming.into_iter().map(EdgeId).collect(),
            label: sn.label, outgoing_property_mask: sn.outgoing_property_mask, content_hash,
        });
    }
    // edges + deleted: identical to load_from_disk (copy that logic).
    // ...
    Ok(KnowledgeGraph::from_parts_with_deleted(nodes, edges, blobs, properties, deleted))
}
```
Add a small public accessor on `KnowledgeGraph`: `pub fn blobs_are_mapped(&self) -> bool { #[cfg(feature="mmap")] { self.blobs.is_mapped() } #[cfg(not(feature="mmap"))] { false } }`.
- [ ] **Run -> PASS** (`cargo test -p grap --features mmap --test mmap_roundtrip`); `cargo test -p grap` (no feature) still green; `cargo test -p grap --features mmap` green.
- [ ] **Commit:** `feat(core): true memory-mapped load_mmap (blobs stay on disk, zero-copy reads)`.

### Task 2 — `IndexedKnowledgeGraph::load_mmap` end-to-end (core)
**Files:** `src/index.rs` (the `#[cfg(feature="mmap")]` `load_mmap` already exists and is correct once Task 1 is real — add a delegating `blobs_are_mapped()` if useful). Test: extend `tests/mmap_roundtrip.rs` to save via `IndexedKnowledgeGraph::save_to_dir`, open via `IndexedKnowledgeGraph::load_mmap`, run a `search_and_traverse`, and assert results + `read_blob` correctness + mapped-ness.
- [ ] Verify the `dir/graph` + `dir/text_index` layout pairing (save_to_dir writes both; load_mmap reads both). Commit if any glue is needed; otherwise the test alone covers it.

### Task 3 — pygrap surface (mmap feature + `open_mmap`)
**Files:** `crates/pygrap/Cargo.toml` (add `mmap` to the grap feature list), `crates/pygrap/src/indexed_graph.rs` (new `open_mmap` staticmethod), `crates/pygrap/smoke_test.py`.
- [ ] `Cargo.toml`: `grap = { path = "../..", features = ["fulltext", "sharding", "mmap"] }`.
- [ ] Add:
```rust
/// Open an existing on-disk graph **read-mostly**, memory-mapping the node-text
/// blobs so they stay on disk and are read zero-copy on demand (fast, low-RAM).
/// A subsequent mutation transparently copies blobs into RAM; for write/ingest
/// workloads use the normal constructor instead.
#[staticmethod]
fn open_mmap(path: &str) -> PyResult<Self> {
    let dir = PathBuf::from(path);
    let inner = InnerGraph::load_mmap(&dir).map_err(to_py_err)?;
    Ok(Self { inner, path: Some(dir) })
}
```
- [ ] Smoke test: build + `save_to_dir`, then `IndexedKnowledgeGraph.open_mmap(path)`, assert `search_and_traverse_hybrid`/`get_node_text` return correct text and (optionally) a `is_mapped()`-style check.
- [ ] `cargo build -p pygrap`; `maturin build/develop`; `python crates/pygrap/smoke_test.py`.
- [ ] **Commit:** `feat(pygrap): open_mmap — read-mostly memory-mapped open`.

### Caveats (documented, not blocking)
- ~~**4 GiB cap** in mmap mode (u32 `BlobRef.offset`)~~ **Lifted** — `BlobRef.offset` is now `u64`; mmap mode supports multi-GB blob files with no format change.
- **Read-mostly:** the first write after an mmap open copies blobs to RAM (existing `BlobStore::store` fallback) — intended; ingest uses the writable path.
- **File locking:** an mmap holds the `graph.blobs` handle for the graph's lifetime; on Windows the directory can't be deleted/overwritten while open.

---

## Bottom line
The core's architecture is sound and already separates hot topology from cold text — but the **cold text is not actually cold**: every open pulls the whole corpus into RAM. Wiring the already-present `Storage::Mmap` path (P0) is the single highest-leverage change for fast, low-RAM, offline-RAG opens, and it lands with no on-disk format change. The remaining proposals (blob vacuum, MCP pagination + compact encoding, guaranteed vector fast-path) are follow-ups that further the "trim / expose efficiently" goals.
