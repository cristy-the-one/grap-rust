# Running the grap ASPICE / Knowledge-Extraction demo on a fresh Windows system

This packages three pieces so they run together on a clean machine:

- **grap-rust-core** — the Rust knowledge-graph engine, exposed to Python as the
  **pygrap** extension (shipped as a prebuilt wheel; no Rust needed in the common case).
- **grap MCP server** (`mcp-server/grap_mcp`) — the capability surface.
- **the demo GUI** (`aspice-gui`) — the browser console: ASPICE gap detection,
  the Knowledge Extraction Engine, and the legal knowledge-graph view.

## Prerequisites

- **Python 3.11+ (64-bit)** on `PATH` — required.
- **Git** — to clone the repo.
- *Optional* — **Rust toolchain** (<https://rustup.rs>): only needed if the bundled
  pygrap wheel doesn't match your Python (the installer then builds it from source).
- *Optional* — **[Ollama](https://ollama.com)** for the LLM-powered features
  (Knowledge Extraction). The demo runs **fully offline without it** (ASPICE gap
  detection works with no model; extraction falls back to a deterministic stub).

The bundled wheel targets **CPython 3.13, win_amd64**. On a different Python
version/arch the installer automatically rebuilds pygrap from source (needs Rust).

## Install (one command)

From the repo root in **PowerShell**:

```powershell
.\scripts\install.ps1
```

This creates `.\.venv`, installs pygrap (bundled wheel or source build), the MCP
server (pulls `mcp` + `PyYAML`), and the GUI deps (`fastapi>=0.128`, `uvicorn`),
then verifies the stack imports.

## Run the demo

```powershell
.\scripts\run-demo.ps1
# then open http://127.0.0.1:8765
```

On the **Dashboard**, click **Run bootstrap** for the end-to-end ASPICE demo
(create KG → ingest → ontology → procedure graph → gap detection). No model required.

### What works offline vs. with Ollama

| Feature | Offline (no Ollama) | With Ollama |
|---|---|---|
| ASPICE bootstrap + gap detection | ✅ | ✅ |
| Knowledge Extraction (Knowledge panel / dashboard demo) | runs with a deterministic **stub** (empty proposals) | real extraction with your model |
| Embeddings (reconcile / dedup) | **hashing** fallback | `nomic-embed-text` (local) |
| Legal graph (citations) | ✅ | ✅ |
| Legal graph (extracted relations + expand) | stub | real |

To enable the real LLM features, install Ollama and pull a model, then:

```powershell
ollama pull llama3.1:8b
ollama pull nomic-embed-text
# the demo auto-detects Ollama at http://localhost:11434/v1
.\scripts\run-demo.ps1
```

Relevant environment variables (all optional):

- `GRAP_LLM_BASE_URL` (default `http://localhost:11434/v1`), `GRAP_LLM_MODEL`, `GRAP_LLM_API_KEY`
- `GRAP_LLM_STUB=1` — force the offline stub
- `GRAP_EMBED_BACKEND` — `ollama` (default in the GUI) / `local` (sentence-transformers) / `hashing`
- `GRAP_ROLE` — `viewer` | `operator` | `admin` (default `operator`)

## Large-corpus batch extraction (`scripts/extract_corpus.py`)

The interactive Extract panel calls the LLM once per chunk, sequentially — fine
for a few files, far too slow for a whole corpus. For bulk extraction use the
batch runner: **parallel across endpoints, bigger chunks, checkpointed/resumable**,
emitting the same candidate-ontology the GUI loads.

```powershell
# one GPU
python scripts\extract_corpus.py real-data-law-ro --model llama3.2:3b `
    --chunk-chars 2000 --out out\legal-ontology

# two GPUs — one Ollama per GPU, then point the runner at both:
#   $env:CUDA_VISIBLE_DEVICES="0"; $env:OLLAMA_HOST="127.0.0.1:11434"; ollama serve
#   $env:CUDA_VISIBLE_DEVICES="1"; $env:OLLAMA_HOST="127.0.0.1:11435"; ollama serve
python scripts\extract_corpus.py real-data-law-ro `
    --endpoints http://localhost:11434/v1,http://localhost:11435/v1 `
    --model llama3.2:3b --chunk-chars 2000 --workers 4 --out out\legal-ontology
```

Notes:
- **Resumable** — kill it (Ctrl+C twice / `taskkill /F`) any time; rerun continues
  from `out\…\fragments.jsonl` (delete that file to start over).
- **Model/chunk size** — `llama3.2:3b` is ~3× faster than `llama3.1:8b` and good
  enough for proposals (noisier). `--chunk-chars 2000` is a safe size for 3b;
  8b can go larger. Bigger chunks = fewer LLM calls.
- **Speed** — e.g. the full law corpus: ~11 days (8b/800-char/serial) →
  roughly **~16h** (3b/2000-char/2 GPUs). Use `--max-files N` for a quick subset.
- **Load the result in the GUI** — Knowledge panel → *Load ontology* →
  `out\legal-ontology\candidate-ontology.yaml`. Project the relations via
  *POST /api/relations/project* (body = `relations.json`) or the **Legal graph**
  panel. (A full-corpus ontology has thousands of outcomes — load a subset run
  for an interactive demo.)

## Maintainer: rebuild the bundled wheel

```powershell
.\scripts\build-dist.ps1     # needs Rust + maturin; writes packaging\wheels\pygrap-*.whl
```

## Troubleshooting

- **`fastapi 0.115` / `starlette` clash** — a stray global `fastapi 0.115` is
  incompatible with the `starlette 1.x` the MCP SDK pulls in. The venv pins
  `fastapi>=0.128`; always run via the venv (`scripts\run-demo.ps1`).
- **"not a supported wheel on this platform"** — your Python differs from the
  bundled wheel's tag; install Rust and re-run `install.ps1` to build from source.
- **Port in use** — `.\scripts\run-demo.ps1 -Port 8770`.
