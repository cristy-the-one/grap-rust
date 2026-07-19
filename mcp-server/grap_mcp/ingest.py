"""Ingest plain-text / markdown documents into a grap KG as real evidence nodes."""

from pathlib import Path
from typing import Any, Dict, List

_EXTS = {".md", ".txt", ".markdown"}


def chunk_text(text: str, max_chars: int = 800) -> List[str]:
    """Split text into chunks on blank lines, capping each at max_chars."""
    chunks: List[str] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        while len(block) > max_chars:
            cut = block.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            chunks.append(block[:cut].strip())
            block = block[cut:].strip()
        if block:
            chunks.append(block)
    return chunks


def ingest_paths(kg, root: str, embedder=None) -> Dict[str, Any]:
    """Chunk + add_node every .md/.txt under `root` (file or dir). Returns counts.

    If `embedder` is supplied (a kee.embeddings.Embedder), each chunk is embedded
    and stored on its node, enabling vector / hybrid search and semantic dedup.
    """
    p = Path(root)
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in _EXTS)
    else:
        raise FileNotFoundError(f"ingest path not found: {root}")

    nodes_added = 0
    labels: List[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        chunks = chunk_text(text)
        embeddings = embedder.embed(chunks) if (embedder and chunks) else None
        for i, chunk in enumerate(chunks):
            label = f"doc:{f.stem}#{i}"
            if embeddings is not None:
                kg.add_node(text=chunk, label=label, embedding=embeddings[i])
            else:
                kg.add_node(text=chunk, label=label)
            nodes_added += 1
            if len(labels) < 5:
                labels.append(label)
    return {"files": len(files), "nodes_added": nodes_added, "labels": labels,
            "embedded": bool(embedder)}
