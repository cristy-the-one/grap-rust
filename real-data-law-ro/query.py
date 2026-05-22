#!/usr/bin/env python3
"""
Phase 0 - Simple query interface for the Romanian Legal Acts dataset.

Demonstrates basic hybrid search on the ingested legal graph.
"""

import os
from pathlib import Path

from sentence_transformers import SentenceTransformer
import pygrap as grap


GRAPH_DIR = Path(os.getenv("GRAP_DIR", Path(__file__).parent / "graph"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")


def main():
    if not (GRAPH_DIR / "graph").exists():
        print(f"No graph found at {GRAPH_DIR}. Run ingest.py first.")
        return

    print(f"Loading graph from {GRAPH_DIR}...")
    g = grap.IndexedKnowledgeGraph.load_from_dir(GRAPH_DIR)

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    print("\nRomanian Legal Acts Graph ready.")
    print("Example queries:")
    print("  - protecția mediului")
    print("  - impozit pe venit")
    print("  - Agenția Română pentru Evenimente Internaționale")
    print("\nType your query (or 'quit' to exit).\n")

    while True:
        try:
            query = input("Query > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not query or query.lower() in {"quit", "exit", "q"}:
            break

        qvec = model.encode(query, normalize_embeddings=True).tolist()

        # Basic hybrid search (Phase 0)
        results = g.search_and_traverse_hybrid(
            text_query=query,
            vector=qvec,
            max_seeds=10,
            text_weight=0.4,
            vector_weight=0.5,
            graph_weight=0.1,   # low in Phase 0 because we haven't built many edges yet
            min_strength=0.5,
            max_depth=2,
            max_results=10,
        )

        if not results:
            print("No results.\n")
            continue

        print(f"\nTop {len(results)} results:\n")
        for i, r in enumerate(results, 1):
            snippet = r.text.replace("\n", " ")[:220]
            print(f"{i}. Fused={r.fused_score:.3f} | V={r.vector_score:.2f}")
            print(f"   {snippet}...\n")

    print("Goodbye.")


if __name__ == "__main__":
    main()
