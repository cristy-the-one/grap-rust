"""ASPICE MCP surface — local web GUI (FastAPI + static frontend)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import bridge

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(
    title="GRAP ASPICE Console",
    description="Browser UI for the ASPICE-oriented grap MCP capability surface",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PathBody(BaseModel):
    path: str


class ScopeBody(BaseModel):
    scope: str = "all"
    hypothesis: Optional[str] = None


class PublishBody(BaseModel):
    level: str = "draft"
    rationale: Optional[str] = None


class PromoteBody(BaseModel):
    version_hash: str
    rationale: str


class ActivateBody(BaseModel):
    activation_id: str
    version_hash: str
    set_as_production: bool = False
    weights: Optional[dict[str, float]] = None
    kg_version: Optional[str] = None


class ProductionBody(BaseModel):
    activation_id: str


class RoleBody(BaseModel):
    role: str


class WeightBody(BaseModel):
    name: str
    value: float
    rationale: str


class RetentionBody(BaseModel):
    key: str
    days: int
    rationale: str


class DiffBody(BaseModel):
    path_a: str
    path_b: str


class WorkspaceBody(BaseModel):
    path: Optional[str] = None


class ExplainBody(BaseModel):
    target: str


class RunQueryBody(BaseModel):
    run_id: Optional[str] = None
    event_type: Optional[str] = None


@app.on_event("startup")
def _startup() -> None:
    bridge.ensure_workspace(os.environ.get("GRAP_WORKSPACE"))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "repo_root": str(bridge.repo_root())}


@app.get("/api/examples")
def examples() -> dict:
    return bridge.default_examples()


@app.post("/api/workspace")
def set_workspace(body: WorkspaceBody) -> dict:
    path = bridge.ensure_workspace(body.path)
    return {"status": "ok", "workspace": path}


@app.get("/api/status")
def get_status() -> dict:
    return bridge.status()


@app.get("/api/capabilities")
def get_capabilities() -> dict:
    return bridge.capabilities()


@app.get("/api/role")
def get_role() -> dict:
    return bridge.call("get_role")


@app.post("/api/role")
def set_role(body: RoleBody) -> dict:
    return bridge.call("set_role", role=body.role)


@app.post("/api/load/kg")
def load_kg(body: PathBody) -> dict:
    return bridge.call("load_knowledge_graph", path=body.path)


@app.post("/api/load/ontology")
def load_ontology(body: PathBody) -> dict:
    return bridge.call("load_ontology", path=body.path)


@app.post("/api/load/pg")
def load_pg(body: PathBody) -> dict:
    return bridge.call("load_procedure_graph", path=body.path)


@app.post("/api/validate/pg")
def validate_pg(body: PathBody) -> dict:
    return bridge.call("validate_procedure_graph", path=body.path)


@app.post("/api/diff/pg")
def diff_pg(body: DiffBody) -> dict:
    return bridge.call("diff_procedure_graphs", path_a=body.path_a, path_b=body.path_b)


@app.post("/api/publish")
def publish(body: PublishBody) -> dict:
    return bridge.call("publish_pg", level=body.level, rationale=body.rationale)


@app.post("/api/promote")
def promote(body: PromoteBody) -> dict:
    return bridge.call("promote_pg", version_hash=body.version_hash, rationale=body.rationale)


@app.post("/api/activate")
def activate(body: ActivateBody) -> dict:
    return bridge.call(
        "activate",
        activation_id=body.activation_id,
        version_hash=body.version_hash,
        weights=body.weights,
        set_as_production=body.set_as_production,
        kg_version=body.kg_version,
    )


@app.post("/api/production")
def set_production(body: ProductionBody) -> dict:
    return bridge.call("set_production", activation_id=body.activation_id)


@app.get("/api/production")
def get_production() -> dict:
    return bridge.call("get_production")


@app.get("/api/versions")
def list_versions() -> dict:
    return bridge.call("list_versions")


@app.post("/api/gaps/detect")
def detect_gaps(body: ScopeBody) -> dict:
    return bridge.call("detect_gaps", scope=body.scope)


@app.post("/api/gaps/experiment")
def run_experiment(body: ScopeBody) -> dict:
    return bridge.call("run_experiment", scope=body.scope, hypothesis=body.hypothesis)


@app.post("/api/gaps/explain")
def explain_gap(body: ExplainBody) -> dict:
    return bridge.call("get_explanation", target=body.target)


@app.post("/api/procedure/run")
def run_procedure(body: ScopeBody) -> dict:
    return bridge.call("run_procedure", scope=body.scope)


@app.get("/api/config")
def get_config() -> dict:
    return bridge.call("get_config")


@app.post("/api/config/weight")
def set_weight(body: WeightBody) -> dict:
    return bridge.call("set_weight", name=body.name, value=body.value, rationale=body.rationale)


@app.post("/api/config/retention")
def set_retention(body: RetentionBody) -> dict:
    return bridge.call("set_retention", key=body.key, days=body.days, rationale=body.rationale)


@app.post("/api/retention/enforce")
def enforce_retention() -> dict:
    return bridge.call("enforce_retention")


@app.get("/api/audit")
def audit_log() -> dict:
    return bridge.call("get_audit_log")


@app.get("/api/runs")
def list_runs(kind: Optional[str] = None) -> dict:
    return bridge.call("list_runs", kind=kind)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    return bridge.call("get_run", run_id=run_id)


@app.get("/api/runs/{run_id}/reconstruct")
def reconstruct_run(run_id: str) -> dict:
    return bridge.call("reconstruct_run", run_id=run_id)


@app.post("/api/events/query")
def query_events(body: RunQueryBody) -> dict:
    return bridge.call("query_events", event_type=body.event_type, run_id=body.run_id)


@app.get("/api/quality")
def quality_metrics() -> dict:
    return bridge.call("get_quality_metrics")


def _api_error(result: dict) -> None:
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", result))


@app.post("/api/wizard/bootstrap")
def wizard_bootstrap() -> dict:
    """One-click POC: load example ontology + PG."""
    ex = bridge.default_examples()
    steps = []
    for label, cap, path_key in (
        ("ontology", "load_ontology", "ontology"),
        ("procedure_graph", "load_procedure_graph", "procedure_graph"),
    ):
        r = bridge.call(cap, path=ex[path_key])
        steps.append({label: r})
        _api_error(r)
    return {"status": "ok", "steps": steps, "examples": ex}


@app.post("/api/wizard/publish-activate")
def wizard_publish_activate(body: PublishBody) -> dict:
    pub = bridge.call("publish_pg", level=body.level, rationale=body.rationale or "GUI publish")
    _api_error(pub)
    vhash = pub.get("version_hash")
    if not vhash:
        raise HTTPException(400, "publish did not return version_hash")
    prom = bridge.call("promote_pg", version_hash=vhash, rationale="GUI promote")
    _api_error(prom)
    act_id = "gui-activation"
    act = bridge.call(
        "activate",
        activation_id=act_id,
        version_hash=vhash,
        set_as_production=True,
    )
    _api_error(act)
    return {"status": "ok", "publish": pub, "promote": prom, "activate": act}


if FRONTEND.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND / "index.html")


def run() -> None:
    import uvicorn

    port = int(os.environ.get("ASPICE_GUI_PORT", "8765"))
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    run()