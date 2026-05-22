#!/usr/bin/env python3
"""
Extended query script with property-based filtering support.

Usage examples:
    python query.py
    python query.py --require topic=rag
    python query.py --require "topic=ai,confidence>0.7"
"""

import os
import argparse
from pathlib import Path
from typing import List, Tuple

from sentence_transformers import SentenceTransformer
import pygrap as grap


GRAPH_DIR = Path(os.getenv("GRAP_DIR", "./graph"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")


def parse_requirements(require_str: str) -> List[str]:
    """Very simple parser: 'topic=rag,confidence>0.7' -> ['topic', 'confidence']"""
    if not require_str:
        return []
    props = []
    for part in require_str.split(","):
        part = part.strip()
        if "=" in part:
            props.append(part.split("=")[0].strip())
        elif ">" in part or "<" in part:
            props.append(part.split(">")[0].split("<")[0].strip())
    return props


def main():
    parser = argparse.ArgumentParser(description="Hybrid query with property filtering")
    parser.add_argument("--require", type=str, default="", help="Comma-separated property requirements (e.g. topic=rag,confidence>0.7)")
    args = parser.parse_args()

    if not (GRAPH_DIR / "graph").exists():
        print(f"No graph found at {GRAPH_DIR}. Run ingest.py first.")
        return

    print(f"Loading graph from {GRAPH_DIR}...")
    g = grap.IndexedKnowledgeGraph.load_from_dir(GRAPH_DIR)

    print(f"Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)

    required_props = parse_requirements(args.require)
    if required_props:
        print(f"Requiring properties: {required_props}\n")

    print("Ready. Type your query (or 'quit' to exit).\n")

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
            required_properties=required_props,
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

        print(f"\nTop {len(results)} results (filtered by {required_props or 'none'}):\n")
        for i, r in enumerate(results, 1):
            snippet = r.text.replace("\n", " ")[:240]
            print(f"{i}. Fused={r.fused_score:.3f} | V={r.vector_score:.2f} | G={r.graph_strength:.2f}")
            print(f"   {snippet}...\n")

    print("Goodbye.")


if __name__ == "__main__":
    main()
