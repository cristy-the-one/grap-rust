#!/usr/bin/env python3
"""
Production-oriented ingestion script for pygrap.

Features:
- Recursive document walking
- Paragraph-aware chunking with overlap
- Idempotency via file hash tracking
- Embeddings via sentence-transformers
- Periodic safe saves during long runs

Note: This basic template creates *nodes only* — no edges between chunks.
The graph_weight in query.py contributes 0 because there are no traversable edges.
For a richer graph structure (edges + property-based filtering), see the extended
template in ../pygrap-arbitrary-data-extended/.
"""

import os
import json
import hashlib
from pathlib import Path
from typing import List, Dict

from sentence_transformers import SentenceTransformer
import pygrap as grap


# ====================== CONFIG ======================
GRAPH_DIR = Path(os.getenv("GRAP_DIR", "./graph"))
DOCS_DIR = Path(os.getenv("DOCS_DIR", "./documents"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

CHUNK_SIZE = 650
CHUNK_OVERLAP = 80
BATCH_SAVE_EVERY = 150
# ====================================================


def get_file_hash(path: Path) -> str:
    stat = path.stat()
    content = f"{path.resolve()}::{stat.st_mtime_ns}".encode()
    return hashlib.md5(content).hexdigest()


def chunk_text(text: str, max_chars: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Simple but effective paragraph-aware chunker."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                chunks.append(current)
            current = para[-overlap:] + "\n\n" + para if len(para) > overlap else para

    if current:
        chunks.append(current)
    return chunks


def load_ingested_state() -> Dict[str, str]:
    state_file = GRAPH_DIR / "ingested.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return {}


def save_ingested_state(state: Dict[str, str]):
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    (GRAPH_DIR / "ingested.json").write_text(json.dumps(state, indent=2))


def main():
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    ingested = load_ingested_state()

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    if (GRAPH_DIR / "graph").exists():
        print("Loading existing graph...")
        g = grap.IndexedKnowledgeGraph.load_from_dir(GRAPH_DIR)
    else:
        print("Creating new on-disk graph...")
        g = grap.IndexedKnowledgeGraph.new_on_disk_with_merge_policy(4)

    docs = [p for p in DOCS_DIR.rglob("*") if p.suffix.lower() in {".md", ".txt", ".rst"}]
    print(f"Found {len(docs)} documents.\n")

    processed = 0
    needs_save = False

    for path in docs:
        file_id = str(path.resolve())
        file_hash = get_file_hash(path)

        if ingested.get(file_id) == file_hash:
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"  Skipping {path} (read error: {e})")
            continue

        chunks = chunk_text(text)
        if not chunks:
            continue

        print(f"Processing {path.relative_to(DOCS_DIR)} ({len(chunks)} chunks)")

        for i, chunk in enumerate(chunks):
            label = f"{path.relative_to(DOCS_DIR)}::chunk_{i}"
            embedding = model.encode(chunk, normalize_embeddings=True).tolist()
            g.add_node(text=chunk, label=label, embedding=embedding)

        ingested[file_id] = file_hash
        processed += 1
        needs_save = True

        if processed % BATCH_SAVE_EVERY == 0:
            print(f"  → Checkpoint save ({processed} files)...")
            g.save_to_dir(GRAPH_DIR)
            save_ingested_state(ingested)

    if needs_save:
        print("Final save...")
        g.save_to_dir(GRAPH_DIR)
        save_ingested_state(ingested)

    print(f"\nIngestion complete. Processed {processed} new/updated files.")


if __name__ == "__main__":
    main()
