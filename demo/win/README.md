# ro-law Ontology Demo (Windows 11)

A self-contained, fully local demo of grap's persistent ontology workflow:
build and incrementally grow a knowledge graph from a Romanian-law sample
corpus using a **local** LLM (via Ollama) and the aspice-gui browser console.
No cloud APIs, no API keys, everything runs on your machine.

## What it demonstrates

- **Build a persistent ro-law ontology** from a sample corpus, entirely with a
  local LLM, driven from the GUI's **Persistent Ontology** panel.
- **Interactive graph + provenance**: explore extracted entities/relations and
  trace each one back to the source chunk it came from.
- **Incremental sync**: re-run the extend step to see the ontology grow
  in place (only new/changed content is added; existing nodes are reconciled).

## Prerequisites

- **Windows 11 x64**
- A recent **NVIDIA driver** strongly recommended (the LLM runs on GPU; CPU
  fallback works but is slow). ~10 GB of free VRAM is comfortable for the
  default 14B model.
- **Python 3.13 x64** on `PATH` (the bundled `pygrap` wheel is built for
  CPython 3.13 / win_amd64).
- **PowerShell 7+** (`pwsh`).
- **Internet access on first run** to install Ollama and download the models
  (~10 GB total, one time).

## Steps

From the repository root, in a PowerShell 7 prompt:

```powershell
# 1) One-time setup: installs Ollama, pulls models, creates .venv-demo,
#    installs the bundled pygrap wheel + server/GUI deps, writes demo.env.
pwsh -File demo\win\bootstrap.ps1

# 2) Launch: starts Ollama, serves the GUI, opens the browser.
pwsh -File demo\win\run-demo.ps1
```

Then in the browser (opens automatically at <http://localhost:8080>), open the
**Persistent Ontology** panel, point it at the sample ro-law corpus, and click
through *Extend* to build the graph. Re-run *Extend* to watch it sync
incrementally.

`bootstrap.ps1` is idempotent - safe to re-run if a step fails partway.

## Local stack (written to `demo.env` by bootstrap)

| Variable             | Value                                   |
| -------------------- | --------------------------------------- |
| `GRAP_LLM_BASE_URL`  | `http://localhost:11434/v1`             |
| `GRAP_LLM_MODEL`     | `qwen2.5:14b-instruct-q4_K_M`           |
| `GRAP_EMBED_BACKEND` | `ollama`                                |
| `GRAP_EMBED_MODEL`   | `bge-m3`                                |
| `GRAP_WORKSPACE`     | `<repo>/demo/workspace`                 |
| `ASPICE_GUI_PORT`    | `8080`                                  |

## Troubleshooting

- **Out of VRAM / model won't load**: switch to a smaller model. Pull it and
  edit `GRAP_LLM_MODEL` in `demo\win\demo.env`:
  ```powershell
  ollama pull llama3.1:8b
  # then set in demo.env:  GRAP_LLM_MODEL=llama3.1:8b
  ```
- **Port 8080 already in use**: change `ASPICE_GUI_PORT` in `demo.env` (the
  launcher binds and opens that port).
- **Port 11434 already in use**: another Ollama is already serving - that's
  fine, the launcher reuses it. If a stale process is wedged, stop it
  (`Get-Process ollama | Stop-Process`) and re-run `run-demo.ps1`.
- **`pygrap` import error / wheel mismatch**: the bundled wheel targets
  **Python 3.13 x64**. Confirm `python --version` reports 3.13 and that it is
  64-bit (`python -c "import sys;print(sys.maxsize > 2**32)"` should print
  `True`). If you must rebuild the wheel, see `demo\win\wheels\README.md`.
- **First run is slow / appears stuck**: the model download is ~10 GB. Watch
  the Ollama pull progress in the bootstrap console.
- **`ollama` not found after install**: open a fresh terminal so the updated
  `PATH` is picked up, then re-run `bootstrap.ps1`.
