# Romanian Legal Acts - Citation Network Demo (Phase 1)

This folder contains a real-world demonstration of building and querying a **citation network** over ~2,100 Romanian legal acts (Hotărâri, Legi, Ordonanțe, Decizii, etc.) using `pygrap`.

## Dataset
- Source: legislație.just.ro (scraped)
- Format: One JSON file per legal act
- Size: ~2,092 documents
- Rich metadata (`tip_act`, `numar`, `data_vigoare`, `emitent`, `titlu`, `text_complet`, `in_vigoare`, ...)

## What This Demo Shows

**Phase 1** implements a two-pass citation network:

1. **Pass 1** — Ingest every legal act as a node with:
   - Full text (prefixed with normalized metadata)
   - `sentence-transformers` embeddings
   - Labels of the form `act-<id>`

2. **Pass 2** — Extract directed "cites" relationships using a high-precision regex for Romanian legal citations (e.g. `Legea nr. 166 din 1999`, `Hotărârea nr. 671/2000`, etc.). Edges carry properties:
   ```python
   {"relation_type": "cites", "confidence": 0.8}
   ```

The resulting graph lets you answer questions like:
- "What does this act cite?"
- "Who cites this act?"
- "Show the full citation chain forward/backward"
- Hybrid semantic + structural search over the legal corpus

## Key pygrap Features Exercised

- `IndexedKnowledgeGraph.with_merge_policy(path, n)`
- `add_node(text=..., label=..., embedding=[...])`
- `add_edge(src, dst, strength, {"relation_type": "...", "confidence": 0.8})`
- `search_and_traverse_hybrid(..., traversal_weight_property="confidence")`
- `traverse_by_relation(node, "relation_type", "cites", depth=...)`
- `predecessors_by_relation(node, "relation_type", "cites")`
- `get_node_text(node_id)`
- Persistent on-disk storage + vector index

## Quick Start

```bash
# 1. Make sure pygrap is built from the local source (see crates/pygrap/README.md)
pip install -r requirements.txt

# 2. Run the two-pass ingestion (creates ./graph with nodes + citation edges)
python ingest.py

# 3. Launch the interactive query interface
python query.py
```

In the query REPL you can use:

- `<any text>` — Hybrid semantic + citation search
- `chain <node_id>` — Outgoing citation chain (uses `traverse_by_relation`)
- `cited_by <node_id>` — Incoming citations (`predecessors_by_relation`)
- `text <node_id>` — Print full stored text of a node
- `quit`

## Current Status & Limitations

- Regex-based extraction (high precision on standard forms, misses some complex or abbreviated references).
- One node per full act (no article-level chunking yet).
- Thousands of unresolved references (acts outside the scraped set, or non-standard phrasing).
- No rich edge properties beyond `relation_type` + `confidence` yet.

## Planned Next Steps (Phase 2+)

- Higher-recall reference extraction (LLM-assisted pass + better regex)
- Article/section chunking with proper intra-document + inter-document edges
- Richer edge properties (e.g. `article`, `amends`, `repeals`)
- Property-based filtering during traversal

## Files

- `ingest.py` — Two-pass citation network builder
- `query.py` — Multi-mode interactive explorer (hybrid + graph traversal)
- `requirements.txt`
- `*.json` — The raw Romanian legal acts dataset

See also:
- `docs/real-data-law-ro-approach.md` (original proposal)
- The `pygrap-arbitrary-data-extended` template for a more general starting point
