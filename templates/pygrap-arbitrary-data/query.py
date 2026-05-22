#!/usr/bin/env python3
"""
Interactive hybrid querying with pygrap.

Demonstrates best-practice hybrid search (text + vector + graph).
"""

import os
from pathlib import Path

from sentence_transformers import SentenceTransformer
import pygrap as grap


GRAPH_DIR = Path(os.getenv("GRAP_DIR", "./graph"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")


def main():
    if not (GRAPH_DIR / "graph").exists():
        print(f"No graph found at {GRAPH_DIR}. Run ingest.py first.")
        return

    print(f"Loading graph from {GRAPH_DIR}...")
    g = grap.IndexedKnowledgeGraph.load_from_dir(GRAPH_DIR)

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    print("\nReady. Type your query (or 'quit' to exit).\n")

    while True:
        try:
            query = input("Query > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not query or query.lower() in {"quit", "exit", "q"}:
            break

        qvec = model.encode(query, normalize_embeddings=True).tolist()

        results = g.search_and_traverse_hybrid(
            text_query=query,
            vector=qvec,
            max_seeds=15,
            text_weight=0.35,
            vector_weight=0.45,
            graph_weight=0.20,
            min_strength=0.55,
            max_depth=3,
            max_results=12,
        )

        if not results:
            print("No results found.\n")
            continue

        print(f"\nTop {len(results)} results:\n")
        for i, r in enumerate(results, 1):
            snippet = r.text.replace("\n", " ")[:260]
            print(f"{i}. Fused={r.fused_score:.3f} | V={r.vector_score:.2f} | G={r.graph_strength:.2f}")
            print(f"   {snippet}...\n")

    print("Goodbye.")


if __name__ == "__main__":
    main()
