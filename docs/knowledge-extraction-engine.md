# Knowledge Extraction Engine — direction note

> September 2026: LLM extraction remains active. The [current implementation and
> local-model evaluation](llm-extraction-improvement-2026-09.md) cover source quotes,
> replacement on successful refresh, durable receipts, and relation-preserving
> candidate exports. The design discussion below includes aspirations beyond the
> implemented behavior.

> Status: **implemented (v1, assisted extraction).** The design discussion below
> has been built as `mcp-server/grap_mcp/kee/` (embeddings, llm, extract,
> reconcile, pipeline, validate), the `extract_ontology` MCP tool, a GUI hook in
> the Knowledge panel, and an end-to-end demo (`mcp-server/demo_kee.py`). Spec:
> `docs/superpowers/specs/2026-06-02-knowledge-extraction-engine-design.md`.
> The shape matches the "assisted extraction" recommendation: **LLM proposes →
> grap embeds/dedups/links/versions → human promotes.** Provider-agnostic
> (OpenAI-compatible HTTP: Ollama/vLLM/llama.cpp/hosted); offline-deterministic
> via a stub provider + hashing embedder.
>
> **Configuration (env):**
> - `GRAP_LLM_BASE_URL` (default `http://localhost:11434/v1`), `GRAP_LLM_MODEL`,
>   `GRAP_LLM_API_KEY`; `GRAP_LLM_STUB=1` forces the offline stub. The model is
>   also selectable per-call (`extract_ontology(model=…)`) / from the GUI.
> - `GRAP_EMBED_BACKEND`: `auto` (default — sentence-transformers, else hashing),
>   `ollama`/`http` (fully-local embeddings via Ollama, e.g. `nomic-embed-text`,
>   falls back to hashing if unreachable), `local`, or `hashing`.
>   `GRAP_EMBED_MODEL` names the model for the chosen backend;
>   `GRAP_EMBED_BASE_URL` overrides the HTTP endpoint.
>
> Original framing (kept for context): whether the grap + MCP system could
> extract an ontology from documents by itself. Separate from the ASPICE-from-PDF
> task, which is a deterministic parser for one known document format.

## The question

Can this system act as a *knowledge extraction engine* — read documents and
produce the ontology (concepts, relations, characteristics) on its own, rather
than us hand-authoring it?

## Short answer

Feasible, and the architecture is a good *substrate* for it — but it needs one
component we have not built. grap is the store/retrieve/dedup/version/govern
layer; the inductive "read prose → propose structure" step is missing, and our
reasoner is deliberately lexical (BM25 token coverage), not semantic
comprehension.

## Two mechanisms behind "extract by itself"

1. **Structural parsing** — regex/grammar tuned to one document's layout (e.g.
   the ASPICE PAM's `SYS.5.BP5` IDs and `Output WP NN-NN` patterns). Precise and
   fully auditable, but hand-fit per format; does not generalize.
2. **Knowledge induction** — reading unstructured prose and *proposing* the
   entities, relations, and characteristics that should become the ontology.
   This is what "a knowledge extraction engine" means, and it is the part the
   system currently has no component for.

## What we have vs. what's missing

| Stage | Needed | Status |
|---|---|---|
| Ingest & chunk text | yes | have (`mcp-server/grap_mcp/ingest.py`) |
| **Propose structure from prose** | yes | **missing** (the extraction intelligence) |
| Dedup / reconcile candidates | yes | partial — content-addressing, `merge_nodes`, `vector_search` exist, but ingest does not yet compute embeddings |
| Store / link / version the result | yes | have (KG + Workspace: VersionStore, audit) |
| Validate & find gaps | yes | have, but reasoner is lexical, not semantic |
| Human curation & governance | yes | have (publish → promote → activate + audit) |

grap is well-suited to be the **backbone** (store, dedup, link, version, govern,
retrieve). The missing block is the inductive step.

## Realistic architecture (assisted extraction)

- **Extractor (new):** an LLM-driven information-extraction step that reads each
  chunk and emits *candidate* ontology fragments constrained to our schema
  (outcomes, characteristics, relations). This is the intelligence; grap does
  not provide it.
- **Reconcile (grap's strength):** compute embeddings on ingest, then
  cluster/merge near-duplicate candidates via vector similarity +
  content-addressing (so "test plan" / "test specification" do not proliferate).
- **Govern (already built):** each extraction run is a *versioned, audited
  artifact* in the Workspace — proposed → reviewed → promoted. Nothing reaches
  the production ontology without passing the lifecycle gate.
- **Validate (extend reasoner):** consistency/coverage checks before promotion.

## Core tension: determinism vs. induction

The system's ethos is deterministic, reproducible, auditable (frozen weights,
reconstructable runs, lexical matching). An LLM extractor is non-deterministic
and imperfect. These are reconcilable only with a specific shape:

- Treat LLM extraction as **proposals**, never ground truth — captured as
  versioned artifacts and gated by human review through the existing
  publish/promote machinery. The *governance* stays deterministic even when the
  *extractor* is not.
- Route by source type: for **structured standards** (ASPICE, ISO, numbered
  regulations) a deterministic parser is *better* than an LLM — more precise and
  fully auditable. For **unstructured domain knowledge**, LLM IE is the only
  realistic path. A mature engine uses both.

## Bottom line

"Fully autonomous, no oversight" is the wrong target for domains where
correctness matters (ASPICE, law). The credible version is **assisted
extraction**: LLM proposes → grap dedups/links/versions → human promotes.

The single most useful concrete first step is small and already half-supported:
**compute embeddings during ingest**, unlocking semantic dedup, candidate
clustering, and a semantic (not just lexical) reasoner.
