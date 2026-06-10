# Blob-store compaction (`vacuum_blobs`) (P1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reclaim dead bytes in the append-only `BlobStore`. `update_node_content` re-stores text and orphans the old blob; tombstoned/merged nodes leave their blobs unreferenced. Over a long-lived, frequently-restructured graph `graph.blobs` only grows. `vacuum_blobs()` rewrites the store to contain **only live blobs** and remaps each node's `BlobRef` — the "trim" half of the efficiency goal.

**Architecture:** A pure blob-bytes compaction on `KnowledgeGraph`: build a fresh `BlobStore` from the current (live) blob/embedding bytes of each non-tombstoned node, collect the new refs, then reassign them. **Node ids, edges, the text index, and the vector index all reference `NodeId`s and are untouched** — so no index rebuild is needed and the operation is O(live bytes). Tombstoned node slots are kept (dropping them would shift `NodeId`s) but their blob bytes are dropped (pointed at an empty entry). Surfaced on `IndexedKnowledgeGraph`, pygrap, and a `compact_graph` MCP tool that also runs the existing deleted-node text-index vacuum.

**Tech Stack:** Rust core, PyO3, Python (`grap_mcp`), `cargo test` + `pytest`. No new deps, no on-disk format change.

**Scope discipline:** Compacts blob bytes only — does NOT renumber nodes/edges or touch indexes. `content_hash` of live nodes is unchanged (text identical); tombstoned nodes keep their (now-stale, never-verified) hash. Works whether the store is in-RAM `Chunks` or `mmap`-backed (reads from the old store, writes a fresh `Chunks` store).

---

## File Structure
- **Modify** `src/blob_store.rs` — add `total_bytes()`.
- **Modify** `src/graph_ops.rs` — add `KnowledgeGraph::vacuum_blobs()` + `BlobVacuumReport`.
- **Modify** `src/index.rs` — `IndexedKnowledgeGraph::vacuum_blobs()` delegate.
- **Modify** `crates/pygrap/src/indexed_graph.rs` — `vacuum_blobs()` PyO3 (returns a dict).
- **Modify** `mcp-server/grap_mcp/server.py` — `compact_graph` tool + capabilities.
- **Tests** `src/graph_ops.rs` units, `crates/pygrap/smoke_test.py`, `mcp-server/tests/test_compact.py`.

Reuse: `BlobStore::{new, store, store_embedding, read, read_embedding}`, `self.nodes`, `self.deleted`, `self.blobs`, `BlobRef`.

---

## Task 1: `BlobStore::total_bytes` + `KnowledgeGraph::vacuum_blobs`

**Files:** `src/blob_store.rs`, `src/graph_ops.rs`. Tests: `src/graph_ops.rs` `#[cfg(test)] mod tests`.

- [ ] **Step 1: Failing test** — add to `src/graph_ops.rs` tests:
```rust
#[test]
fn vacuum_blobs_reclaims_orphaned_and_tombstoned_bytes() {
    let mut g = KnowledgeGraph::new();
    let a = g.add_node("a short node", Some("A"), None);
    let b = g.add_node("b node to be deleted with a fairly long body", Some("B"), None);
    // orphan a's old blob by replacing it with a much larger one, then a small one
    g.update_node_content(a, &"X".repeat(5000), None).unwrap();
    g.update_node_content(a, "tiny", None).unwrap();
    // tombstone b -> its blob becomes dead
    assert!(g.remove_node(b));

    let before = g.blob_bytes();
    let report = g.vacuum_blobs();
    let after = g.blob_bytes();

    assert_eq!(report.bytes_before, before);
    assert_eq!(report.bytes_after, after);
    assert!(report.bytes_reclaimed > 5000);          // the orphaned 5000-byte blob is gone
    assert!(after < before);
    // live content + integrity preserved
    assert_eq!(g.read_blob(a), "tiny");
    assert_eq!(g.verify_node(a), crate::graph::NodeIntegrity::Match);
    assert!(g.is_node_deleted(b));                    // slot kept (NodeIds stable)
}

#[test]
fn vacuum_blobs_preserves_edges_and_embeddings() {
    let mut g = KnowledgeGraph::new();
    let a = g.add_node("alpha", Some("A"), Some(&[1.0_f32, 2.0, 3.0]));
    let b = g.add_node("beta", Some("B"), None);
    g.add_edge(a, b, 0.9, vec![]);
    g.update_node_content(a, "alpha-v2", Some(&[4.0_f32, 5.0, 6.0])); // orphans old text+emb
    g.vacuum_blobs();
    assert_eq!(g.read_blob(a), "alpha-v2");
    assert_eq!(g.read_embedding(a), Some(vec![4.0_f32, 5.0, 6.0]));
    assert_eq!(g.edge_count(), 1);
    assert_eq!(g.node_label(a), Some("A"));
}
```
(Confirm the real accessor names: `blob_bytes()` is the new public method on `KnowledgeGraph` exposing `self.blobs.total_bytes()`; `update_node_content` is `pub(crate)` — callable from the test module since it's in-crate; `node_label`/`read_blob`/`read_embedding`/`verify_node` exist.)

- [ ] **Step 2: Run, verify FAIL** — `cargo test -p grap --lib graph_ops::tests::vacuum_blobs_reclaims_orphaned_and_tombstoned_bytes` → undefined.

- [ ] **Step 3: Implement.**
   `src/blob_store.rs` — add:
```rust
/// Total bytes currently held by the store (sum of chunk lengths, or the
/// mapped-file length). Includes dead/orphaned bytes — `vacuum_blobs` reclaims them.
pub fn total_bytes(&self) -> usize {
    match &self.storage {
        Storage::Chunks(c) => c.iter().map(|chunk| chunk.len()).sum(),
        #[cfg(feature = "mmap")]
        Storage::Mmap(m) => m.len(),
    }
}
```
   `src/graph_ops.rs` — add the report type (module level) and the method on `impl KnowledgeGraph`:
```rust
/// Outcome of a `vacuum_blobs` compaction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BlobVacuumReport {
    /// Blob-store bytes before compaction (incl. dead/orphaned bytes).
    pub bytes_before: usize,
    /// Blob-store bytes after compaction (live blobs only).
    pub bytes_after: usize,
    /// Bytes reclaimed (`bytes_before - bytes_after`).
    pub bytes_reclaimed: usize,
}

impl KnowledgeGraph {
    /// Bytes currently held by the blob store (live + dead). See `vacuum_blobs`.
    pub fn blob_bytes(&self) -> usize {
        self.blobs.total_bytes()
    }

    /// Compact the blob store: rebuild it from only the live nodes' current
    /// blob + embedding bytes and remap each `BlobRef`. Reclaims bytes orphaned
    /// by `update_node_content`, `merge_nodes`, and tombstoned nodes. Node ids,
    /// edges, and all indexes are untouched (they key on `NodeId`).
    pub fn vacuum_blobs(&mut self) -> BlobVacuumReport {
        let bytes_before = self.blobs.total_bytes();
        let n = self.nodes.len();
        let mut new_blobs = crate::blob_store::BlobStore::new();
        // Pass 1: copy live bytes into the fresh store, recording new refs.
        // (Read the old store + nodes immutably; write only `new_blobs`.)
        let mut new_refs: Vec<(crate::blob_store::BlobRef, Option<crate::blob_store::BlobRef>)> =
            Vec::with_capacity(n);
        for i in 0..n {
            let deleted = self.deleted.get(i).copied().unwrap_or(false);
            if deleted {
                // Drop the dead node's bytes; keep the slot with an empty blob.
                new_refs.push((new_blobs.store(""), None));
                continue;
            }
            let text = self.blobs.read(&self.nodes[i].blob).to_string();
            let emb: Option<Vec<f32>> = self.nodes[i]
                .embedding
                .as_ref()
                .and_then(|e| self.blobs.read_embedding(e));
            let bref = new_blobs.store(&text);
            let eref = emb.map(|v| new_blobs.store_embedding(&v));
            new_refs.push((bref, eref));
        }
        // Pass 2: reassign refs (mutates nodes; old store no longer borrowed).
        for (i, (bref, eref)) in new_refs.into_iter().enumerate() {
            self.nodes[i].blob = bref;
            self.nodes[i].embedding = eref;
        }
        self.blobs = new_blobs;
        let bytes_after = self.blobs.total_bytes();
        BlobVacuumReport {
            bytes_before,
            bytes_after,
            bytes_reclaimed: bytes_before.saturating_sub(bytes_after),
        }
    }
}
```
   Re-export `BlobVacuumReport` wherever `MergeReport` is re-exported (e.g. `src/lib.rs` `pub use`), so it's reachable as `grap::BlobVacuumReport`. Verify `self.blobs`, `self.nodes`, `self.deleted` field names match `KnowledgeGraph`; `BlobRef` import path.

- [ ] **Step 4: Verify** — both new unit tests pass; `cargo test -p grap` full suite green; `cargo build -p grap` warning-clean (doc the new public items); also `cargo test -p grap --features mmap` (vacuum reads from a possibly-mmapped store).

- [ ] **Step 5: Commit**
```
git add src/blob_store.rs src/graph_ops.rs src/lib.rs
git commit -m "feat(core): vacuum_blobs - compact the blob store, reclaim orphaned/tombstoned bytes"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: surface on `IndexedKnowledgeGraph` + pygrap

**Files:** `src/index.rs`, `crates/pygrap/src/indexed_graph.rs`, `crates/pygrap/smoke_test.py`.

- [ ] **Step 1: Failing test (Rust delegation)** — in `src/index.rs` tests:
```rust
#[test]
fn indexed_vacuum_blobs_reclaims_and_keeps_searchable() {
    let mut g = IndexedKnowledgeGraph::new();
    let a = g.add_node("brake redundancy requirement text", Some("REQ-1"), None);
    g.update_node(a, &"Z".repeat(4000), None).unwrap();   // orphan the big blob
    g.update_node(a, "brake redundancy requirement text", None).unwrap();
    let before = g.blob_bytes();
    let rep = g.vacuum_blobs();
    assert!(rep.bytes_reclaimed > 3000 && g.blob_bytes() < before);
    assert_eq!(g.read_blob(a), "brake redundancy requirement text");
    // text index still resolves the node (unchanged — keyed on NodeId)
    let hits = g.search_and_traverse_default("brake", 5, &[], 0.0, 2, 10);
    assert!(hits.iter().any(|r| r.graph_result.node_id == a));
}
```

- [ ] **Step 2: Run, verify FAIL** — methods missing.

- [ ] **Step 3: Implement.**
   `src/index.rs` (delegate to `self.graph`):
```rust
/// Blob-store bytes held (live + dead). See `vacuum_blobs`.
pub fn blob_bytes(&self) -> usize {
    self.graph.blob_bytes()
}
/// Compact the blob store (see `KnowledgeGraph::vacuum_blobs`). Indexes are
/// unaffected — they key on `NodeId`, which this does not change.
pub fn vacuum_blobs(&mut self) -> crate::graph_ops::BlobVacuumReport {
    self.graph.vacuum_blobs()
}
```
   (Use the correct path for `BlobVacuumReport`. The text/vector indexes need no update.)
   pygrap (`crates/pygrap/src/indexed_graph.rs`) — dict return:
```rust
/// Compact the on-disk blob store: reclaim bytes orphaned by node updates,
/// merges, and deletions. Returns {bytes_before, bytes_after, bytes_reclaimed}.
/// Node ids, edges, and search indexes are unchanged. Call `save_to_dir`
/// afterwards to shrink the persisted `graph.blobs`.
fn vacuum_blobs(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
    let r = self.inner.vacuum_blobs();
    let d = pyo3::types::PyDict::new(py);  // match the crate's PyDict ctor
    d.set_item("bytes_before", r.bytes_before)?;
    d.set_item("bytes_after", r.bytes_after)?;
    d.set_item("bytes_reclaimed", r.bytes_reclaimed)?;
    Ok(d.into())
}
```

- [ ] **Step 4: Verify** — Rust test passes; `cargo build -p pygrap`; `maturin build --release --manifest-path crates/pygrap/Cargo.toml -i C:/Python313/python.exe` + reinstall; extend `smoke_test.py`: add a node, `update_node` to a big body then small, `vacuum_blobs()` → assert `bytes_reclaimed > 0` and the node text still reads; `python crates/pygrap/smoke_test.py`.

- [ ] **Step 5: Commit**
```
git add src/index.rs crates/pygrap/src/indexed_graph.rs crates/pygrap/smoke_test.py
git commit -m "feat(pygrap): expose vacuum_blobs (blob-store compaction)"
```

---

## Task 3: `compact_graph` MCP tool

**Files:** `mcp-server/grap_mcp/server.py`. Test: `mcp-server/tests/test_compact.py`.

- [ ] **Step 1: Failing test** — `mcp-server/tests/test_compact.py` (mirror `test_analytics.py`'s fake/monkeypatch):
```python
import grap_mcp.server as s

class _CompactKG:
    def __init__(self): self.saved = False; self.vacuumed = False
    def vacuum_blobs(self):
        self.vacuumed = True
        return {"bytes_before": 9000, "bytes_after": 1000, "bytes_reclaimed": 8000}
    def vacuum_deleted_nodes(self): pass          # text-index vacuum (no-op in fake)
    def save_to_dir(self, path): self.saved = True

def test_compact_graph(tmp_path, monkeypatch):
    kg = _CompactKG()
    monkeypatch.setitem(s._state, "kg", kg)
    monkeypatch.setitem(s._state, "kg_path", str(tmp_path))
    out = s.compact_graph()
    assert out["status"] == "ok"
    assert out["bytes_reclaimed"] == 8000
    assert kg.vacuumed and kg.saved
```

- [ ] **Step 2: Run, verify FAIL** — tool undefined.

- [ ] **Step 3: Implement** in `server.py` (mirror the read/maintenance tool pattern; default `standard` tier — do NOT add to `_TIERS` unless you want it elsewhere):
```python
@tiered
def compact_graph() -> dict:
    """Reclaim disk/RAM held by dead blobs: compact the blob store (bytes orphaned
    by node updates/merges/deletes) and vacuum deleted nodes from the text index,
    then persist. Node ids, edges, and search results are unchanged."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.get("kg")
    if not kg:
        return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        report = kg.vacuum_blobs()
        try:
            kg.vacuum_deleted_nodes()   # text-index space reclamation (best-effort)
        except Exception:
            logger.warning("vacuum_deleted_nodes failed", exc_info=True)
        kg_path = _state.get("kg_path")
        if kg_path:
            kg.save_to_dir(kg_path)
        try:
            _get_workspace().record_event("graph_compacted", kg_path or "",
                                          details={"bytes_reclaimed": str(report.get("bytes_reclaimed", 0))})
        except Exception:
            logger.warning("audit record_event failed for graph_compacted", exc_info=True)
        return {"status": "ok", **report}
    except Exception as e:
        logger.exception("compact_graph failed")
        return {"status": "error", "message": str(e)}
```
   Confirm the pygrap KG exposes `vacuum_deleted_nodes()` (it does — `IndexedKnowledgeGraph::vacuum_deleted_nodes`; if the pygrap wrapper doesn't expose it, guard the call in try/except — the test's fake provides it). Register `compact_graph` in `list_capabilities` (a `maintenance` group, or the `audit`/`knowledge` group — pick one and keep the coverage test green).

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_compact.py -q`; full `python -m pytest -q` (capabilities coverage passes with the new tool registered).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_compact.py
git commit -m "feat(mcp): compact_graph tool (vacuum blobs + deleted nodes, persist)"
```
(Do NOT add out/, terminals/, mcps/, *.patch, *.png, *.pdf, CLAUDE.md, untracked docs/*.md.)

---

## Self-Review
- **Coverage:** core `vacuum_blobs` + `blob_bytes` + report (T1), indexed/pygrap surface (T2), MCP `compact_graph` (T3).
- **Type consistency:** `BlobVacuumReport { bytes_before, bytes_after, bytes_reclaimed }` across core→index→pygrap dict; `vacuum_blobs(&mut self) -> BlobVacuumReport`; pygrap dict keys match.
- **Correctness:** two-pass (read-old / reassign) avoids the borrow conflict; tombstoned slots kept (NodeIds stable) with bytes dropped; live `content_hash` unchanged (text identical); indexes untouched (keyed on NodeId); works on Chunks or mmap source.
- **No format change**; additive APIs; `vacuum_deleted_nodes` reused for text-index space.
- **Follow-up:** auto-compaction trigger (e.g. when dead-byte ratio exceeds a threshold on save) — out of scope.
