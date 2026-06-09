# Incremental (Watched) Ingestion (P1-E) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make re-ingesting a corpus cheap and non-duplicating — a content-hash manifest lets re-ingest skip unchanged chunks, add only new/changed ones, tombstone removed ones, and embed only what changed — so the KG can be a *living* index instead of growing duplicates on every run.

**Architecture:** Almost entirely the Python ingestion skin; the core prerequisite (`content_hash` per node, from feature C) already shipped. A per-corpus **manifest** (`{file: {chunk_sha256: node_id}}`, persisted as JSON next to the KG) is diffed against freshly-chunked files. Chunk identity = SHA-256 of the chunk's UTF-8 bytes — **the same hash the Rust core computes** (`lifecycle::hash::sha256_digest` over `text.as_bytes()`), so a manifest entry's hash equals `kg.get_node_content_hash(node_id)`. The only core/binding gap is a pygrap `remove_node` wrapper (the engine already has `IndexedKnowledgeGraph::remove_node`; pygrap may not expose it) needed to tombstone removed chunks.

**Tech Stack:** Python (`grap_mcp`), pygrap (one small binding if missing), `pytest` (+ a Rust binding test). No new Python dependencies (a simple mtime poll, not `watchdog`).

**Scope discipline:** No new Rust analytics/format changes. Chunk identity is by content hash (a file with two identical chunks dedups to one node — desirable). The "watch" is a minimal, dependency-free mtime poll exposed as a callable loop (NOT a blocking MCP tool — MCP tools must return).

---

## File Structure
- **Modify** `crates/pygrap/src/indexed_graph.rs` — add `remove_node(node_id)` PyO3 wrapper IF not already present.
- **Create** `mcp-server/grap_mcp/incremental.py` — `chunk_hash`, manifest load/save, `ingest_incremental`, `watch_paths`.
- **Modify** `mcp-server/grap_mcp/server.py` — `reingest_documents` `@mcp.tool()` + capabilities registration.
- **Tests** `crates/pygrap/smoke_test.py` (remove_node), `mcp-server/tests/test_incremental.py`.

Reuse: `grap_mcp/ingest.py::chunk_text(text, max_chars=800)`, the `_EXTS` set + file-walk from `ingest_paths`, `kg.add_node(text, label, embedding)`, `kg.get_node_content_hash(id)` (feature C), embedders from `grap_mcp/kee/embeddings.py`.

---

## Task 1: pygrap `remove_node` binding (tombstone support)

**Files:** `crates/pygrap/src/indexed_graph.rs`; `crates/pygrap/smoke_test.py`.

- [ ] **Step 1: Check first** — does pygrap's `IndexedKnowledgeGraph` already expose `remove_node`/`is_node_deleted`? (Read the `#[pymethods]` block; `is_node_deleted` is likely present, `remove_node` likely NOT.) If `remove_node` already exists, SKIP this task and report that. Otherwise continue.

- [ ] **Step 2: Failing smoke assertion** — add to `crates/pygrap/smoke_test.py`:
```python
n = g.add_node(text="to be removed", label="tmp")
assert g.remove_node(n) is True
assert g.is_node_deleted(n) is True
```
Run `python crates/pygrap/smoke_test.py` (after build) → AttributeError (no `remove_node`).

- [ ] **Step 3: Implement** — add to the `#[pymethods] impl` (mirror existing methods; the core has `IndexedKnowledgeGraph::remove_node(&mut self, NodeId) -> bool`):
```rust
/// Tombstone a node (soft delete). Returns true if it was live.
fn remove_node(&mut self, node_id: u32) -> bool {
    self.inner.remove_node(grap::NodeId(node_id))
}
```
(If `is_node_deleted` is also missing, add it likewise: `fn is_node_deleted(&self, node_id: u32) -> bool { self.inner.is_node_deleted(grap::NodeId(node_id)) }`.)

- [ ] **Step 4: Build + install + smoke** — `cargo build -p pygrap`; `maturin build --release --manifest-path crates/pygrap/Cargo.toml -i C:/Python313/python.exe`; `C:/Python313/python.exe -m pip install --force-reinstall --no-deps target/wheels/pygrap-0.1.0-cp313-cp313-win_amd64.whl`; `python crates/pygrap/smoke_test.py` → passes.

- [ ] **Step 5: Commit**
```
git add crates/pygrap/src/indexed_graph.rs crates/pygrap/smoke_test.py
git commit -m "feat(pygrap): expose remove_node (tombstone) for incremental ingest"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: `incremental.py` — manifest + hash-diff ingest

**Files:** Create `mcp-server/grap_mcp/incremental.py`. Test: `mcp-server/tests/test_incremental.py`.

- [ ] **Step 1: Failing test** — `mcp-server/tests/test_incremental.py`:
```python
import json
from pathlib import Path
import grap_mcp.incremental as inc

class _FakeKG:
    """Minimal stand-in: records add/remove, assigns sequential ids."""
    def __init__(self):
        self._next = 0
        self.live = {}   # id -> text
    def add_node(self, text, label=None, embedding=None):
        nid = self._next; self._next += 1
        self.live[nid] = text
        return nid
    def remove_node(self, nid):
        return self.live.pop(nid, None) is not None

def _write(p, text):
    p.write_text(text, encoding="utf-8")

def test_incremental_skips_unchanged_and_handles_edits(tmp_path):
    doc = tmp_path / "a.md"
    _write(doc, "Alpha block.\n\nBeta block.")
    man = tmp_path / "manifest.json"

    r1 = inc.ingest_incremental(_FakeKG(), str(tmp_path), manifest_path=str(man))
    assert r1["nodes_added"] == 2 and r1["nodes_removed"] == 0 and r1["skipped"] == 0

    # Re-ingest unchanged → everything skipped, nothing added/removed.
    kg2 = _FakeKG()
    r2 = inc.ingest_incremental(kg2, str(tmp_path), manifest_path=str(man))
    assert r2["nodes_added"] == 0 and r2["nodes_removed"] == 0 and r2["skipped"] == 2

    # Edit one block → 1 added, 1 removed (the changed chunk), 1 skipped.
    _write(doc, "Alpha block.\n\nBeta block EDITED.")
    kg3 = _FakeKG()
    r3 = inc.ingest_incremental(kg3, str(tmp_path), manifest_path=str(man))
    assert r3["nodes_added"] == 1 and r3["nodes_removed"] == 1 and r3["skipped"] == 1

    # Manifest persisted and reflects the final chunk set (2 chunks).
    saved = json.loads(man.read_text())
    assert sum(len(chunks) for chunks in saved.values()) == 2
```

- [ ] **Step 2: Run, verify FAIL** — `cd mcp-server && python -m pytest tests/test_incremental.py -q` → module missing.

- [ ] **Step 3: Implement** `mcp-server/grap_mcp/incremental.py`:
```python
"""Incremental, manifest-backed ingestion.

Re-ingesting a corpus normally duplicates every chunk. This module keeps a
content-hash manifest ({file: {chunk_sha256: node_id}}) so re-ingest only adds
new/changed chunks, tombstones removed ones, and embeds only what changed.

Chunk identity is SHA-256 of the chunk's UTF-8 bytes — identical to the hash
the Rust core stores per node, so manifest hashes line up with
kg.get_node_content_hash(...).
"""
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ingest import chunk_text, _EXTS  # reuse the existing chunker + extension set


def chunk_hash(chunk: str) -> str:
    """SHA-256 hex of a chunk's UTF-8 bytes (matches the core node hash)."""
    return hashlib.sha256(chunk.encode("utf-8")).hexdigest()


def load_manifest(path: Optional[str]) -> Dict[str, Dict[str, int]]:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_manifest(path: Optional[str], manifest: Dict[str, Dict[str, int]]) -> None:
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, separators=(",", ":"))
    os.replace(tmp, path)


def _files_under(root: str) -> List[Path]:
    p = Path(root)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(f for f in p.rglob("*") if f.suffix.lower() in _EXTS)
    raise FileNotFoundError(f"ingest path not found: {root}")


def ingest_incremental(kg, root: str, embedder=None,
                       manifest_path: Optional[str] = None) -> Dict[str, Any]:
    """Diff each file's chunks against the manifest; add new, tombstone removed,
    skip unchanged. Embeds only newly-added chunks. Returns counts and persists
    the manifest. `manifest_path` defaults to `<root>/.grap-ingest-manifest.json`
    when `root` is a directory."""
    if manifest_path is None and Path(root).is_dir():
        manifest_path = str(Path(root) / ".grap-ingest-manifest.json")
    manifest = load_manifest(manifest_path)

    added = removed = skipped = 0
    for f in _files_under(root):
        key = str(f.resolve())
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        chunks = chunk_text(text)
        new_by_hash: Dict[str, str] = {chunk_hash(c): c for c in chunks}
        prev = manifest.get(key, {})

        # Removed chunks: in manifest, not in new set → tombstone.
        for h, nid in list(prev.items()):
            if h not in new_by_hash:
                try:
                    kg.remove_node(nid)
                except Exception:
                    pass
                removed += 1

        # New chunks: in new set, not in manifest → add (embed only these).
        to_add = [(h, c) for h, c in new_by_hash.items() if h not in prev]
        embeddings = embedder.embed([c for _, c in to_add]) if (embedder and to_add) else None
        new_manifest_file: Dict[str, int] = {}
        for h in new_by_hash:
            if h in prev and h in new_by_hash:
                # unchanged → keep existing node id, skip
                new_manifest_file[h] = prev[h]
        for i, (h, c) in enumerate(to_add):
            label = f"doc:{f.stem}#{h[:8]}"
            if embeddings is not None:
                nid = kg.add_node(text=c, label=label, embedding=embeddings[i])
            else:
                nid = kg.add_node(text=c, label=label)
            new_manifest_file[h] = nid
            added += 1

        skipped += sum(1 for h in new_by_hash if h in prev)
        if new_manifest_file:
            manifest[key] = new_manifest_file
        else:
            manifest.pop(key, None)

    save_manifest(manifest_path, manifest)
    return {"nodes_added": added, "nodes_removed": removed, "skipped": skipped,
            "manifest_path": manifest_path, "embedded": bool(embedder)}


def watch_paths(kg, root: str, embedder=None, manifest_path: Optional[str] = None,
                interval: float = 2.0, iterations: Optional[int] = None,
                on_change=None) -> int:
    """Dependency-free mtime poll: re-run `ingest_incremental` whenever a file
    under `root` changes. Runs forever unless `iterations` is set (tests pass
    iterations=1). Returns the number of re-ingests performed."""
    mtimes: Dict[str, float] = {}
    runs = 0
    i = 0
    while iterations is None or i < iterations:
        i += 1
        changed = False
        for f in _files_under(root):
            m = f.stat().st_mtime
            if mtimes.get(str(f)) != m:
                mtimes[str(f)] = m
                changed = True
        if changed:
            res = ingest_incremental(kg, root, embedder, manifest_path)
            runs += 1
            if on_change:
                on_change(res)
        if iterations is None:
            time.sleep(interval)
    return runs
```
   **Verify the `from .ingest import chunk_text, _EXTS` works** — confirm `_EXTS` is the module-level name in `ingest.py` (it is referenced there). If it's named differently, import the right symbol or inline the extension set `{".md", ".txt"}`.

- [ ] **Step 4: Run, verify PASS** — `cd mcp-server && python -m pytest tests/test_incremental.py -q`. Add a second test for `watch_paths(..., iterations=1)` that asserts it performs one re-ingest on first poll.

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/incremental.py mcp-server/tests/test_incremental.py
git commit -m "feat(mcp): incremental manifest-backed ingestion (hash-diff skip/add/tombstone)"
```

---

## Task 3: MCP `reingest_documents` tool

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `mcp-server/tests/test_incremental.py`.

- [ ] **Step 1: Failing test** — mirror `test_analytics.py` (`import grap_mcp.server as s`, monkeypatch `_state`). Inject a `_FakeKG` (with `add_node`/`remove_node`) and a `kg_path`, call `s.reingest_documents(str(tmp_path))`, assert `status == "ok"` and the returned counts. (Use `monkeypatch.setitem(s._state, "kg", fake)` and `monkeypatch.setitem(s._state, "kg_path", str(tmp_path))`; default role satisfies `_require("operator")`.)

- [ ] **Step 2: Run, verify FAIL** — tool undefined.

- [ ] **Step 3: Implement** — add to `server.py`, mirroring `ingest_documents` (~line 170) including the save + audit-event behavior:
```python
@mcp.tool()
def reingest_documents(path: str) -> dict:
    """Incrementally (re)ingest .md/.txt under `path`: skip unchanged chunks,
    add new/changed ones, tombstone removed ones. Non-duplicating."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.get("kg")
    if not kg:
        return {"status": "error", "message": "Load or create a Knowledge Graph first."}
    try:
        from grap_mcp import incremental
        kg_path = _state.get("kg_path")
        manifest_path = (str(__import__("pathlib").Path(kg_path) / "ingest-manifest.json")
                         if kg_path else None)
        result = incremental.ingest_incremental(kg, path, manifest_path=manifest_path)
        if kg_path:
            kg.save_to_dir(kg_path)
        try:
            _get_workspace().record_event("documents_reingested", path,
                                          details={"nodes_added": str(result["nodes_added"]),
                                                   "nodes_removed": str(result["nodes_removed"]),
                                                   "skipped": str(result["skipped"])})
        except Exception:
            logger.warning("audit record_event failed for documents_reingested", exc_info=True)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("reingest_documents failed")
        return {"status": "error", "message": str(e)}
```
   Register `reingest_documents` in `list_capabilities` (the "knowledge" group, next to `ingest_documents`).

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_incremental.py -q`; then full `python -m pytest -q` (capabilities coverage must pass).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_incremental.py
git commit -m "feat(mcp): reingest_documents tool (incremental, manifest-backed)"
```
(Do NOT add out/, terminals/, mcps/, *.patch, *.png, *.pdf, CLAUDE.md, untracked docs/*.md.)

---

## Self-Review
- **Coverage:** tombstone binding (T1), incremental diff + manifest + watch (T2), MCP tool (T3). Matches the P1-E architecture; uses the C content-hash prerequisite.
- **Type consistency:** `chunk_hash(str)->str`, `ingest_incremental(kg, root, embedder, manifest_path)->{nodes_added,nodes_removed,skipped,manifest_path,embedded}`, `watch_paths(..., iterations)` consistent T2–T3. `kg.remove_node(id)->bool` / `kg.add_node(text,label,embedding)->id` match pygrap.
- **Correctness:** unchanged chunks keep their node id (no churn); changed = remove old + add new; embeddings computed only for added chunks; manifest persisted atomically; identical-within-file chunks dedup by hash (documented).
- **Local-first / simple:** no new deps; the watcher is a dependency-free mtime poll with a deterministic `iterations` hook for tests; blocking watch is a function/CLI, not an MCP tool.
- **Follow-up (out of scope):** `watchdog`-based event watching; using `kg.update_node` for in-place content edits instead of remove+add.
