# GRAP ASPICE Console

Browser UI for the **ASPICE-oriented MCP surface** (`mcp-server/grap_mcp`). It calls the same Python functions the MCP server exposes—no separate protocol layer.

## Prerequisites

- Python 3.11+
- Built **pygrap** bindings (`maturin develop` in `crates/pygrap` from repo root)
- MCP server deps: `pip install -e mcp-server` from repo root

## Run

From the **repository root** (worktree or main checkout):

```powershell
cd aspice-gui
pip install -e ../mcp-server
pip install -e .
$env:PYTHONPATH = (Resolve-Path ../mcp-server).Path
python -m uvicorn backend.main:app --app-dir . --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765

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