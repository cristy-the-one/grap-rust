#!/usr/bin/env python3
"""
Phase 1 - Three-mode interactive query interface for the Romanian Legal Acts graph.

Modes:
  <text>          Hybrid search (text + vector + graph)
  chain <id>      Citation chain from node (traverse_by_relation)
  cited_by <id>   Who cites this node (predecessors_by_relation)
  text <id>       Show full text of node
  quit            Exit
"""

import os
from pathlib import Path

from sentence_transformers import SentenceTransformer
import pygrap

GRAPH_DIR = Path(os.getenv("GRAP_DIR", Path(__file__).parent / "graph"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

HELP = """\
Commands:
  <text>          Hybrid search
  chain <id>      Citation chain from node
  cited_by <id>   Who cites this node
  text <id>       Show full text of node
  quit            Exit
"""


def load_graph() -> pygrap.IndexedKnowledgeGraph:
    if not GRAPH_DIR.exists():
        raise FileNotFoundError(
            f"No graph found at {GRAPH_DIR}. Run ingest.py first."
        )
    print(f"Loading graph from {GRAPH_DIR}...")
    return pygrap.IndexedKnowledgeGraph.with_merge_policy(str(GRAPH_DIR), 4)


def show_node_snippet(g: pygrap.IndexedKnowledgeGraph, node_id: int, width: int = 120) -> str:
    try:
        return g.get_node_text(node_id).replace("\n", " ")[:width]
    except Exception:
        return "<text unavailable>"


def cmd_hybrid_search(
    g: pygrap.IndexedKnowledgeGraph,
    model: SentenceTransformer,
    query: str,
) -> None:
    qvec = model.encode(query, normalize_embeddings=True).tolist()
    results = g.search_and_traverse_hybrid(
        query,
        vector=qvec,
        text_weight=0.4,
        vector_weight=0.4,
        graph_weight=0.2,
        traversal_weight_property="confidence",
        min_strength=0.3,
        max_depth=3,
        max_results=10,
    )
    if not results:
        print("No results.\n")
        return

    print(f"\nTop {len(results)} results:\n")
    for i, r in enumerate(results, 1):
        snippet = r.text.replace("\n", " ")[:220]
        print(f"{i}. node={r.node} | fused={r.fused_score:.3f} | vec={r.vector_score:.2f} | depth={r.depth}")
        print(f"   {snippet}...\n")

    # Offer to expand a result's citation chain
    try:
        choice = input("Enter node_id to expand citation chain (or Enter to continue): ").strip()
        if choice:
            cmd_chain(g, int(choice))
    except (ValueError, EOFError):
        pass
    print()


def cmd_chain(g: pygrap.IndexedKnowledgeGraph, node_id: int) -> None:
    results = g.traverse_by_relation(node_id, "relation_type", "cites", depth=4, max_results=20)
    if not results:
        print(f"  No outgoing citations from node {node_id}.\n")
        return
    print(f"\nCitation chain from node {node_id} ({len(results)} results):\n")
    for r in results:
        snippet = show_node_snippet(g, r.node_id)
        print(f"  depth={r.depth} | node={r.node_id} | strength={r.strength:.3f}")
        print(f"    {snippet}...\n")


def cmd_cited_by(g: pygrap.IndexedKnowledgeGraph, node_id: int) -> None:
    preds = g.predecessors_by_relation(node_id, "relation_type", "cites")
    if not preds:
        print(f"  No acts cite node {node_id}.\n")
        return
    print(f"\nActs citing node {node_id} ({len(preds)} results):\n")
    for pred_id, strength in preds:
        snippet = show_node_snippet(g, pred_id)
        print(f"  node={pred_id} | strength={strength:.3f}")
        print(f"    {snippet}...\n")


def cmd_text(g: pygrap.IndexedKnowledgeGraph, node_id: int) -> None:
    try:
        text = g.get_node_text(node_id)
        print(f"\n--- Full text of node {node_id} ---\n")
        print(text)
        print(f"\n--- End of node {node_id} ---\n")
    except Exception as e:
        print(f"  Error: {e}\n")


def main():
    g = load_graph()
    print(f"Graph loaded: {g}")

    print(f"Loading embedding model: {EMBEDDING_MODEL}...")
    model = SentenceTransformer(EMBEDDING_MODEL)

    print("\nRomanian Legal Acts Graph (Phase 1) ready.")
    print(HELP)

    while True:
        try:
            line = input("Query > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not line:
            continue

        lower = line.lower()

        if lower in {"quit", "exit", "q"}:
            break

        if lower.startswith("chain "):
            parts = line.split(None, 1)
            try:
                cmd_chain(g, int(parts[1]))
            except (ValueError, IndexError):
                print("  Usage: chain <node_id>\n")
            continue

        if lower.startswith("cited_by "):
            parts = line.split(None, 1)
            try:
                cmd_cited_by(g, int(parts[1]))
            except (ValueError, IndexError):
                print("  Usage: cited_by <node_id>\n")
            continue

        if lower.startswith("text "):
            parts = line.split(None, 1)
            try:
                cmd_text(g, int(parts[1]))
            except (ValueError, IndexError):
                print("  Usage: text <node_id>\n")
            continue

        # Default: hybrid search
        cmd_hybrid_search(g, model, line)

    print("Goodbye.")


if __name__ == "__main__":
    main()
