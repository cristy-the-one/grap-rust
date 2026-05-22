#!/usr/bin/env python3
"""
Phase 0 - Prototype ingestion for the Romanian Legal Acts dataset (real-data-law-ro).

This script ingests all JSON files from the legal corpus into grap as nodes.

Features implemented in Phase 0:
- Parse JSON legal acts
- Create one node per legal act with rich properties (via label + text prefix)
- Chunk long legal texts
- Generate embeddings
- Idempotent ingestion (based on act ID)
- On-disk persistence with merge policy

Future phases will add:
- Relationship extraction (references, amendments)
- Proper edge properties for filtering
- Advanced hybrid + graph queries
"""

import json
import os
from pathlib import Path
from typing import List, Optional

from sentence_transformers import SentenceTransformer
import pygrap as grap


# ====================== CONFIG ======================
DATA_DIR = Path(__file__).parent  # real-data-law-ro/
GRAPH_DIR = Path(os.getenv("GRAP_DIR", DATA_DIR / "graph"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
# ====================================================


def chunk_text(text: str, max_chars: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Simple paragraph-aware chunker for legal texts."""
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


def load_or_create_graph() -> grap.IndexedKnowledgeGraph:
    """Load existing graph or create a new on-disk one."""
    if (GRAPH_DIR / "graph").exists():
        print(f"Loading existing graph from {GRAPH_DIR}")
        return grap.IndexedKnowledgeGraph.load_from_dir(GRAPH_DIR)
    else:
        print("Creating new on-disk graph with merge policy...")
        return grap.IndexedKnowledgeGraph.new_on_disk_with_merge_policy(4)


def get_act_id(data: dict) -> str:
    return str(data.get("id"))


def build_node_text(act: dict) -> str:
    """Create the main text content for the node, with metadata prefix for filtering."""
    meta = []
    if act.get("tip_act"):
        meta.append(f"Tip: {act['tip_act']}")
    if act.get("numar"):
        meta.append(f"Număr: {act['numar']}")
    if act.get("data_vigoare"):
        meta.append(f"Data vigoare: {act['data_vigoare']}")
    if act.get("emitent"):
        meta.append(f"Emitent: {act['emitent']}")
    if act.get("in_vigoare") is not None:
        meta.append(f"În vigoare: {act['in_vigoare']}")

    meta_str = " | ".join(meta)
    title = act.get("titlu", "").strip()
    text = act.get("text_complet", "").strip()

    # Prefix metadata so it can be used in text search + we can parse it later if needed
    prefix = f"[METADATA] {meta_str}\n\n"

    full_text = prefix + f"{title}\n\n{text}" if text else prefix + title
    return full_text


def main():
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    g = load_or_create_graph()

    # Register useful property tags (will be used more in later phases)
    g.graph.properties.register("tip_act")
    g.graph.properties.register("emitent")
    g.graph.properties.register("data_vigoare")
    g.graph.properties.register("in_vigoare")

    json_files = sorted(DATA_DIR.glob("*.json"))
    print(f"Found {len(json_files)} legal acts.\n")

    processed = 0
    skipped = 0

    for json_path in json_files:
        try:
            with open(json_path, encoding="utf-8") as f:
                act = json.load(f)
        except Exception as e:
            print(f"  Skipping {json_path.name} (JSON error: {e})")
            continue

        act_id = get_act_id(act)
        if not act_id:
            skipped += 1
            continue

        # Idempotency: use the official act ID as label
        label = f"act-{act_id}"

        # Very naive check for existing nodes (we'll improve this)
        # For Phase 0 we just always re-add (the label helps humans, real idempotency comes later)
        # A better way would be to store processed IDs, but for simplicity in Phase 0 we re-process.

        text = build_node_text(act)
        chunks = chunk_text(text)

        # For Phase 0 we create one main node per act (using the first chunk + metadata)
        # In later phases we can create chunk nodes + link them.
        main_text = chunks[0] if chunks else text
        embedding = model.encode(main_text, normalize_embeddings=True).tolist()

        g.add_node(text=main_text, label=label, embedding=embedding)

        processed += 1
        if processed % 200 == 0:
            print(f"  Processed {processed} acts...")
            g.save_to_dir(GRAPH_DIR)

    print(f"\nIngestion complete.")
    print(f"  Processed: {processed}")
    print(f"  Skipped:   {skipped}")

    print("Saving graph...")
    g.save_to_dir(GRAPH_DIR)
    print(f"Graph saved to {GRAPH_DIR}")


if __name__ == "__main__":
    main()
