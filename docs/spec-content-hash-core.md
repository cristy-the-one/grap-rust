# Node Content-Hashing (Rust core) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every KG node a stable SHA-256 content hash, computed in the Rust core, persisted across save/load, with a self-verify integrity check — the foundation for incremental re-ingest, provenance integrity, and result-freshness.

**Architecture:** Add a `content_hash: [u8; 32]` field to the core `Node`, computed at `add_node`/`update_node_content` time by **reusing** the existing SHA-256 in `src/lifecycle/hash.rs` (no parallel hasher). Persist it in `SerializableNode` with `#[serde(default)]` so old graphs load and lazily back-fill. Expose `node_content_hash(id)` and `verify_node(id)` on `KnowledgeGraph` and `IndexedKnowledgeGraph`. **All of this is core** — it is per-node CPU/memory work that must be blazing fast and is reused by ingestion, analytics, and the MCP. The *source-drift* check (comparing a node hash to a freshly hashed source chunk) is the only "skin" piece and is deferred to the incremental-ingest spec (P1).

**Tech Stack:** Rust, `sha2` (already a dependency via `lifecycle/hash.rs`), `bincode`/`serde` (existing persistence), `cargo test`.

**Allocation rationale (core vs skin):** Hashing node bytes is an engine concern — it touches the `BlobStore`, must run on every mutation at memory speed, and is consumed by three downstream subsystems. It belongs in the core. Nothing here is presentation/transport, so nothing goes to the MCP.

---

## File Structure

- **Modify** `src/lifecycle/hash.rs` — extract a raw `sha256_digest(bytes) -> [u8; 32]` helper; keep `ContentHash::of` as a thin hex wrapper over it.
- **Modify** `src/graph.rs` — add `content_hash` to `Node`; compute it in `add_node`; add `node_content_hash` + `verify_node` + a `NodeIntegrity` enum; back-fill in `from_parts`/`from_parts_with_deleted`.
- **Modify** `src/graph_ops.rs` — recompute `content_hash` in `update_node_content`.
- **Modify** `src/serialize.rs` — add `content_hash` to `SerializableNode` (`#[serde(default)]`); bump the format version; back-fill on load when absent.
- **Modify** `src/index.rs` — re-export `node_content_hash`/`verify_node`/`NodeIntegrity` on `IndexedKnowledgeGraph`.
- **Test** `tests/content_hash.rs` (new integration test) + unit tests in the modified files.

---

## Task 1: Raw digest helper in `hash.rs`

**Files:**
- Modify: `src/lifecycle/hash.rs:11-20`
- Test: `src/lifecycle/hash.rs` (unit test module at bottom)

- [ ] **Step 1: Write the failing test**

Add to the `#[cfg(test)] mod tests` block in `src/lifecycle/hash.rs`:

```rust
#[test]
fn digest_matches_content_hash_hex() {
    let d = sha256_digest(b"hello");
    let hex: String = d.iter().map(|b| format!("{:02x}", b)).collect();
    assert_eq!(hex, ContentHash::of(b"hello").as_str());
    assert_eq!(d.len(), 32);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib lifecycle::hash::tests::digest_matches_content_hash_hex`
Expected: FAIL — `cannot find function sha256_digest`.

- [ ] **Step 3: Write minimal implementation**

In `src/lifecycle/hash.rs`, add the helper and refactor `of` to call it:

```rust
/// Raw 32-byte SHA-256 digest. The lean form used for per-node hashing;
/// `ContentHash::of` is the hex-string wrapper for human-facing IDs.
pub(crate) fn sha256_digest(bytes: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hasher.finalize().into()
}

impl ContentHash {
    /// Compute the content hash of `bytes`.
    pub fn of(bytes: &[u8]) -> Self {
        let digest = sha256_digest(bytes);
        let mut hex = String::with_capacity(64);
        for b in digest {
            hex.push_str(&format!("{:02x}", b));
        }
        ContentHash(hex)
    }
}
```

Add `pub(crate) use lifecycle::hash::sha256_digest;` only if `graph.rs`/`graph_ops.rs` cannot reach it via path; otherwise reference as `crate::lifecycle::hash::sha256_digest`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib lifecycle::hash::tests::digest_matches_content_hash_hex`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lifecycle/hash.rs
git commit -m "refactor(core): extract sha256_digest helper from ContentHash::of"
```

---

## Task 2: `content_hash` field on `Node`, computed at `add_node`

**Files:**
- Modify: `src/graph.rs:18-26` (the `Node` struct), `src/graph.rs:90-112` (`add_node`)
- Test: `src/graph.rs` unit tests

- [ ] **Step 1: Write the failing test**

Add to the `#[cfg(test)] mod tests` in `src/graph.rs`:

```rust
#[test]
fn add_node_stores_content_hash() {
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("memory safety", Some("Rust"), None);
    let expected = crate::lifecycle::hash::sha256_digest(b"memory safety");
    assert_eq!(g.node_content_hash(n), Some(expected));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib graph::tests::add_node_stores_content_hash`
Expected: FAIL — no field `content_hash` / no method `node_content_hash`.

- [ ] **Step 3: Write minimal implementation**

In the `Node` struct (`src/graph.rs:18`), add the field (keep it `pub(crate)`-visible to the crate as the struct is):

```rust
pub(crate) struct Node {
    blob: BlobRef,
    embedding: Option<BlobRef>,
    outgoing: Vec<EdgeId>,
    incoming: Vec<EdgeId>,
    label: Option<String>,
    outgoing_property_mask: u64,
    /// SHA-256 of the node's text blob at write time. Lean [u8;32] form;
    /// hex via `crate::lifecycle::hash::ContentHash`. Back-filled on load
    /// of pre-v<N> graphs (see serialize.rs).
    content_hash: [u8; 32],
}
```

In `add_node` (`src/graph.rs:90`), compute and set it:

```rust
pub fn add_node(
    &mut self,
    text: &str,
    label: Option<&str>,
    embedding: Option<&[f32]>,
) -> NodeId {
    let content_hash = crate::lifecycle::hash::sha256_digest(text.as_bytes());
    let blob = self.blobs.store(text);
    let embedding_ref = embedding.map(|e| self.blobs.store_embedding(e));

    let id = NodeId(
        u32::try_from(self.nodes.len()).expect("node count exceeds u32::MAX — graph too large"),
    );
    self.nodes.push(Node {
        blob,
        embedding: embedding_ref,
        outgoing: Vec::new(),
        incoming: Vec::new(),
        label: label.map(|s| s.to_string()),
        outgoing_property_mask: 0,
        content_hash,
    });
    self.deleted.push(false);
    id
}
```

Add the accessor (near `read_blob`, ~`src/graph.rs:176`):

```rust
/// Returns the stored SHA-256 content hash of a node's text blob, or
/// `None` if the id is out of bounds.
pub fn node_content_hash(&self, node: NodeId) -> Option<[u8; 32]> {
    self.nodes.get(node.0 as usize).map(|n| n.content_hash)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib graph::tests::add_node_stores_content_hash`
Expected: PASS. (Other call sites that construct `Node` literally — `from_parts*` — will fail to compile; fixed in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add src/graph.rs
git commit -m "feat(core): store SHA-256 content_hash on every node"
```

---

## Task 3: Recompute hash on `update_node_content`

**Files:**
- Modify: `src/graph_ops.rs:238-257`
- Test: `src/graph_ops.rs` unit tests

- [ ] **Step 1: Write the failing test**

Add to `#[cfg(test)] mod tests` in `src/graph_ops.rs`:

```rust
#[test]
fn update_node_content_refreshes_hash() {
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("old text", None, None);
    let before = g.node_content_hash(n).unwrap();
    g.update_node_content(n, "new text", None).unwrap();
    let after = g.node_content_hash(n).unwrap();
    assert_ne!(before, after);
    assert_eq!(after, crate::lifecycle::hash::sha256_digest(b"new text"));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib graph_ops::tests::update_node_content_refreshes_hash`
Expected: FAIL — `after == before` (hash not updated).

- [ ] **Step 3: Write minimal implementation**

In `update_node_content` (`src/graph_ops.rs:238`), after setting the new blob:

```rust
let new_blob = self.blobs.store(new_text);
self.nodes[idx].blob = new_blob;
self.nodes[idx].content_hash = crate::lifecycle::hash::sha256_digest(new_text.as_bytes());
if let Some(emb) = new_embedding {
    self.nodes[idx].embedding = Some(self.blobs.store_embedding(emb));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib graph_ops::tests::update_node_content_refreshes_hash`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/graph_ops.rs
git commit -m "feat(core): refresh content_hash when node content is updated"
```

---

## Task 4: Persist `content_hash`; back-fill old graphs; bump version

**Files:**
- Modify: `src/serialize.rs:33-45` (`SerializableNode`), the node round-trip in `write_graph_to_dir` (`src/serialize.rs:291-397`) and `load_from_disk` (`src/serialize.rs:558-729`), and the format-version constant behind `GraphMeta::version()`.
- Modify: `src/graph.rs:325-354` (`from_parts_with_deleted` / `from_parts`) to back-fill when a hash is absent (all-zero).
- Test: `tests/content_hash.rs` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/content_hash.rs`:

```rust
use grap::KnowledgeGraph;
use tempfile::tempdir;

#[test]
fn content_hash_survives_save_load() {
    let dir = tempdir().unwrap();
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("durable knowledge", Some("L"), None);
    let h = g.node_content_hash(n).unwrap();
    g.save_to_disk(dir.path()).unwrap();

    let g2 = KnowledgeGraph::load_from_disk(dir.path()).unwrap();
    assert_eq!(g2.node_content_hash(n), Some(h));
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --test content_hash content_hash_survives_save_load`
Expected: FAIL — hash not persisted (loaded value differs / is zero).

- [ ] **Step 3: Write minimal implementation**

In `SerializableNode` (`src/serialize.rs:33`) add the field with a default so older payloads deserialize:

```rust
#[serde(default)]
content_hash: [u8; 32],
```

In `write_graph_to_dir`, when building each `SerializableNode`, copy `node.content_hash`. In `load_from_disk`, pass the deserialized `content_hash` through to the reconstructed `Node`. In `from_parts_with_deleted` (`src/graph.rs:325`), back-fill any all-zero hash by re-hashing the blob:

```rust
for node in nodes.iter_mut() {
    if node.content_hash == [0u8; 32] {
        let text = blobs.read(&node.blob);
        node.content_hash = crate::lifecycle::hash::sha256_digest(text.as_bytes());
    }
}
```

Bump the format version so `load_meta` reports the new shape. Find the constant:

Run: `rg -n "version" src/serialize.rs | rg -i "const|CURRENT"`

Increment that constant by 1 (e.g. `CURRENT_VERSION: u16 = <old+1>`) and leave `is_current_version` logic intact — the `#[serde(default)]` + back-fill makes old graphs forward-compatible, so loading an older version must still succeed (verify `load_meta_and_version_rejection` still passes or update its expectation).

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --test content_hash content_hash_survives_save_load`
Run: `cargo test -p grap --test persistence_roundtrip`
Expected: both PASS (existing roundtrip tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/serialize.rs src/graph.rs
git commit -m "feat(core): persist node content_hash; back-fill legacy graphs; bump format version"
```

---

## Task 5: `verify_node` self-integrity check

**Files:**
- Modify: `src/graph.rs` (add `NodeIntegrity` enum + `verify_node`)
- Test: `src/graph.rs` unit tests

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn verify_node_reports_match_for_intact_node() {
    let mut g = KnowledgeGraph::new();
    let n = g.add_node("intact", None, None);
    assert_eq!(g.verify_node(n), crate::graph::NodeIntegrity::Match);
    assert_eq!(g.verify_node(NodeId(999)), crate::graph::NodeIntegrity::Unknown);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib graph::tests::verify_node_reports_match_for_intact_node`
Expected: FAIL — no `NodeIntegrity` / no `verify_node`.

- [ ] **Step 3: Write minimal implementation**

In `src/graph.rs`:

```rust
/// Result of re-hashing a node's stored blob and comparing it to the
/// stored `content_hash`. This is the self-referential ("cache") integrity
/// check; comparing against an external source is the ingestion layer's job.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeIntegrity {
    /// Re-hash of the blob matches the stored content_hash.
    Match,
    /// Re-hash differs — the index is internally incoherent (tamper/corruption).
    Mismatch,
    /// Node id is out of bounds or tombstoned.
    Unknown,
}

impl KnowledgeGraph {
    /// Re-hash a node's current blob and compare to its stored content_hash.
    pub fn verify_node(&self, node: NodeId) -> NodeIntegrity {
        let idx = node.0 as usize;
        if idx >= self.nodes.len() || self.deleted.get(idx).copied().unwrap_or(true) {
            return NodeIntegrity::Unknown;
        }
        let n = &self.nodes[idx];
        let actual = crate::lifecycle::hash::sha256_digest(self.blobs.read(&n.blob).as_bytes());
        if actual == n.content_hash {
            NodeIntegrity::Match
        } else {
            NodeIntegrity::Mismatch
        }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p grap --lib graph::tests::verify_node_reports_match_for_intact_node`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/graph.rs
git commit -m "feat(core): add verify_node self-integrity check (NodeIntegrity)"
```

---

## Task 6: Surface on `IndexedKnowledgeGraph` + pygrap

**Files:**
- Modify: `src/index.rs` (delegating wrappers near `read_blob` ~`src/index.rs:1227`)
- Modify: `crates/pygrap/src/indexed_graph.rs` (new `get_node_content_hash` + `verify_node` methods, near `add_node`/`get_node_text`)
- Test: `src/index.rs` unit test + `crates/pygrap/smoke_test.py`

- [ ] **Step 1: Write the failing test (Rust delegation)**

In `src/index.rs` tests:

```rust
#[test]
fn indexed_exposes_content_hash() {
    let mut g = IndexedKnowledgeGraph::new();
    let n = g.add_node("x", None, None);
    assert!(g.node_content_hash(n).is_some());
    assert_eq!(g.verify_node(n), crate::graph::NodeIntegrity::Match);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p grap --lib index::tests::indexed_exposes_content_hash`
Expected: FAIL — methods not on `IndexedKnowledgeGraph`.

- [ ] **Step 3: Write minimal implementation**

In `src/index.rs` (delegate to `self.graph`):

```rust
/// SHA-256 content hash of a node's blob (see `KnowledgeGraph::node_content_hash`).
pub fn node_content_hash(&self, node: NodeId) -> Option<[u8; 32]> {
    self.graph.node_content_hash(node)
}

/// Self-integrity check (see `KnowledgeGraph::verify_node`).
pub fn verify_node(&self, node: NodeId) -> crate::graph::NodeIntegrity {
    self.graph.verify_node(node)
}
```

In `crates/pygrap/src/indexed_graph.rs`, add PyO3 methods returning a hex string / status string (Python-friendly):

```rust
/// Hex SHA-256 of the node's text, or None.
fn get_node_content_hash(&self, node_id: u32) -> Option<String> {
    self.inner
        .node_content_hash(grap::NodeId(node_id))
        .map(|d| d.iter().map(|b| format!("{:02x}", b)).collect())
}

/// "match" | "mismatch" | "unknown".
fn verify_node(&self, node_id: u32) -> String {
    match self.inner.verify_node(grap::NodeId(node_id)) {
        grap::graph::NodeIntegrity::Match => "match",
        grap::graph::NodeIntegrity::Mismatch => "mismatch",
        grap::graph::NodeIntegrity::Unknown => "unknown",
    }
    .to_string()
}
```

(Ensure `NodeIntegrity` and `NodeId` are re-exported from `grap` so pygrap can name them; add `pub use graph::NodeIntegrity;` to `src/lib.rs` if needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test -p grap --lib index::tests::indexed_exposes_content_hash`
Run: `maturin develop --manifest-path crates/pygrap/Cargo.toml --release && python crates/pygrap/smoke_test.py`
Expected: Rust test PASS; smoke test runs without error (extend it to call `get_node_content_hash` on a freshly added node and assert a 64-char string).

- [ ] **Step 5: Commit**

```bash
git add src/index.rs src/lib.rs crates/pygrap/src/indexed_graph.rs crates/pygrap/smoke_test.py
git commit -m "feat(pygrap): expose node content hash + verify_node"
```

---

## Self-Review

- **Spec coverage:** field on `Node` (T2), compute on add (T2) + update (T3), persist + version + back-fill (T4), self-verify (T5), surface to `IndexedKnowledgeGraph` + pygrap (T6), reuse existing hasher (T1). All covered.
- **Type consistency:** `[u8; 32]` everywhere in Rust; hex `String` only at the pygrap boundary. `NodeIntegrity::{Match,Mismatch,Unknown}` used identically in T5/T6. `sha256_digest` signature identical across T1–T5.
- **Downstream hooks (do not implement here):** incremental-ingest dedup keys on `node_content_hash`; source-drift verify (the `git_sha`-analog) compares it to a freshly hashed source chunk — both specced in the P1 incremental-ingest section of [`borrowing-from-jcodemunch.md`](borrowing-from-jcodemunch.md).
