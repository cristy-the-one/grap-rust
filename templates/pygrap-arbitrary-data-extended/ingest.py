#!/usr/bin/env python3
"""
Extended production-oriented ingestion with metadata/properties.

Supports YAML frontmatter in Markdown files.
Metadata is attached to edges between chunks of the same document,
enabling powerful property-based filtering during traversal.
"""

import os
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any

import yaml
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
    return hashlib.md5(f"{path.resolve()}::{stat.st_mtime_ns}".encode()).hexdigest()


def parse_frontmatter(text: str) -> tuple[str, Dict[str, Any]]:
    """Extract YAML frontmatter if present."""
    if text.startswith("---"):
        try:
            parts = text.split("---", 2)
            if len(parts) >= 3:
                metadata = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
                return body, metadata
        except Exception:
            pass
    return text, {}


def chunk_text(text: str, max_chars: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
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
    return json.loads(state_file.read_text()) if state_file.exists() else {}


def save_ingested_state(state: Dict[str, str]):
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    (GRAPH_DIR / "ingested.json").write_text(json.dumps(state, indent=2))


def main():
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    ingested = load_ingested_state()

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    print("Opening/creating graph (with merge policy)...")
    g = grap.IndexedKnowledgeGraph.with_merge_policy(str(GRAPH_DIR), 4)

    # Register common property tags for filtering
    topic_tag = g.graph.properties.register("topic")
    source_tag = g.graph.properties.register("source")
    confidence_tag = g.graph.properties.register("confidence")

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
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"  Skipping {path} (read error: {e})")
            continue

        body, metadata = parse_frontmatter(raw_text)
        chunks = chunk_text(body)
        if not chunks:
            continue

        print(f"Processing {path.relative_to(DOCS_DIR)} ({len(chunks)} chunks)")

        node_ids = []
        for i, chunk in enumerate(chunks):
            label = f"{path.relative_to(DOCS_DIR)}::chunk_{i}"
            embedding = model.encode(chunk, normalize_embeddings=True).tolist()
            nid = g.add_node(text=chunk, label=label, embedding=embedding)
            node_ids.append(nid)

        # Create edges between chunks of the same document and attach metadata
        for i in range(len(node_ids) - 1):
            props = []
            if "topic" in metadata:
                props.append((topic_tag, str(metadata["topic"])))
            if "source" in metadata:
                props.append((source_tag, str(metadata["source"])))
            if "confidence" in metadata:
                try:
                    props.append((confidence_tag, float(metadata["confidence"])))
                except (ValueError, TypeError):
                    pass

            # Convert list of (tag, value) tuples into a simple dict for the current pygrap binding
            props_dict = {name: val for name, val in props} if props else None
            g.add_edge(node_ids[i], node_ids[i + 1], 0.85, props_dict)

        ingested[file_id] = file_hash
        processed += 1
        needs_save = True

        if processed % BATCH_SAVE_EVERY == 0:
            print(f"  → Checkpoint save...")
            g.save_to_dir(GRAPH_DIR)
            save_ingested_state(ingested)

    if needs_save:
        print("Final save...")
        g.save_to_dir(GRAPH_DIR)
        save_ingested_state(ingested)

    print(f"\nIngestion complete. Processed {processed} files.")


if __name__ == "__main__":
    main()
