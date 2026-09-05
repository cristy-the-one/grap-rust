# KEE Demo – Client Machine Setup (Windows)

This tutorial explains exactly what you need to do on a **client / fresh machine** to successfully run the Knowledge Extraction Engine demo:

```powershell
python mcp-server/demo_kee.py
```

The demo is designed to be **portable and fully offline by default** (no LLM, no internet after the initial setup).

It demonstrates:
- Graph-augmented extraction using the `WorkingGraph`
- `version_hash` + per-concept `concept_hashes` (core content hashes for provenance/dedup)
- The full governance loop: `extract_ontology` → `list_proposed_ontologies` → `get_proposed_ontology` → `adopt_proposed_ontology` (with required rationale)
- Using the adopted ontology for gap detection on evidence documents

---

## 1. Prerequisites

| Requirement              | Details                                                                 |
|--------------------------|-------------------------------------------------------------------------|
| **Python**               | **3.13 x64** recommended (wheels target `cp313-win_amd64`). Python 3.11+ works. |
| **Git**                  | To clone the repository                                                 |
| **PowerShell**           | `pwsh` (PowerShell 7+) is convenient                                      |
| **Rust + maturin**       | **Only needed** if no matching prebuilt wheel is available             |
| **Internet**             | Only for first-time `pip install` and (optionally) downloading Ollama  |

> **Note**: You do **not** need the full GUI stack or ASPICE demo to run this.

---

## 2. Clone the Repository

```powershell
git clone <your-repo-url> grap-rust
cd grap-rust
```

> The demo lives inside the full repo. Do not copy only the `mcp-server` folder.

---

## 3. Create a Virtual Environment (Recommended)

```powershell
python -m venv .venv-kee
.\.venv-kee\Scripts\Activate.ps1
```

Activate this environment in every new terminal.

---

## 4. Install pygrap (the Rust core bindings)

This is the most important step.

### Option A – Use the prebuilt wheel (fastest, no Rust needed)

```powershell
# From repo root
pip install --no-deps --force-reinstall "packaging\wheels\pygrap-0.1.0-cp313-cp313-win_amd64.whl"
```

There is also a copy at `target\wheels\...`.

### Option B – Build from source (if the wheel doesn't match your Python)

```powershell
pip install maturin
maturin build --release -m crates\pygrap\Cargo.toml --out packaging\wheels

# Then install the newly built wheel
pip install --no-deps --force-reinstall (Get-ChildItem packaging\wheels\pygrap-*.whl | Select-Object -Last 1).FullName
```

Verify it works:

```powershell
python -c "import pygrap; print('pygrap OK:', pygrap.__file__)"
```

---

## 5. Install the Remaining Python Dependencies

The demo needs two packages:

```powershell
pip install mcp PyYAML
```

These are declared in `mcp-server/pyproject.toml`.

---

## 6. Run the Demo (Offline / Stub Mode)

From the repository root:

```powershell
python mcp-server/demo_kee.py
```

You should see output similar to:

```
Using offline StubProvider + HashingEmbedder ...
====================================================================
1. Extract + reconcile ...
====================================================================
extract_ontology status: ok
version_hash: a45837...
concept_hashes sample: [('quality_assurance', '4925...'), ...]
process_outcomes: 2
...
gaps_found=6
  GAP quality_assurance:quality_plan
  ...
Done (portable non-banal KEE demo).
```

The demo:
- Creates temporary directories (`kee-demo-...`)
- Uses the deterministic stub (no network calls)
- Shows real content hashes
- Completes the full review + adopt + gaps flow

---

## 7. (Optional) Run with a Real Local Model

### Install Ollama

1. Download from https://ollama.com
2. Start it (or let the demo start it)

### Pull models

```powershell
ollama pull llama3.1:8b
# For embeddings (optional but nice):
ollama pull nomic-embed-text
```

### Run the demo with a real model

```powershell
$env:GRAP_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
$env:GRAP_LLM_MODEL = "llama3.1:8b"
# Optional: force embeddings via Ollama
$env:GRAP_EMBED_BACKEND = "ollama"
$env:GRAP_EMBED_MODEL = "nomic-embed-text"

python mcp-server/demo_kee.py --real
```

You can also set these permanently in the environment or a `.env` file if you wrap the demo.

---

## 8. Quick Verification Commands

```powershell
# 1. pygrap is importable
python -c "import pygrap; print(pygrap.__version__ if hasattr(pygrap,'__version__') else 'OK')"

# 2. grap_mcp package is importable
python -c "
import sys
sys.path.insert(0, 'mcp-server')
import grap_mcp.server as s
print('extract_ontology:', hasattr(s, 'extract_ontology'))
print('list/get/adopt present')
"
```

---

## 9. Troubleshooting (Windows)

| Problem                              | Solution |
|--------------------------------------|----------|
| `No module named 'pygrap'`           | Install the wheel (step 4). Make sure you're in the right venv. |
| Wheel architecture mismatch          | Use Python 3.13 64-bit. Or build from source with Rust. |
| `mcp` or `yaml` not found            | `pip install mcp PyYAML` |
| Import errors inside `grap_mcp`      | Run from repo root and ensure `PYTHONPATH` includes `mcp-server` (the script does this automatically). |
| Demo produces empty results          | Make sure you are **not** setting `GRAP_LLM_STUB=1` manually in a way that overrides the demo's handler, or run without `--real`. |
| CRLF / line ending warnings          | Harmless on Windows. Git will normalize. |
| Want to force offline stub           | The demo does this automatically. You can also set `GRAP_LLM_STUB=1`. |
| Want to see what a real run looks like | Follow section 7. |

---

## 10. What the Demo Does Not Require

- No full GUI (`aspice-gui`)
- No Rust on the client (when using the wheel)
- No external API keys
- No persistent workspace (everything uses temp dirs)
- No internet after the first `pip install`

---

## Summary – Minimal One-Time Setup

```powershell
# 1. Clone + cd
# 2. python -m venv .venv-kee ; .\.venv-kee\Scripts\Activate.ps1
# 3. pip install --no-deps packaging\wheels\pygrap-*.whl
# 4. pip install mcp PyYAML
# 5. python mcp-server/demo_kee.py
```

That's it. The demo should now run successfully and demonstrate the full portable + interesting KEE flow.

---

If you want a one-command bootstrap script tailored only to the KEE demo (without the GUI), let me know and I can create one.