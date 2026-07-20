"""Ingest plain-text / markdown / JSON documents into a grap KG as real evidence nodes."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_EXTS = {".md", ".txt", ".markdown"}
# Evidence and extraction must see the SAME corpus. The KEE extraction side
# was law-JSON-aware while evidence ingestion silently skipped .json files,
# so the pilot scored gaps against an empty evidence index (law-ro pilot
# finding 13). Every ingest path now accepts this full set.
_ALL_EXTS = _EXTS | {".json"}


def _text_and_label_from_json(obj: Any, stem: str) -> Tuple[str, str]:
    """Extract (text, label) from a parsed JSON document.

    Law-aware (titlu + text_complet, the Romanian-legal-acts schema), with a
    generic fallback (common text fields, else all string values joined)."""
    if isinstance(obj, dict):
        label = (str(obj.get("id")).strip() if obj.get("id") not in (None, "") else stem)
        title = str(obj.get("titlu") or "").strip()
        body = str(obj.get("text_complet") or obj.get("text")
                   or obj.get("content") or obj.get("body") or "").strip()
        if title or body:
            return ((title + "\n\n" + body).strip(), label)
        parts = [v.strip() for v in obj.values() if isinstance(v, str) and v.strip()]
        return ("\n\n".join(parts), label)
    if isinstance(obj, list):
        return ("\n\n".join(_text_and_label_from_json(x, stem)[0] for x in obj), stem)
    return (str(obj or ""), stem)


def read_document(path: Path) -> Optional[str]:
    """Text of one evidence document, or None if unreadable.

    .json goes through the law-aware/generic extractor so evidence ingestion
    and KEE extraction chunk the same text for the same file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if path.suffix.lower() == ".json":
        try:
            text, _ = _text_and_label_from_json(json.loads(raw), path.stem)
            return text
        except ValueError:
            return None
    return raw


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
    """Chunk + add_node every .md/.txt/.json under `root` (file or dir). Returns counts.

    If `embedder` is supplied (a kee.embeddings.Embedder), each chunk is embedded
    and stored on its node, enabling vector / hybrid search and semantic dedup.
    """
    p = Path(root)
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in _ALL_EXTS)
    else:
        raise FileNotFoundError(f"ingest path not found: {root}")

    nodes_added = 0
    labels: List[str] = []
    for f in files:
        text = read_document(f)
        if text is None:
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
