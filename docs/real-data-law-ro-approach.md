# Approach for Ingesting Romanian Legal Data (`real-data-law-ro`) into grap

**Date**: 2026-05  
**Status**: Proposal

---

## 1. Executive Summary

The `real-data-law-ro` dataset contains approximately **2,100 Romanian legal acts** (Hotărâri, Legi, Ordonanțe, Decizii, etc.) with rich structured metadata and full text.

This is an **excellent real-world use case** for `grap` because:

- Legal acts have strong, queryable **properties** (`emitent`, `data_vigoare`, `tip_act`, `in_vigoare`).
- There are natural **graph relationships** (references, amendments, repeals).
- Full text benefits from both **semantic (vector)** and **precise (text + property)** retrieval.

**Recommended approach**: Start with **Python + `pygrap`** using the existing `pygrap-arbitrary-data-extended` template as the foundation. This gives the fastest path to a working prototype while fully leveraging the engine's current strengths (hybrid search, property filtering, vector embeddings, persistence).

Native Rust can be considered later for ingestion performance if the corpus grows significantly.

---

## 2. Data Overview

### Key Statistics (from sampling)

- ~2,092 JSON files
- Dominant document types: `HOTĂRÂRE` (~53%), `DECIZIE`, `LEGE`, `ORDONANȚĂ DE URGENȚĂ`
- Main issuers: `Guvernul`, `Parlamentul`, `Curtea Constituțională`
- Average text length: ~11k characters (median ~3.2k) — very manageable
- ~63% of documents contain explicit references to other legal acts (strong graph signal)
- Text quality is generally good (only a small percentage still say "Act în curs de procesare")

### JSON Structure (typical)

```json
{
  "id": "...",
  "link": "...",
  "tip_act": "HOTĂRÂRE",
  "numar": "958",
  "data_vigoare": "1999-01-30",
  "emitent": "Guvernul",
  "titlu": "...",
  "text_complet": "... full legal text ...",
  "in_vigoare": true,
  ...
}
```

---

## 3. Recommended Technology Approach

**Start with Python (`pygrap`)**

### Why Python first?

- We already have mature, production-oriented templates (`pygrap-arbitrary-data-extended`) that handle:
  - Idempotent ingestion
  - Chunking
  - YAML-style metadata (easily adaptable to JSON fields)
  - Property attachment to edges
  - Hybrid search with `required_properties`
- Fast iteration is critical when experimenting with legal reference extraction.
- The current dataset size (~2k documents) makes Python performance perfectly adequate.
- Easy to integrate with embedding models and (optionally) LLMs for relationship extraction.

**Rust path**: Only consider if/when you move to a much larger corpus (tens or hundreds of thousands of acts) and need maximum ingestion speed. Even then, you could keep orchestration in Python and only move hot paths to Rust.

---

## 4. Proposed Data Model

### Core Entity: Legal Act (Node)

One node per JSON file.

**Recommended properties** (use the engine's property system):

- `id` (unique)
- `tip_act` (HOTĂRÂRE, LEGE, etc.)
- `numar`
- `data_vigoare` (effective date)
- `emitent`
- `in_vigoare` (boolean or status)
- `titlu`
- `text_complet` (or chunks)
- `publicatie`
- Optional: `tags` / `domain` (if derivable)

### Relationships (Edges)

This is where `grap` adds the most value:

- `references` — Act A references Act B (very common in legal text)
- `amends` / `modifies`
- `repeals` / `abrogates`
- `implements` (for EU directives, for example)

**Extraction strategy** (see section 6):

1. Rule-based (high precision, fast): regex for standardized Romanian legal citations (`Legea nr. \d+/\d{4}`, `Hotărârea nr. ...`, `art. X din Legea...`)
2. Optional LLM pass for higher recall on complex references.

### Document Structure (optional but powerful)

- Create a "document root" node for each act.
- Link individual chunks to the root with edges carrying the act's metadata as properties.
- This enables very effective `required_properties` filtering during traversal.

---

## 5. Ingestion Pipeline Design

**Recommended base**: Extend `templates/pygrap-arbitrary-data-extended/`

### Pipeline Steps

1. **Load JSON** files from `real-data-law-ro/`
2. **Parse metadata** from the JSON fields (much cleaner than scraping HTML)
3. **Chunk `text_complet`** (respect articles/sections where possible)
4. **Generate embeddings** on title + chunks (use a good Romanian or multilingual model)
5. **Create nodes** with rich properties
6. **Extract and create relationships** between acts (the graph edges)
7. **Persist** using the existing on-disk + vector index system

**Idempotency**: Use the `id` field from the JSON as the primary key (store in label or a dedicated property).

**Chunking recommendation**: Start with the existing paragraph-aware chunker, then improve to respect legal article boundaries (`Articolul 1`, `Art. 12`, etc.) if needed.

---

## 6. Relationship Extraction Strategy

This is the most valuable (and hardest) part.

### Phase 1 (Quick value)
- High-precision regex for common citation patterns:
  - `Legea nr. \d+/\d{4}`
  - `Hotărârea (Guvernului|nr\.) \d+/\d{4}`
  - `Ordonanța de urgență nr. ...`
  - `art\. \d+ .* Legea nr\.`

### Phase 2 (Higher recall)
- Use a small LLM (or fine-tuned model) to extract `(source_act, target_act, relation_type)` triples from the legal text.

Even a good rule-based extractor will already create a very useful graph because Romanian legal drafting is highly formulaic.

---

## 7. Query Patterns & Value Demonstration

This dataset shines when combining the three signals:

**Examples**:

- Semantic + property filter:  
  `"protecția mediului"` + `emitent=Guvernul` + `data_vigoare > 2020`

- Graph + semantic:  
  Find acts that *reference* a specific environmental law and are semantically similar to a query.

- Validity + graph:  
  "Show me all acts that amend Law X and are still in force."

These kinds of queries are extremely difficult with pure vector DBs or pure relational databases, but natural in `grap`.

---

## 8. Risks & Mitigations

| Risk                              | Mitigation |
|-----------------------------------|----------|
| Incomplete texts ("Act în curs de procesare") | Filter them out or mark with a property |
| Romanian legal language complexity | Start with high-precision rules; add LLM later |
| Reference resolution (which exact act is being referenced) | Use `numar` + `data_vigoare` + `tip_act` when possible |
| Scale (if corpus grows 10x–100x) | Use `ShardedGraph` + HNSW vector index |
| Embedding quality in Romanian     | Test multiple models (`bge-m3`, `nomic`, `intfloat/e5` multilingual) |

---

## 9. Recommended Phased Plan

**Phase 0 – Prototype (1–2 weeks)**
- Use/extend the `pygrap-arbitrary-data-extended` template
- Ingest all JSONs as nodes with properties
- Basic rule-based reference extraction
- Demonstrate hybrid search + property filtering

**Phase 1 – Graph Enrichment**
- Improve relationship extraction
- Add document root + chunk linking with metadata on edges
- Evaluate real legal research queries

**Phase 2 – Production Hardening**
- Add background workers
- Consider sharding if volume grows
- Add monitoring / `stats()` dashboards
- Optional: expose via simple API

**Phase 3 (optional)**
- Native Rust ingestion pipeline for higher throughput
- Advanced relationship extraction with LLMs

---

## 10. Final Recommendation

**Start immediately with Python + the extended template.**

This gives you the fastest path to a working, valuable system on real legal data while fully exercising the engine's current strengths (properties + hybrid retrieval + graph + vectors).

The dataset is small enough that you can iterate quickly and prove the value before investing in a native Rust ingestion pipeline.

Would you like me to:

- Start building the Phase 0 prototype (adapting the extended template for this JSON dataset)?
- First write a more detailed technical design document?
- Or focus on the relationship extraction strategy first?

I'm ready to move on whichever part you prefer.