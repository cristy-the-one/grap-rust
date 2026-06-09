"""Incremental, manifest-backed ingestion.

Re-ingesting a corpus normally duplicates every chunk. This keeps a
content-hash manifest ({file: {chunk_sha256: node_id}}) so re-ingest only adds
new/changed chunks, tombstones removed ones, and embeds only what changed.
Chunk identity = SHA-256 of the chunk's UTF-8 bytes (matches the core node hash).
"""
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .ingest import chunk_text, _EXTS


def chunk_hash(chunk: str) -> str:
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
        for h, nid in list(prev.items()):
            if h not in new_by_hash:
                try:
                    kg.remove_node(nid)
                except Exception:
                    pass
                removed += 1
        to_add = [(h, c) for h, c in new_by_hash.items() if h not in prev]
        embeddings = embedder.embed([c for _, c in to_add]) if (embedder and to_add) else None
        new_file: Dict[str, int] = {}
        for h in new_by_hash:
            if h in prev:
                new_file[h] = prev[h]   # unchanged → keep node id
        for i, (h, c) in enumerate(to_add):
            label = f"doc:{f.stem}#{h[:8]}"
            if embeddings is not None:
                nid = kg.add_node(text=c, label=label, embedding=embeddings[i])
            else:
                nid = kg.add_node(text=c, label=label)
            new_file[h] = nid
            added += 1
        skipped += sum(1 for h in new_by_hash if h in prev)
        if new_file:
            manifest[key] = new_file
        else:
            manifest.pop(key, None)
    save_manifest(manifest_path, manifest)
    return {"nodes_added": added, "nodes_removed": removed, "skipped": skipped,
            "manifest_path": manifest_path, "embedded": bool(embedder)}


def watch_paths(kg, root: str, embedder=None, manifest_path: Optional[str] = None,
                interval: float = 2.0, iterations: Optional[int] = None,
                on_change=None) -> int:
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
