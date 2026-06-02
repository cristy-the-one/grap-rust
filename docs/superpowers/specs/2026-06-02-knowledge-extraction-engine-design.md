# Knowledge Extraction Engine (KEE) — Design Spec

**Date:** 2026-06-02
**Status:** approved
**Implements:** `docs/knowledge-extraction-engine.md` (direction note)

## Goal

Turn documents into a *proposed* ASPICE-style ontology automatically, while
keeping the system's deterministic, auditable governance: **LLM proposes →
grap embeds/dedups/links/versions → human promotes**. Provider-agnostic (no
hardwired vendor). Also a deliberate stress test of grap-core / pygrap / MCP to
surface latent bugs.

## Non-negotiables

- **Provider-agnostic LLM.** Target the OpenAI-compatible `/chat/completions`
  HTTP API (Ollama, vLLM, llama.cpp server, hosted APIs all speak it). Stdlib
  `urllib` only — no vendor SDK dependency. A deterministic `StubProvider`
  backs offline/test runs.
- **Determinism of governance.** LLM output is *only* a proposal: a versioned,
  audited artifact promoted through the existing publish→promote→activate gate.
  Nothing reaches the production ontology without human action.
- **Offline/reproducible tests.** `StubProvider` + `HashingEmbedder` run the
  whole pipeline deterministically with no network.

## Substrate check (done)

pygrap `add_node(embedding=…)`, `vector_search`, `search_and_traverse_hybrid`
all work and **embeddings persist/round-trip** through save/reload. The vector
backbone is usable as-is.

## Components — `mcp-server/grap_mcp/kee/`

### Phase 1 — Embeddings foundation (`embeddings.py`)
- `Embedder` protocol: `embed(texts: list[str]) -> list[list[float]]`, `dim`.
- `HashingEmbedder(dim=256)`: deterministic, offline. Hash tokens → bucket →
  L2-normalized vector. Used for tests and as fallback.
- `LocalEmbedder(model=env GRAP_EMBED_MODEL or "all-MiniLM-L6-v2")`: lazy
  sentence-transformers; encodes + normalizes.
- `get_embedder()`: try `LocalEmbedder`; on import/load failure, warn and return
  `HashingEmbedder` (chosen default: try-local-fallback-hashing).
- Wire into `ingest.ingest_paths(kg, root, embedder=None)`: when an embedder is
  supplied, compute per-chunk embeddings and pass to `add_node(embedding=…)`.
  Backward compatible (embedder optional).

### Phase 2 — LLM provider (`llm.py`)
- `LLMProvider` protocol: `complete(system: str, user: str) -> str`.
- `OpenAICompatProvider(base_url, model, api_key)`: stdlib `urllib` POST to
  `{base_url}/chat/completions`. Config via `GRAP_LLM_BASE_URL`
  (default `http://localhost:11434/v1`), `GRAP_LLM_MODEL`, `GRAP_LLM_API_KEY`.
- `StubProvider(responses)`: returns canned JSON for deterministic tests.
- `get_provider()`: `StubProvider` unless a base_url is reachable/configured;
  the demo defaults to Ollama and reports a clear error if unreachable.

### Phase 3 — Extractor (`extract.py`)
- `extract_fragments(chunks, provider) -> list[Fragment]`.
- Prompts the model to emit **strict JSON** constrained to our schema:
  `{outcomes:[{id?,name,characteristics:[...] }], relations:[...]}`.
- Robust parse: extract JSON block, `json.loads`, validate shape, drop
  malformed; every fragment records `source` (chunk label) for provenance.
- The LLM never sees grap internals — only text in, JSON out.

### Phase 4 — Reconcile (`reconcile.py`)
- Embed candidate concept labels; cluster near-duplicates by cosine similarity
  (threshold `GRAP_KEE_SIM` default 0.86) using numpy (and exercising pygrap
  `vector_search`/`merge_nodes` where candidates are materialized as nodes).
- Produce a canonical, deduped candidate ontology dict (tool schema:
  `process_outcomes`, `mapping_hints`).

### Phase 5 — Governance / MCP surface
- New tool `extract_ontology(docs_path)` (operator-gated): ingest → extract →
  reconcile → write a **candidate** ontology and version it as a `proposed`
  artifact in the Workspace (reuse `publish_ontology` + audit `ontology_proposed`).
- Reuse existing `promote`/`activate` to gate it into production.
- `get_status` / a `list_candidates` view surfaces proposed ontologies.

### Phase 6 — Validate + demo + GUI
- `validate_candidate(ontology)`: no duplicate ids, no empty characteristics,
  every relation references known concepts; returns errors/warnings.
- GUI: a "Extract ontology from documents" action in the Knowledge panel that
  calls `extract_ontology` and renders the proposed outcomes + a promote button.
- `mcp-server/demo_kee.py`: end-to-end run on a small corpus.

## Data shapes

```
Fragment   = {"source": str, "outcomes": [Outcome], "relations": [Relation]}
Outcome    = {"name": str, "characteristics": [str]}
Relation   = {"from": str, "type": str, "to": str}
Candidate  = {"version": str, "process_outcomes": [...], "mapping_hints": [...],
              "provenance": {concept_id: [source, ...]}}
```

## Testing (deterministic, offline)

- `test_kee_embeddings.py`: hashing determinism + dimension; ingest-with-embedder
  populates vectors (real pygrap `vector_search` round-trip).
- `test_kee_llm.py`: `StubProvider` contract; `OpenAICompatProvider` request
  shaping via a fake urlopen (no network).
- `test_kee_extract.py`: stub JSON → fragments; malformed JSON dropped;
  provenance recorded.
- `test_kee_reconcile.py`: near-duplicate concepts merge; distinct ones don't.
- `test_kee_pipeline.py`: end-to-end stub+hashing → proposed candidate ontology,
  validated, loadable by `load_ontology`.

## Bug-hunt log

Track any grap-core/pygrap/MCP issues found while wiring embeddings, vector
search, `merge_nodes`, and candidate versioning. Fix small ones inline; flag
larger ones.

## Out of scope (v1)

- Capability-level (PA/GP) extraction.
- Multi-document cross-linking beyond concept dedup.
- Fine-tuned/optimized prompts per domain (a generic IE prompt for v1).
