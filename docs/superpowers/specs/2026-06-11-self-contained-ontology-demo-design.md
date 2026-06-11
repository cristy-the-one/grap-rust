# Self-Contained ro-law Ontology Demo — Design

**Date:** 2026-06-11
**Status:** Approved design (pre-implementation)
**Builds on:** Phase 1 graph-augmented extraction + Phase 2 persistent extraction graph (both on `master`): `WorkingGraph`, `graph_extract_ontology`, `extend_graph_ontology`, and the `open_extraction_graph`/`extend_extraction_graph`/`export_ontology` MCP tools.

## Problem / Goal

Deliver a **self-contained Windows 11 demo** that lets a user create and grow an **ontology** from Romanian-legal ("ro-law") JSON files using a **local LLM**, leveraging the new Grap features (graph-augmented extraction + the persistent, sync-with-retraction extraction graph), and explore the result through a **polished interactive graph view**. The target machine: Intel Core Ultra 9 275HX, 64GB RAM, 2TB SSD, **RTX 5080 16GB**, Windows 11 Pro.

Today the pieces exist but aren't packaged for a turnkey local run: `llm.get_provider`/`embeddings.get_embedder` are Ollama-ready via env (`GRAP_LLM_BASE_URL`/`GRAP_LLM_MODEL`, `GRAP_EMBED_BACKEND`), the aspice-gui FastAPI app has a streaming extract wizard, and `pygrap` requires a build. But there is no one-click local stack, and the GUI does not yet expose the Phase-2 persistent-graph lifecycle.

### Decisions locked during brainstorming

1. **Packaging = Ollama + PowerShell bootstrap** (not Docker, not a bundled-weights archive). Native, GPU-via-Ollama, matches the codebase's Ollama-first design.
2. **Corpus = bundled ro-law JSON** (the law-aware `titlu`/`text_complet` schema `read_chunks` already handles); a small sample so the demo works out of the box.
3. **Visuals = interactive ontology graph + run dashboard** — concept nodes, typed relation edges, click-for-characteristics+provenance, live neighbor expansion (reuse `graph_neighbors`), and a sync-stats dashboard. (Not a full guided tour or legal-document navigator.)
4. **Default LLM = `qwen2.5:14b-instruct-q4_K_M`** (strong Romanian + structured JSON, ~9GB VRAM, fits the 5080 with headroom; overridable via `GRAP_LLM_MODEL`). **Embeddings = `bge-m3` via Ollama** (local multilingual).

## Architecture & components

A new `demo/win/` package plus a thin GUI feature layer. Six units:

- **`demo/win/bootstrap.ps1`** — idempotent one-time setup: detect/install **Ollama** (`winget install Ollama.Ollama`, fallback to the direct `OllamaSetup.exe`); `ollama pull qwen2.5:14b-instruct-q4_K_M` + `ollama pull bge-m3`; create a Python `.venv`; `pip install` the mcp-server + aspice-gui deps and the **bundled prebuilt `pygrap` wheel**; write `demo.env` (`GRAP_LLM_BASE_URL=http://localhost:11434/v1`, `GRAP_LLM_MODEL=qwen2.5:14b-instruct-q4_K_M`, `GRAP_EMBED_BACKEND=ollama`, embed model `bge-m3`); print an `nvidia-smi` GPU check. Detect-and-skip anything already present.
- **`demo/win/run-demo.ps1`** — launcher: ensure `ollama serve` is up, start the aspice-gui FastAPI server (uvicorn) on a fixed port (8080), health-check both (and `/models`), open the browser, clean Ctrl-C shutdown. Reports port/Ollama conflicts.
- **`demo/corpus/ro-law/*.json`** — a small bundled set of ro-law JSON files (`{"id","titlu","text_complet"}`); the GUI prefills this path.
- **`demo/win/wheels/pygrap-*.whl`** — a prebuilt `pygrap` wheel (cp313 win_amd64), produced via `maturin build --release` so the target needs no Rust toolchain.
- **GUI feature layer** — backend endpoints in `aspice-gui/backend/main.py` + a frontend "Persistent Ontology" panel (details below).
- **`demo/win/README.md`** — prereqs (Win 11 x64, NVIDIA driver, **Python 3.13 x64**), run steps, what it demonstrates, troubleshooting (VRAM, ports, Ollama, wheel/Python mismatch).

The packaging is native + GPU-via-Ollama; the GUI layer is thin wiring over already-shipped `open`/`extend`/`export` + `graph_neighbors`. The prebuilt wheel is what makes it self-contained without dev tools.

## Data flow

1. **`bootstrap.ps1`** (once): install Ollama → pull Qwen2.5-14B + bge-m3 → `.venv` + bundled `pygrap` wheel + deps → write `demo.env`. Idempotent.
2. **`run-demo.ps1`**: start Ollama (GPU) + the GUI, health-check, open browser.
3. **User**: corpus path prefilled to `demo/corpus/ro-law/`; click **Build / Grow** → NDJSON progress stream.
4. **`extend_graph_ontology`** runs server-side with the local Qwen2.5 (extraction) + bge-m3 (embeddings): graph-augmented (Phase 1) + sync-with-retraction (Phase 2).
5. **Persistence**: the `GraphStore` (truth) at a fixed workdir is updated and the pygrap projection rebuilt → the ontology survives across runs.
6. **Visualization**: the GUI loads a graph snapshot (concepts + edges) and renders the interactive view (click → characteristics + provenance; double-click → expand neighbors); the dashboard shows sync stats.
7. **Re-run loop**: edit/add ro-law files and Build/Grow again → incremental sync (added/changed/removed) on the *same* persistent graph — the headline of the new features. Everything after the first model pull runs fully offline.

## GUI wiring (endpoints + contracts)

The aspice-gui backend already calls `grap_mcp` functions directly with `get_provider`/`get_embedder`; the new endpoints mirror that pattern (Pydantic body, error-dict style). Five additions to `aspice-gui/backend/main.py`:

- **`POST /api/ontology/extend`** `{workdir, corpus_path, model?, sim?, max_files?}` → **streams NDJSON** progress (reusing the wizard thread+queue stream pattern), ending with sync stats + the graph snapshot. Calls `extend_graph_ontology` with the local provider/embedder.
- **`POST /api/ontology/open`** `{workdir}` → `open_extraction_graph` (loads `_state["kg"]`) → counts. Lets the graph view + neighbor expansion work after a reload without re-extracting.
- **`GET /api/ontology/snapshot?workdir=...`** → the render payload (a presentation-only transform of the `export_ontology` dict, living in the GUI backend, not in `kee`):
  ```json
  { "nodes": [{"id": "<oid>", "name": "<canonical>", "characteristics": ["..."], "provenance": ["<law#article>", "..."]}],
    "edges": [{"source": "<oid>", "target": "<oid>", "type": "<relation>", "sources": ["..."]}],
    "stats": {"concepts": 0, "edges": 0, "added": 0, "changed": 0, "removed": 0, "relooks": 0, "parked": 0} }
  ```
  Relation endpoints (names in the export dict's `relations`) are resolved to `oid`s via the `process_outcomes` name→id map so nodes and edges share one id space. `provenance` comes from the export dict's `provenance` map (keyed by `oid`).
- **Neighbor expansion** reuses the existing `POST /api/graph/neighbors` against the opened `_state["kg"]`.
- **`GET /api/ontology/export`** `{workdir, out_path?}` → `export_ontology` (YAML download). Optional convenience.

**Minimal additive touch to shipped code:** `extend_graph_ontology` gains an optional `progress: Callable[[dict], None] = None` callback (default `None` → zero behavior change), invoked with `{phase, chunk, added/changed/removed, ...}` events. The `/api/ontology/extend` endpoint runs extend in a worker thread and drains those events into the NDJSON stream. This is the only change outside `demo/` + the GUI.

**Frontend "Persistent Ontology" panel:** prefilled corpus + workdir, a `sim` slider, **Build/Grow** (streams progress → renders graph on done), the interactive ontology graph (click node → side panel with characteristics + provenance; double-click → expand neighbors via `/api/graph/neighbors`), and a stats dashboard (concepts, edges, added/changed/removed, relooks, parked). It reuses whatever graph-viz library the frontend already uses for `graph_neighbors` expand-on-click (confirmed during planning and matched).

## Testing

Mirror the existing offline aspice-gui tests (`StubProvider` + `HashingEmbedder`, deterministic):

- `/api/ontology/extend` over a tiny temp ro-law corpus (stub provider) → returns sync stats + a snapshot with nodes/edges; the stream emits phase events ending in `done`.
- Snapshot transform: given an `export_ontology` dict → nodes + edges sharing one `oid` id-space, provenance attached.
- `/api/ontology/open` sets `_state["kg"]`; snapshot + neighbor expansion work afterward.
- `progress` callback unit test on `extend_graph_ontology`: the callback receives phase events; `progress=None` leaves the existing Phase-2 tests green.
- PowerShell scripts: not unit-tested (ops); a documented manual smoke + a tiny test asserting `bootstrap` writes `demo.env` with the expected keys (assert against the template/written file). The real Qwen-via-Ollama path is exercised manually by running the demo.
- Frontend: minimal/manual; a smoke that the app index serves and the new panel's assets load (existing aspice-gui test style).

## Scope / non-goals (YAGNI)

- ❌ No bundled model weights (first-run pull); ❌ no Docker; ❌ no `.exe` installer (PowerShell scripts only).
- ❌ No new ontology-query MCP/GUI tools — reuse `graph_neighbors` + open/extend/export.
- ❌ Small bundled sample corpus, not a full ro-law database.
- ❌ Coarse per-chunk progress, not per-token streaming.
- ❌ No auth/multi-user; ❌ no GPU tuning beyond Ollama defaults.
- ❌ No Phase-1/2 logic changes beyond the additive `progress` callback on `extend_graph_ontology`.

## Build-time artifacts & risks

- **Prebuilt `pygrap` wheel** — built on a Windows 11 / Python 3.13 x64 machine via `maturin build --release` (cp313 win_amd64), bundled under `demo/win/wheels/`. Risk: the target's Python must be **3.13 x64**; `bootstrap.ps1` checks the interpreter version and errors clearly on mismatch, falling back to a source build only if a Rust toolchain is detected.
- **Ollama install** — `winget install Ollama.Ollama`; fallback to the direct `OllamaSetup.exe` download; detect-and-skip if already installed.
- **Model pull** — ~9GB (Qwen2.5 14B Q4) + ~1.2GB (bge-m3) on first run; idempotent; well within the 2TB SSD; documented wait.
- **VRAM** — 14B Q4 (~9GB) fits the 16GB 5080 with context headroom; overridable to an 8B model via `GRAP_LLM_MODEL` if needed.
- **Ports** — GUI on 8080, Ollama on 11434; the launcher checks and reports conflicts.

## Open questions for implementation planning

- The exact graph-viz library the aspice-gui frontend already uses (for `graph_neighbors` expand-on-click) — confirm in `aspice-gui/frontend/` and match it for the ontology view rather than introducing a new dependency.
- The default workdir location for the persistent graph (e.g. `demo/workspace/ro-law-graph`) and whether it ships empty or pre-seeded; lean: empty, built on first Build/Grow.
- Sourcing the bundled ro-law sample JSON (public-domain legal excerpts vs small synthetic files); lean: a handful of short, clearly-labelled sample files sufficient to show multi-document concept resolution + relations.
- The exact env var name for the Ollama embedding model — confirm in `embeddings.get_embedder` (alongside `GRAP_EMBED_BACKEND`) and set it to `bge-m3` in `demo.env`; the design intent is fixed (bge-m3 via Ollama), only the variable name needs verifying.
