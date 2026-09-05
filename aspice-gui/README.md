# GRAP ASPICE Console

A single-piece browser console for the **ASPICE-oriented MCP surface**
(`mcp-server/grap_mcp`). The console owns a `GrapApplication` instance and uses
the same operations as MCP and batch runners. Selecting a workspace replaces
that instance without changing MCP globals or process environment variables.
The UI offers tracked gap checks and artifact-availability checks; it does not
execute procedure operations or replay historical evidence snapshots.

## Prerequisites

- Python 3.11+
- Built **pygrap** bindings (`maturin develop` in `crates/pygrap` from the repo root)
- `fastapi>=0.128` in your environment (see note below — this is the default
  the project installs)

## Run (single environment, no venv required)

From the repository root, install the dependencies into your normal Python
environment once:

```powershell
python -m pip install -e .\mcp-server            # grap-mcp-server (pulls mcp>=1.27)
python -m pip install -r .\aspice-gui\requirements.txt   # fastapi>=0.128, uvicorn, httpx
```

Then start it:

```powershell
.\aspice-gui\scripts\run.ps1
# or directly:
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765 --app-dir aspice-gui
```

Open <http://127.0.0.1:8765> and click **Run bootstrap** on the dashboard.

### Why `fastapi>=0.128`?

`fastapi 0.115` requires `starlette<0.39`, but the MCP SDK (`mcp>=1.27`) pulls
in **starlette 1.x**. The two are incompatible — `fastapi 0.115` passes
`on_startup` to a Starlette `Router` that no longer accepts it, so the app
fails to construct. This project pins **`fastapi>=0.128`**, which works with
starlette 1.x. If your global Python already has `fastapi 0.115`, upgrade it:

```powershell
python -m pip install -U "fastapi>=0.128"
```

### Optional: isolated venv (fallback)

If you would rather not change your global environment,
`scripts/setup-venv.ps1` builds a local `aspice-gui/.venv` with compatible
versions; `run.ps1` uses it automatically when present.

## What it does

The dashboard's **one-click bootstrap** runs the whole ASPICE workflow end to
end: it creates a fresh knowledge graph, ingests the bundled sample documents
(`sample-knowledge/`) as evidence, projects the ASPICE ontology and a procedure
graph over it, and runs gap detection — rendering the result as a step
checklist plus colour-coded gap cards with confidence meters and inline
explanations.

| Area | MCP capabilities |
|------|------------------|
| Knowledge | `create_knowledge_graph`, `ingest_documents`, `load_knowledge_graph`, `load_ontology`, `load_procedure_graph`, `validate_procedure_graph` |
| Gaps | `detect_gaps`, `run_experiment`, `run_procedure`, `get_explanation` |
| Lifecycle | `publish_pg`, `promote_pg`, `activate`, `set_production`, `list_versions` |
| Runs & temporal | `list_runs`, `get_run`, `reconstruct_run`, `query_events` |
| Quality / audit | `get_quality_metrics`, `get_audit_log` |
| Governance | `get_config`, `set_weight`, `set_retention`, `enforce_retention`, roles |

The bundled examples are `docs/examples/minimal-aspice-ontology.yaml` and
`docs/examples/simple-aspice-gap-check.pg.yaml`.

## Tests

```powershell
python -m pytest aspice-gui/tests -q      # FastAPI TestClient: health + bootstrap
python -m pytest mcp-server/tests -q      # engine + reasoner + ingest
```
