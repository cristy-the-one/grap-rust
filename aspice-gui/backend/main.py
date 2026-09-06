"""ASPICE MCP surface — local web GUI (FastAPI + static frontend)."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import bridge

# The console talks to a local Ollama by default, so prefer Ollama for embeddings
# too — a quick local HTTP call (nomic-embed-text) instead of loading
# sentence-transformers, which keeps the reconcile phase snappy. get_embedder()
# falls back to the hashing embedder if the endpoint is unreachable, and an
# explicit GRAP_EMBED_BACKEND in the environment still wins.
os.environ.setdefault("GRAP_EMBED_BACKEND", "ollama")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    bridge.ensure_workspace(os.environ.get("GRAP_WORKSPACE"))
    yield


app = FastAPI(
    title="GRAP ASPICE Console",
    description="Browser UI for the ASPICE-oriented grap MCP capability surface",
    version="0.1.0",
    lifespan=_lifespan,
)


class PathBody(BaseModel):
    path: str


class ExtractBody(BaseModel):
    path: str
    sim: float = 0.86
    model: Optional[str] = None
    max_files: Optional[int] = None


class WizardExtractBody(BaseModel):
    path: Optional[str] = None     # corpus to extract from; default = sample knowledge
    sim: float = 0.86
    model: Optional[str] = None


class RelationsBody(BaseModel):
    relations: list = []


class LegalGraphBody(BaseModel):
    path: Optional[str] = None     # corpus dir; default = real-data-law-ro
    max_files: int = 400
    with_relations: bool = False
    relation_files: int = 3
    model: Optional[str] = None


class OntologyExtendBody(BaseModel):
    workdir: str
    corpus_path: str
    model: Optional[str] = None
    sim: float = 0.86
    max_files: Optional[int] = None
    chunk_chars: int = 800
    concurrency: int = 1           # parallel LLM calls per window (1 = sequential)


class NeighborsBody(BaseModel):
    node_id: int
    types: Optional[list] = None
    limit: Optional[int] = None
    cursor: Optional[str] = None


class RelationsFileBody(BaseModel):
    path: str
    top: int = 200


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
    limit: Optional[int] = None
    cursor: Optional[str] = None


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


@app.post("/api/kg/create")
def create_kg(body: PathBody) -> dict:
    return bridge.call("create_knowledge_graph", path=body.path)


@app.post("/api/ingest")
def ingest_docs(body: PathBody) -> dict:
    return bridge.call("ingest_documents", path=body.path)


@app.post("/api/extract")
def extract_ontology(body: ExtractBody) -> dict:
    return bridge.call("extract_ontology", path=body.path, sim=body.sim,
                       model=body.model or None, max_files=body.max_files)


@app.post("/api/relations/project")
def project_relations_ep(body: RelationsBody) -> dict:
    """Project extracted relations as typed edges in the current KG."""
    return bridge.call("project_relations", relations=body.relations)


@app.post("/api/legal-graph/build")
def legal_graph_build(body: LegalGraphBody) -> dict:
    """Build one navigable legal KG (citations + extracted relations)."""
    path = body.path or str(bridge.repo_root() / "real-data-law-ro")
    return bridge.call("build_legal_graph", path=path, max_files=body.max_files,
                       with_relations=body.with_relations,
                       relation_files=body.relation_files, model=body.model or None)


@app.post("/api/graph/neighbors")
def graph_neighbors_ep(body: NeighborsBody) -> dict:
    """Live neighbors of a node from the current KG (powers expand-on-click)."""
    return bridge.call("graph_neighbors", node_id=body.node_id, types=body.types,
                       limit=body.limit, cursor=body.cursor)


@app.post("/api/legal-graph/from-relations")
def legal_graph_from_relations_ep(body: RelationsFileBody) -> dict:
    """Load a relations JSON file (e.g. relations.curated.json) into the graph."""
    return bridge.call("legal_graph_from_relations", path=body.path, top=body.top)


@app.get("/api/models")
def list_models() -> dict:
    """Available LLM models from the configured OpenAI-compatible endpoint."""
    try:
        from grap_mcp.kee import llm
        return {"status": "ok", "models": llm.list_models()}
    except Exception as exc:  # endpoint unreachable / no server — non-fatal for the UI
        return {"status": "error", "message": str(exc), "models": []}


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
    return bridge.call("query_events", event_type=body.event_type, run_id=body.run_id,
                       limit=body.limit, cursor=body.cursor)


@app.get("/api/quality")
def quality_metrics() -> dict:
    return bridge.call("get_quality_metrics")


def _api_error(result: dict) -> None:
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message", result))


@app.post("/api/wizard/bootstrap")
def wizard_bootstrap() -> dict:
    """One-click POC: fresh KG + ingest sample knowledge + ontology + PG + gap preview."""
    ws = bridge.ensure_workspace()
    ex = bridge.default_examples()
    kg_path = str(Path(ws) / "kg")
    steps = []
    for label, cap, kwargs in (
        ("knowledge_graph", "create_knowledge_graph", {"path": kg_path}),
        ("ingest", "ingest_documents", {"path": ex["sample_knowledge"]}),
        ("ontology", "load_ontology", {"path": ex["ontology"]}),
        ("procedure_graph", "load_procedure_graph", {"path": ex["procedure_graph"]}),
    ):
        r = bridge.call(cap, **kwargs)
        steps.append({label: r})
        _api_error(r)
    gaps = bridge.call("detect_gaps", scope="all")
    return {"status": "ok", "steps": steps, "gaps": gaps, "workspace": ws, "examples": ex}


def _extraction_events(corpus: str, model: Optional[str], sim: float,
                       max_files: Optional[int] = None) -> Iterator[dict]:
    """Extract-only event generator (no KG/adopt/gaps): prepare → per-chunk
    stream (token events from real LLM deltas) → reconcile → version. Ends with
    an 'extracted' event carrying the extract_ontology-shaped result. Shared by
    the Extract card stream and the demo wizard."""
    srv = bridge.application
    from grap_mcp.kee import embeddings, extract, llm, pipeline, reconcile, validate

    # provider only; embedder is deferred to reconcile so tokens flow immediately
    yield {"type": "phase", "name": "prepare"}
    provider = extract.configure_provider(llm.get_provider(model=model or None))
    chunks = pipeline.read_chunks(corpus, max_files=max_files)
    yield {"type": "extract_begin", "chunks": len(chunks),
           "provider": provider.name, "model": getattr(provider, "model", None)}

    can_stream = hasattr(provider, "stream")
    fragments = []
    failures = []
    for i, (src, text) in enumerate(chunks):
        yield {"type": "chunk_start", "i": i + 1, "n": len(chunks), "source": src}
        parts: list[str] = []
        ntok = 0
        try:
            if can_stream:
                for delta in provider.stream(extract.SYSTEM_PROMPT,
                                             extract.build_user_prompt(text)):
                    parts.append(delta)
                    ntok += 1
                    if ntok % 6 == 0:
                        yield {"type": "token", "i": i + 1, "n": len(chunks), "tokens": ntok}
            else:
                parts.append(provider.complete(extract.SYSTEM_PROMPT,
                                               extract.build_user_prompt(text)))
        except Exception as exc:
            failures.append({"source": src, "reason": "provider"})
            yield {"type": "chunk_error", "i": i + 1, "message": str(exc)}
            continue
        frag, reason = extract.parse_reply("".join(parts), src, text, provider)
        if frag is None:
            failures.append({"source": src, "reason": reason})
            yield {"type": "chunk_error", "i": i + 1, "message": f"Extraction rejected: {reason}"}
            continue
        n_out = len(frag["outcomes"]) if frag else 0
        if frag:
            fragments.append(frag)
        yield {"type": "chunk_done", "i": i + 1, "n": len(chunks),
               "tokens": ntok, "outcomes": n_out}

    yield {"type": "phase", "name": "reconcile"}
    embedder = embeddings.get_embedder()
    onto = reconcile.reconcile(fragments, embedder, sim=sim)
    valr = validate.validate_ontology(onto)
    yaml_text = pipeline.to_yaml(onto)
    # use the resolved workspace root WITHOUT resetting _state (ensure_workspace
    # clears the loaded KG, which would break the wizard's already-created KG)
    out_path = str(Path(srv._resolve_ws_root()) / "candidate-ontology.yaml")
    Path(out_path).write_text(yaml_text, encoding="utf-8")
    vhash = None
    try:
        version = srv._get_workspace().publish_ontology(
            yaml_text, metadata={"kind": "proposed", "source": corpus})
        srv._get_workspace().record_event(
            "ontology_proposed", corpus,
            details={"outcomes": str(len(onto["process_outcomes"])),
                     "provider": provider.name, "embedder": embedder.name})
        vhash = version.hash
    except Exception:
        pass
    extracted = {
        "status": "ok", "out_path": out_path, "version_hash": vhash,
        "provider": provider.name, "model": getattr(provider, "model", None),
        "embedder": embedder.name, "chunks": len(chunks), "fragments": len(fragments),
        "failures": failures, "extraction_complete": not failures,
        "process_outcomes": [{"id": o["id"], "name": o["name"],
                              "characteristics": o["required_characteristics"]}
                             for o in onto["process_outcomes"]],
        "provenance": onto["provenance"], "relations": onto.get("relations", []),
        "validation": valr,
    }
    yield {"type": "extracted", "extracted": extracted}


def _extract_events(path: Optional[str], model: Optional[str],
                    sim: float) -> Iterator[dict]:
    """KEE demo wizard: create KG → (extraction events) → adopt → ingest
    evidence → detect gaps. Wraps _extraction_events."""
    try:
        ws = bridge.ensure_workspace()
        ex = bridge.default_examples()
        corpus = path or ex["sample_knowledge"]
        kg_path = str(Path(ws) / "kg")

        kg = bridge.call("create_knowledge_graph", path=kg_path)
        if kg.get("status") == "error":
            yield {"type": "error", "message": kg.get("message")}
            return
        yield {"type": "step", "name": "knowledge_graph", "status": "ok"}

        extracted = None
        for event in _extraction_events(corpus, model, sim):
            if event["type"] == "extracted":
                extracted = event["extracted"]
            else:
                yield event
        if not extracted or extracted.get("status") == "error":
            yield {"type": "error", "message": "extraction produced no result"}
            return

        if not extracted.get("extraction_complete", True):
            yield {"type": "error", "message": "Extraction is incomplete; review the proposed artifact before adoption."}
            return

        yield {"type": "phase", "name": "adopt"}
        adopt = bridge.call("load_ontology", path=extracted["out_path"])
        if adopt.get("status") == "error":
            yield {"type": "error", "message": adopt.get("message")}
            return

        yield {"type": "phase", "name": "ingest_evidence"}
        md = sorted(str(p) for p in Path(corpus).glob("*.md"))
        evidence = md[:-1] if len(md) > 1 else md
        nodes = 0
        for f in evidence:
            r = bridge.call("ingest_documents", path=f)
            nodes += r.get("nodes_added", 0)

        yield {"type": "phase", "name": "detect_gaps"}
        gaps = bridge.call("detect_gaps", scope="all")

        result = {
            "status": "ok", "extracted": extracted, "gaps": gaps,
            "evidence_files": [Path(f).name for f in evidence], "workspace": ws,
            "steps": [{"knowledge_graph": kg}, {"extract": extracted},
                      {"adopt_ontology": adopt},
                      {"ingest_evidence": {"status": "ok", "files": len(evidence),
                                           "nodes_added": nodes}}],
        }
        yield {"type": "done", "result": result}
    except Exception as exc:
        yield {"type": "error", "message": str(exc)}


@app.post("/api/extract/stream")
def extract_stream(body: ExtractBody) -> StreamingResponse:
    """Streaming Extract: NDJSON progress events (phase/chunk/token), ending with
    an 'extracted' event (the same shape /api/extract returns). Token events are
    forwarded from the model's real stream deltas — genuine activity."""
    def lines() -> Iterator[str]:
        try:
            for event in _extraction_events(body.path, body.model or None,
                                            body.sim, body.max_files):
                yield json.dumps(event) + "\n"
        except Exception as exc:
            yield json.dumps({"type": "error", "message": str(exc)}) + "\n"
    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/api/ontology/extend")
def ontology_extend(body: OntologyExtendBody) -> StreamingResponse:
    """Build/grow the persistent extraction graph from a corpus, streaming NDJSON
    progress (diff/chunk/done). The terminal 'done' event carries the snapshot."""
    import queue
    import threading
    from grap_mcp.kee import llm, embeddings
    from grap_mcp.kee.graph_extract import extend_graph_ontology
    from .ontology_view import ontology_snapshot

    q: "queue.Queue[dict]" = queue.Queue()
    _SENTINEL = {"__end__": True}

    def worker():
        try:
            provider = llm.get_provider(model=body.model or None)
            embedder = embeddings.get_embedder()
            out = extend_graph_ontology(
                body.workdir, body.corpus_path, provider, embedder,
                sim=body.sim, max_files=body.max_files,
                chunk_chars=body.chunk_chars, concurrency=body.concurrency,
                progress=lambda ev: q.put(ev))
            snap = ontology_snapshot(out)
            q.put({"phase": "result", "stats": snap["stats"], "snapshot": snap})
        except Exception as exc:  # noqa: BLE001
            q.put({"phase": "error", "message": str(exc)})
        finally:
            q.put(_SENTINEL)

    def lines() -> Iterator[str]:
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        pending_done = None
        while True:
            ev = q.get()
            if ev is _SENTINEL:
                break
            phase = ev.get("phase")
            if phase == "done":
                pending_done = ev
            elif phase == "result":
                merged = dict(pending_done or {"phase": "done"})
                merged["snapshot"] = ev["snapshot"]
                merged["stats"] = ev["stats"]
                yield json.dumps(merged) + "\n"
                pending_done = None
            else:
                yield json.dumps(ev) + "\n"
        t.join()
    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/api/wizard/extract")
def wizard_extract(body: WizardExtractBody) -> dict:
    """Non-streaming KEE demo (consumes the same event generator to its result)."""
    for event in _extract_events(body.path, body.model, body.sim):
        if event["type"] == "error":
            raise HTTPException(400, event["message"])
        if event["type"] == "done":
            return event["result"]
    raise HTTPException(500, "extraction produced no result")


@app.post("/api/wizard/extract/stream")
def wizard_extract_stream(body: WizardExtractBody) -> StreamingResponse:
    """Streaming KEE demo: NDJSON progress events (step/phase/chunk/token/done)."""
    def lines() -> Iterator[str]:
        for event in _extract_events(body.path, body.model, body.sim):
            yield json.dumps(event) + "\n"
    return StreamingResponse(lines(), media_type="application/x-ndjson")


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


class OntologyOpenBody(BaseModel):
    workdir: str


@app.post("/api/ontology/open")
def ontology_open(body: OntologyOpenBody) -> dict:
    """Open a persistent extraction graph as the active KG (powers graph queries)."""
    r = bridge.call("open_extraction_graph", path=body.workdir)
    _api_error(r)
    return r


@app.get("/api/ontology/snapshot")
def ontology_snapshot_ep(workdir: str) -> dict:
    """Graph snapshot (nodes/edges/stats) for the current persistent ontology."""
    from .ontology_view import ontology_snapshot
    r = bridge.call("export_ontology", path=workdir)
    _api_error(r)
    return ontology_snapshot(r)


@app.get("/api/ontology/export")
def ontology_export_ep(workdir: str, out_path: Optional[str] = None) -> dict:
    """Export the candidate-ontology YAML for the persistent graph."""
    r = bridge.call("export_ontology", path=workdir, out_path=out_path)
    _api_error(r)
    return r


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
