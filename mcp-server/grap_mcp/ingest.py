"""Ingest prose documents and reader-backed files into a grap KG as real evidence nodes."""

import json
from itertools import islice
from .native_graph import add_node_batch
from .readers.dbc import read_dbc
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_EXTS = {".md", ".txt", ".markdown"}
# Evidence and extraction must see the SAME corpus. The KEE extraction side
# was law-JSON-aware while evidence ingestion silently skipped .json files,
# so the pilot scored gaps against an empty evidence index (law-ro pilot
# finding 13). Every ingest path now accepts this full set.
# Structured suffixes dispatch to a reader. Prose stays on read_document.
_READERS = {
    ".dbc": read_dbc,
}
_ALL_EXTS = _EXTS | {".json"} | set(_READERS)


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


EMBED_BATCH = 128     # texts per /embeddings request; bounds payload size


def ingest_paths(kg, root: str, embedder=None, batch_size: int = EMBED_BATCH) -> Dict[str, Any]:
    """Add every .md/.txt/.json under `root`, and one node per reader item. Returns counts.

    If `embedder` is supplied (a kee.embeddings.Embedder), each chunk is embedded
    and stored on its node, enabling vector / hybrid search and semantic dedup.

    Embedding batches span FILES, not just the chunks of one file. A local
    /embeddings round-trip costs ~2.2s regardless of how few texts it carries,
    so embedding per file made a 200-document corpus pay ~200 round-trips for
    ~2 chunks each (~7 min of pure overhead). Batching across files in bounded
    groups collapses that to a handful of requests while keeping the payload
    (and peak memory) capped.
    """
    p = Path(root)
    if p.is_file():
        files = [p]
        corpus_root = p.parent
    elif p.is_dir():
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in _ALL_EXTS)
        corpus_root = p
    else:
        raise FileNotFoundError(f"ingest path not found: {root}")

    def records():
        for f in files:
            reader = _READERS.get(f.suffix.lower())
            if reader is not None:
                for i, item in enumerate(reader(f, corpus_root=corpus_root)):
                    yield (f"doc:{f.stem}#{i}", item.text)
                continue
            text = read_document(f)
            if text is None:
                continue
            for i, chunk in enumerate(chunk_text(text)):
                yield (f"doc:{f.stem}#{i}", chunk)

    nodes_added = 0
    labels: List[str] = []
    step = max(1, int(batch_size))
    pending = records()
    while group := list(islice(pending, step)):
        embeddings = embedder.embed([c for _, c in group]) if embedder else None
        if embeddings is not None and len(embeddings) != len(group):
            raise ValueError("embedding result count does not match the input batch")
        rows = [(chunk, label, embeddings[j] if embeddings is not None else None)
                for j, (label, chunk) in enumerate(group)]
        add_node_batch(kg, rows)
        nodes_added += len(rows)
        labels.extend(label for label, _ in group[:max(0, 5 - len(labels))])
    return {"files": len(files), "nodes_added": nodes_added, "labels": labels,
            "embedded": bool(embedder)}
