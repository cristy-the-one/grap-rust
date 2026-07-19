# KEE Workflow — improving the four graphs with grap-rust, grap-mcp, and local LLMs

This is the operating manual for the Knowledge Extraction Engine loop: what each
graph is, which tools improve it, where the LLM is allowed to act, and what stays
deterministic. Every command here is exercised by the test suite or `demo_kee.py`.

## The four graphs

| Graph | Lives in | Source of truth | Improved by |
|---|---|---|---|
| **Knowledge Graph** (evidence) | on-disk grap KG dir (Rust core) | ingested documents | `ingest_documents`, `reingest_documents`, `compact_graph`, `reindex_entities` |
| **Concept graph** (KEE working graph) | `<workdir>/graph.sqlite` + derived `<workdir>/graph` projection | extraction state (concepts, aliases, relations, sources, content hashes) | `extend_extraction_graph` (LLM), `open_extraction_graph`, `export_ontology` |
| **Procedure Graph** (process logic) | YAML + versioned Workspace records | authored PG files | `validate_procedure_graph`, `diff_procedure_graphs`, `publish_pg` → `promote_pg` → `activate` |
| **Trace / Temporal graph** (runs, events, explanations) | Workspace (Rust core) | every audited action | grows automatically; queried via `list_runs`, `query_events`, `reconstruct_run`; pruned by `enforce_retention` |

The division of labor: **the LLM only ever proposes** (ontology fragments from
document chunks). Everything that governs — dedup, versioning, hashes, review,
adoption, gap scoring, audit — is deterministic Rust/Python. Gap explanations are
persisted, versioned records and reproduce bit-for-bit for the same inputs.

## Local LLM setup (pick one)

All KEE tools resolve the model through one seam (`GRAP_LLM_BASE_URL` +
`GRAP_LLM_MODEL`, OpenAI-compatible `/chat/completions`).

```powershell
# Ollama (default): base URL already http://localhost:11434/v1
$env:GRAP_LLM_MODEL = "llama3.1:8b"

# Bonsai (llama.cpp llama-server from D:\projects\Bonsai-demo):
#   .\scripts\start_llama_server.ps1   → OpenAI-compatible API on :8080
$env:GRAP_LLM_BASE_URL   = "http://127.0.0.1:8080/v1"
$env:GRAP_LLM_MODEL      = "ternary-bonsai-27b"       # cosmetic; server has one model
$env:GRAP_LLM_EXTRA_BODY = '{"chat_template_kwargs": {"enable_thinking": false}}'
#   ^ Bonsai-27B is a reasoning model; disable thinking for fast batch extraction.
#   The server exposes 4 parallel slots — pair with concurrency=4 below.

# vLLM in WSL2: see scripts/serve-vllm-wsl.ps1, then GRAP_LLM_BASE_URL=http://localhost:8000/v1
```

Embeddings are a separate, smaller model (the LLM server does not embed):

```powershell
$env:GRAP_EMBED_BACKEND = "ollama"            # nomic-embed-text via Ollama, or:
# "auto" (sentence-transformers → hashing fallback), "hashing" (offline/deterministic)
```

`GRAP_LLM_STUB=1` replaces the LLM with the deterministic stub (tests, offline demos).

## The end-to-end loop

```text
1  create_knowledge_graph(path) / load_knowledge_graph(path)
2  ingest_documents(evidence_dir)                  # evidence into the KG
3  extract_ontology(docs_dir)                      # LLM proposes; graph-augmented;
                                                   # versioned as *proposed* + concept_hashes
4  list_proposed_ontologies() / get_proposed_ontology(version_hash)   # human review
5  adopt_proposed_ontology(version_hash, rationale)
       - verifies concept_hashes (sha256 of each concept node's text captured at
         extraction) against the stored yaml — REFUSES adoption if tampered
       - projects the ontology into the KG and core-verifies the written nodes
       - publishes an adoption version + audit event with the rationale
6  load_procedure_graph(pg.yaml) → publish_pg → promote_pg → activate(set_as_production=True)
7  detect_gaps() / run_procedure()                 # deterministic hybrid scoring;
                                                   # every run + explanation versioned
8  get_explanation(target), query_events(), reconstruct_run(run_id)
```

## Improvement loops per graph

**Knowledge Graph.** `reingest_documents` is incremental (a content-hash manifest
skips unchanged files), so re-running it after document edits only touches deltas.
`compact_graph` reclaims blob space after churn; `reindex_entities` rebuilds the
entity sidecar. Node integrity is checkable at any time via the core
(`get_node_content_hash` / `verify_node` — sha256 of the node text, recomputed on
load).

**Concept graph.** `extend_extraction_graph(workdir, docs_path, concurrency=N)` is
the long-lived loop: it syncs the durable working graph to the corpus
(new/changed/removed chunks), feeds each chunk to the LLM *with the relevant known
subgraph as context*, resolves entities against the live graph (exact
content-hash match first, then embedding similarity), grounds relations, and
spends a bounded re-look budget only where the graph flags conflicts. Concept
nodes carry core content hashes end-to-end — through the sqlite store, into
proposal yamls, and into the adoption gate. `open_extraction_graph` loads the
projection read-only for browsing with the ordinary graph tools (neighbors,
context bundles); durable changes go only through `extend_extraction_graph`.

**Procedure Graph.** Authoring is file-based; governance is versioned:
`validate_procedure_graph` (publish-time rules), `diff_procedure_graphs` (review),
`publish_pg` (stores + validates), `promote_pg` (refused on validation errors),
`activate` (freezes weights). To trial changes safely, `run_experiment` executes
gap detection in quarantine (full audit parity, excluded from production
metrics), and `adopt_experiment` promotes through the same validation-gated path.

**Trace graph.** Nothing to do — every mutation and run lands here with frozen
version hashes. Use `reconstruct_run(run_id)` to check a past run is fully
reproducible (it names any missing artifacts), `query_events` to walk history,
and `enforce_retention` (admin) to prune by the governed windows.

## Dark-factory mode (Phase 1)

With the governed adoption policy enabled, step 4–5 of the loop needs no human:

```text
set_policy("enabled", True, rationale)        # admin; THE rare human act
extract_ontology(docs)                        # policy evaluates the proposal:
                                              #   integrity + validity + churn budget
                                              #   + provenance floor
   pass -> auto-adopted (audited adopted_by=policy)
   fail -> audited adoption_policy_rejected; the line never blocks
list_pending_interventions()                  # the ONLY place a human looks:
                                              # empty queue = the factory needs nobody
rollback_ontology(version, reason)            # the safety valve: a wrong adoption
                                              # costs one audited revert
evaluate_proposal(v) / diff_ontologies(v)     # dry-run the verdict / inspect churn
diff_runs(run_a, run_b)                       # triage by delta: only new/regressed
                                              # gaps deserve attention
```

Budgets (`max_outcomes_added/removed`, `max_characteristics_removed`,
`min_source_chunks`) are governed config — tune with `set_policy`, every change
audited. Removing an outcome the active PG references always fails churn,
regardless of budget. See `docs/dark-factory-proposals.md` for Phases 2–4
(watcher, canary auto-promotion, n-version extraction).

## Verified reference runs

```powershell
# Offline, no model, ~5s — full loop incl. hash-verified adoption:
.venv\Scripts\python.exe mcp-server\demo_kee.py

# Same loop against live local Bonsai-27B (server started as above):
$env:GRAP_EMBED_BACKEND = "hashing"   # or ollama
.venv\Scripts\python.exe mcp-server\demo_kee.py --real
```

Both paths were run green on 2026-07-19 (suite: `python -m pytest mcp-server/tests`).
