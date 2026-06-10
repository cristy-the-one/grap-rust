# Lift the 4 GiB mmap cap: `BlobRef.offset` → u64 (P2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the fast memory-mapped open (`load_mmap`) usable for **multi-GB** corpora. Today the mmap loader interprets `BlobRef.offset` as a *global* byte offset into the flat `graph.blobs` file, but `BlobRef.offset` is `u32` — so a blob file > 4 GiB trips a hard error and the caller falls back to the eager `load_from_disk` (the whole-corpus-in-RAM path the mmap work was meant to avoid). Widening `BlobRef.offset` to `u64` removes the cap.

**Key fact — no on-disk format change.** `SerializableNode.blob_global_offset` (and `embedding_global_offset`) are **already `u64`** on disk; `GraphMeta.chunk_boundaries`/`blob_total_bytes` are already `u64`; `BlobRef` is **in-memory only** (never serialized — the wire form is `SerializableNode`). So the bottleneck is purely the in-memory `BlobRef.offset: u32`. Widening it needs **no format bump and no back-compat handling**.

**Cost:** `BlobRef` grows 16 → 24 bytes (u64 offset forces 8-byte alignment). It's the **cold-path** blob handle — the hot traversal path is the arena adjacency (`Node.outgoing/incoming`), not `BlobRef` — so this does not affect traversal speed. `length`/`uncompressed_len` stay `u32` (a single blob is never > 4 GiB) and `chunk_idx` stays `u32` (4 MiB chunks → 4 GiB needs ~1024 chunks).

**Scope:** in-memory type widening + remove the loader's u32 guard + update comments/docs. No pygrap API change (the cap note in `open_mmap`'s docstring is updated). No format change.

---

## File Structure
- **Modify** `src/blob_store.rs` — `BlobRef.offset: u32 -> u64`; `store`/`store_bytes` set `offset: ... as u64`; update the Mmap `read` comment.
- **Modify** `src/serialize.rs` — portable `load_from_disk` builds `BlobRef { offset: <local> as u64 }`; the real `load_mmap` sets `offset: sn.blob_global_offset` directly (remove the `global_to_u32` guard); update comments.
- **Modify** `crates/pygrap/src/indexed_graph.rs` — update the `open_mmap` docstring (the 4 GiB caveat is gone).
- **Modify** `docs/core-efficiency-assessment.md` + `docs/spec-mmap-readonly-open.md` — note the cap is lifted.
- **Tests** `tests/mmap_roundtrip.rs` + a `blob_store.rs`/`serialize.rs` unit test for the u64 plumbing.

---

## Task 1: widen `BlobRef.offset` to u64 + drop the loader cap

**Files:** `src/blob_store.rs`, `src/serialize.rs`. Tests: `src/blob_store.rs` unit + `tests/mmap_roundtrip.rs`.

- [ ] **Step 1: Failing test** — add to `src/blob_store.rs` `#[cfg(test)] mod tests` a test proving the offset field is u64-capable and reads use it correctly:
```rust
#[test]
fn blobref_offset_is_u64_capable() {
    // A global offset beyond u32::MAX must be representable (mmap-mode addressing
    // for >4 GiB blob files). This is a plumbing check; we don't allocate 4 GiB.
    let r = BlobRef { chunk_idx: 0, offset: (u32::MAX as u64) + 1234, length: 4, uncompressed_len: 4 };
    assert_eq!(r.offset, 4_294_968_529);
}
```
(Run `cargo test -p grap --lib blob_store::tests::blobref_offset_is_u64_capable` → FAIL: `offset` is `u32`, the literal overflows / type mismatch.)

- [ ] **Step 2: Implement.**
   `src/blob_store.rs`:
   - Change the field: `pub offset: u32,` → `pub offset: u64,` (keep `chunk_idx: u32`, `length: u32`, `uncompressed_len: u32`). Update the doc comment ("Byte offset within that chunk" → "Byte offset: local within the chunk for in-memory `Chunks`, or the global offset into the mapped file for `Storage::Mmap`.").
   - In `store` and `store_bytes`, the `BlobRef { ..., offset: offset as u32, ... }` literals → `offset: offset as u64` (`offset` is a `usize` local = chunk length; `as u64`).
   - The `read`/`read_bytes` arms already do `let start = r.offset as usize;` — leave as-is (u64 → usize is correct on 64-bit; on 32-bit, offsets > 4 GiB are inherently unsupported, which is fine). Update the inline comment at the Mmap `read` arm to drop "we currently store global offset in .offset / flat is simpler" hedging and state plainly that `offset` is the global offset into the map.
   `src/serialize.rs`:
   - `write_graph_to_dir` line ~320/327: `chunk_base + node.blob.offset as u64` — `node.blob.offset` is now `u64`, so `as u64` is a redundant cast; either drop the cast (`chunk_base + node.blob.offset`) or leave it (harmless). Prefer dropping for clarity.
   - `load_from_disk` (portable): `find_chunk_for_offset` returns `(u32 chunk_idx, u32 local_offset)`; the `BlobRef { chunk_idx, offset: local_offset, ... }` now needs `offset: local_offset as u64` (local < 4 MiB, fits). Same for the embedding `BlobRef`. The content-hash recompute `let start = blob.offset as usize;` is unchanged (works on u64).
   - The real `load_mmap`: REMOVE the `global_to_u32` helper and the 4 GiB error; build `BlobRef { chunk_idx: 0, offset: sn.blob_global_offset, length: sn.blob_length, uncompressed_len: sn.blob_uncompressed_len }` (offset is the u64 global offset directly), same for the embedding. Keep the per-node bounds check (`blob_global_offset + blob_length <= meta.blob_total_bytes`, all u64). Update the comments that mention the 4 GiB cap / `BlobRef.offset is u32`.
   - `grep -n "\.offset" src/*.rs` to catch any other construction/use site and adjust (`as usize` reads are fine; `as u32` constructions must become `as u64`).

- [ ] **Step 3: Verify** — `cargo test -p grap --lib blob_store::tests::blobref_offset_is_u64_capable` PASS; `cargo test -p grap` (no feature) green; `cargo test -p grap --features mmap` green (the mmap round-trips exercise the u64 loader path at small offsets); `cargo build -p grap` + `--features mmap` → 0 warnings.

- [ ] **Step 4: Commit**
```
git add src/blob_store.rs src/serialize.rs
git commit -m "feat(core): widen BlobRef.offset to u64 - lift the 4 GiB mmap blob-file cap"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

## Task 2: a serialize-level regression test + doc updates

**Files:** `tests/mmap_roundtrip.rs`, `crates/pygrap/src/indexed_graph.rs`, `docs/core-efficiency-assessment.md`, `docs/spec-mmap-readonly-open.md`.

- [ ] **Step 1: Test** — add to `tests/mmap_roundtrip.rs` (`#![cfg(feature="mmap")]`) a test that a normal save→`load_mmap` round-trip still reads correctly AND that the loader no longer has a u32 ceiling by asserting a multi-node graph maps & reads (the existing tests cover correctness; add one that builds, say, a few hundred small nodes so the flat file has non-trivial offsets, saves, `load_mmap`, and reads the last node correctly — confirming offset plumbing through u64):
```rust
#[test]
fn mmap_reads_correct_text_at_nontrivial_offsets() {
    let dir = tempdir().unwrap();
    let mut g = KnowledgeGraph::new();
    let mut last = grap::NodeId(0);
    for i in 0..500 {
        last = g.add_node(&format!("node number {i} with some body text"), None, None);
    }
    g.save_to_disk(dir.path()).unwrap();
    let g2 = KnowledgeGraph::load_mmap(dir.path()).unwrap();
    assert!(g2.blobs_are_mapped());
    assert_eq!(g2.read_blob(last), "node number 499 with some body text");
}
```
(A genuine > 4 GiB test is infeasible in CI; the u64 widening is verified by the type test in Task 1 + this offset round-trip. Note that in the report.)

- [ ] **Step 2: Doc updates.**
   - `crates/pygrap/src/indexed_graph.rs` `open_mmap` docstring: remove/replace any "4 GiB" caveat — mmap mode now supports multi-GB blob files.
   - `docs/core-efficiency-assessment.md`: in the P2 list, mark "u64 blob offsets" as done / move it to the done table; remove the "4 GiB cap" caveat from the P0 section (or note it's lifted).
   - `docs/spec-mmap-readonly-open.md`: update the "4 GiB cap" caveat to "lifted (BlobRef.offset is u64 as of <commit>)".

- [ ] **Step 3: Verify** — `cargo test -p grap --features mmap --test mmap_roundtrip` (all pass); `cargo build -p pygrap` (doc-only change compiles).

- [ ] **Step 4: Commit**
```
git add tests/mmap_roundtrip.rs crates/pygrap/src/indexed_graph.rs docs/core-efficiency-assessment.md docs/spec-mmap-readonly-open.md
git commit -m "test+docs(core): mmap offset round-trip; note 4 GiB cap lifted"
```
(Do NOT add out/, terminals/, mcps/, *.patch, *.png, *.pdf, CLAUDE.md, untracked docs/*.md beyond the two named.)

---

## Self-Review
- **Coverage:** field widen + all construction/read sites (T1), regression test + doc cleanup (T2).
- **No format change:** `SerializableNode`/`GraphMeta` already u64; `BlobRef` is in-memory only → existing graphs load unchanged; no version bump.
- **Correctness:** Chunks mode unaffected (local offset < 4 MiB, fits u64 trivially; chunk_idx still addresses the global position); mmap mode now uses the full u64 global offset; reads (`as usize`) correct on 64-bit; bounds check stays.
- **Perf:** `BlobRef` 16→24 bytes on the cold blob-handle path; hot traversal (adjacency) untouched.
- **Follow-up (still open):** CSR adjacency (P2) for cache locality at very large scale; true chunked mmap (vs flat) only needed if a single mapping > usize on 32-bit, which is out of scope.
