# GRAP ASPICE Console

Browser UI for the **ASPICE-oriented MCP surface** (`mcp-server/grap_mcp`). It calls the same Python functions the MCP server exposes—no separate protocol layer.

## Prerequisites

- Python 3.11+
- Built **pygrap** bindings (`maturin develop` in `crates/pygrap` from repo root)

## Setup (recommended: isolated venv)

Use a **venv** so GUI dependencies do not clash with a global `fastapi 0.115` + `starlette 1.x` install (common after `jcodemunch-mcp`).

From the **repository root** (worktree or main checkout):

```powershell
.\aspice-gui\scripts\setup-venv.ps1
```

Then start the server:

```powershell
.\aspice-gui\scripts\run.ps1
```

Or manually:

```powershell
.\aspice-gui\.venv\Scripts\Activate.ps1
cd aspice-gui
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765

### Manual venv (same result)

```powershell
cd aspice-gui
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .                    # fastapi>=0.128 (compatible with starlette 1.x)
pip install -e ..\mcp-server
pip install -e ..\crates\pygrap     # after maturin develop, if needed
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

### Why not `fastapi==0.115`?

`fastapi 0.115` requires `starlette<0.39`. The MCP SDK (`mcp>=1.27`) pulls **starlette 1.x**. Installing both into the same environment triggers the resolver warning you saw. This project pins **`fastapi>=0.128`**, which works with starlette 1.x.

Do **not** mix a global `pip install fastapi==0.115` with `pip install -e ..\mcp-server` unless you use a fresh venv.

## What it covers

| Area | MCP capabilities |
|------|------------------|
| Load | `load_knowledge_graph`, `load_ontology`, `load_procedure_graph`, `validate_procedure_graph` |
| Lifecycle | `publish_pg`, `promote_pg`, `activate`, `set_production`, `list_versions` |
| Gaps | `detect_gaps`, `run_experiment`, `run_procedure`, `get_explanation` |
| Audit | `get_audit_log`, `list_runs`, `reconstruct_run`, `get_quality_metrics` |
| Governance | `get_config`, `set_weight`, `set_retention`, `enforce_retention`, roles |

Quick start on the dashboard loads `docs/examples/minimal-aspice-ontology.yaml` and `docs/examples/simple-aspice-gap-check.pg.yaml`.

## Branch

Developed on `feature/aspice-gui` in `.worktrees/aspice-gui`.