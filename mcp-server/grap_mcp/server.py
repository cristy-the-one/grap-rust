"""
grap-mcp-server
Minimal MCP server for the grap engine POC.

This is the starting point for making the narrow-but-vertical ASPICE gap-detection POC real.
"""

from mcp.server.fastmcp import FastMCP
import pygrap
from pathlib import Path
import logging
import os
import uuid
import json
import time

from . import procedure_graph as pg_loader
from .validation import validate_pg
from .diff import diff_pg
from .procedure_graph import _default_resolver
from . import ontology as onto_loader
from . import reasoner
from .publish_validation import validate_for_publish
from .experiments import experiment_summary, quality_metrics
from . import config as config_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grap-mcp")

# Create the MCP server
mcp = FastMCP("Grap Engine")

# In-memory state for the POC
_state = {
    "kg": None,
    "pg": None,                 # Will hold a ProcedureGraph object
    "ws": None,                 # lifecycle Workspace (Rust-backed)
    "ws_root": None,            # workspace root path (for the config store)
    "current_logic_id": None,   # pg_logic_id of the loaded/active PG
    "pg_version": None,         # content hash of the last published PG
    "ontology": None,           # parsed Ontology model
    "ontology_version": None,   # ontology version hash in the workspace
    "explanations": {},         # target -> explanation dict (POC, in-memory)
}


def _get_workspace():
    """Lazily open the lifecycle workspace (env GRAP_WORKSPACE or ./grap-workspace)."""
    if _state["ws"] is None:
        root = os.environ.get("GRAP_WORKSPACE", "./grap-workspace")
        _state["ws"] = pygrap.Workspace.open(root)
        _state["ws_root"] = root
    return _state["ws"]


def _resolve_ws_root():
    """Return the workspace root path, opening the workspace if needed."""
    if _state.get("ws_root") is None:
        _get_workspace()
    return _state["ws_root"]


def _cached_validation(ws, version_hash):
    """Return the cached validation report for a PG version, or None.

    Looks up the latest `validation_run` event linked to the version, fetches the
    linked validation record, and only returns it if it was validated against the
    currently loaded ontology version.
    """
    try:
        events = [e for e in ws.events_linked_to("pg_version", version_hash)
                  if e.event_type == "validation_run"]
        if not events:
            return None
        latest = events[-1]
        record_hash = next((i for (_r, k, i) in latest.links if k == "validation"), None)
        if not record_hash:
            return None
        raw = ws.get_record(record_hash)
        if not raw:
            return None
        report = json.loads(raw)
        if report.get("ontology_version") != _state.get("ontology_version"):
            return None
        return report
    except Exception:
        logger.warning("cached validation lookup failed", exc_info=True)
        return None


# =============================================================================
# P0 Capabilities (Tier 1 for the narrow vertical POC)
# =============================================================================

@mcp.tool()
def load_knowledge_graph(path: str) -> dict:
    """
    Load a Knowledge Graph (with ontology) from disk.
    For the POC this can be a simple on-disk grap store.
    """
    logger.info(f"Loading Knowledge Graph from: {path}")
    try:
        kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(path, 2)
        _state["kg"] = kg
        try:
            _get_workspace().record_event("kg_loaded", path,
                                          details={"node_count": str(kg.node_count())})
        except Exception:
            logger.warning("audit record_event failed for kg_loaded", exc_info=True)
        return {
            "status": "ok",
            "path": path,
            "node_count": kg.node_count(),
            "message": "Knowledge Graph loaded successfully (POC mode)"
        }
    except Exception as e:
        logger.exception("Failed to load KG")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def load_ontology(path: str) -> dict:
    """Load an ASPICE ontology, project it into the KG, and version it."""
    kg = _state.get("kg")
    if not kg:
        return {"status": "error", "message": "Load a Knowledge Graph first."}
    try:
        ontology = onto_loader.load_ontology_from_file(path)
        projection = onto_loader.project_ontology_to_grap(ontology, kg)
        raw = open(path, "r", encoding="utf-8").read()
        version = _get_workspace().publish_ontology(raw, metadata={"version": ontology.version})
        _state["ontology"] = ontology
        _state["ontology_version"] = version.hash
        _get_workspace().record_event("ontology_loaded", path,
                                      details={"outcomes": str(projection["outcomes_projected"])})
        return {
            "status": "ok",
            "path": path,
            "version_hash": version.hash,
            "outcomes_projected": projection["outcomes_projected"],
            "characteristics_projected": projection["characteristics_projected"],
        }
    except Exception as e:
        logger.exception("load_ontology failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def load_procedure_graph(path: str) -> dict:
    """
    Load a Procedure Graph from a .pg.yaml file using the POC flat format.
    """
    logger.info(f"Loading Procedure Graph from: {path}")
    try:
        pg = pg_loader.load_from_file(path)
        _state["pg"] = pg
        _state["current_logic_id"] = pg.name
        try:
            _get_workspace().record_event("pg_loaded", path, details={"name": pg.name})
        except Exception:
            logger.warning("audit record_event failed for pg_loaded", exc_info=True)

        # Attempt early projection into the KG (POC)
        projection_result = {}
        kg = _state.get("kg")
        if kg:
            projection_result = pg_loader.project_to_grap(pg, kg)

        return {
            "status": "ok",
            "path": path,
            "name": pg.name,
            "version": pg.version,
            "step_count": len(pg.steps),
            "projection": projection_result,
            "message": "Procedure Graph loaded, parsed, and projected (POC)"
        }
    except Exception as e:
        logger.exception("Failed to load PG")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def run_procedure(scope: str = "all") -> dict:
    """
    Execute the currently active (or loaded) Procedure Graph against the KG.
    Respects the current activation and frozen weights when present.
    """
    logger.info(f"Running procedure with scope: {scope}")

    activation = None
    logic_id = _state.get("current_logic_id")
    if logic_id and _state.get("ws"):
        activation = _state["ws"].current_production(logic_id)

    pg = _state.get("pg")
    kg = _state.get("kg")

    if not kg:
        return {"status": "error", "message": "No Knowledge Graph loaded."}
    if not pg:
        return {"status": "error", "message": "No Procedure Graph loaded or activated."}

    try:
        node_count = kg.node_count()
        step_count = len(pg.steps) if hasattr(pg, "steps") else 0

        ws = _get_workspace()
        ontology = _state.get("ontology")
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        activation_id = activation.activation_id if activation else None
        pg_version = activation.pg_version if activation else None
        kg_version = (activation.kg_version if activation else None) or _state.get("ontology_version")
        ws.start_run(run_id, kind="production", activation_id=activation_id,
                     pg_version=pg_version, kg_version=kg_version, scope=scope)

        gaps = reasoner.detect_gaps(kg, ontology, pg) if ontology else []
        ws.complete_run(run_id, status="completed", gaps_found=len(gaps),
                        explanation_hashes=[], summary={"scope": scope})

        return {
            "status": "ok",
            "message": "Procedure executed (gap detection wired)",
            "scope": scope,
            "run_id": run_id,
            "activation_id": activation_id,
            "kg_node_count": node_count,
            "pg_step_count": step_count,
            "gaps_found": len(gaps),
            "gap_summary": [f"{g['outcome_id']}:{g['missing_characteristic']}" for g in gaps],
        }
    except Exception as e:
        logger.exception("Execution failed")
        return {"status": "error", "message": str(e)}


def _run_gap_detection(scope: str, kind: str, extra_summary: dict = None) -> dict:
    """Run gap detection as a tracked run (production or experiment)."""
    kg = _state.get("kg")
    ontology = _state.get("ontology")
    if not kg or not ontology:
        return {"status": "error", "message": "KG and ontology must be loaded first."}
    try:
        ws = _get_workspace()
        pg = _state.get("pg")
        logic_id = _state.get("current_logic_id")
        activation = ws.current_production(logic_id) if logic_id else None
        activation_id = activation.activation_id if activation else None
        pg_version = (activation.pg_version if activation else None) or _state.get("pg_version")
        kg_version = (activation.kg_version if activation else None) or _state.get("ontology_version")

        run_id = f"run-{uuid.uuid4().hex[:8]}"
        ws.start_run(run_id, kind=kind, activation_id=activation_id,
                     pg_version=pg_version, kg_version=kg_version, scope=scope)

        if activation is not None:
            weights = dict(activation.frozen_weights)
        else:
            weights = config_store.load_config(_resolve_ws_root())["weights"]
        gaps = reasoner.detect_gaps(kg, ontology, pg, weights=weights)
        explanations = {}
        hashes = []
        for gap in gaps:
            oc = ontology.outcome(gap["outcome_id"])
            exp = reasoner.explain_gap(gap, _state.get("ontology_version"),
                                       expected=oc.description if oc else "")
            explanations[exp["target"]] = exp
            version = ws.record_explanation(json.dumps(exp), metadata={"target": exp["target"]})
            hashes.append(version.hash)
        _state["explanations"] = explanations

        summary = {"scope": scope, "weights": json.dumps(weights)}
        if extra_summary:
            summary.update(extra_summary)
        ws.complete_run(run_id, status="completed", gaps_found=len(gaps),
                        explanation_hashes=hashes, summary=summary)
        return {"status": "ok", "run_id": run_id, "kind": kind, "scope": scope,
                "gaps": gaps, "gaps_found": len(gaps)}
    except Exception as e:
        logger.exception("gap detection failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def detect_gaps(scope: str = "all") -> dict:
    """Detect ASPICE gaps as a production run (persists run + explanations)."""
    return _run_gap_detection(scope, "production")


@mcp.tool()
def run_experiment(scope: str = "all", hypothesis: str = None) -> dict:
    """Run gap detection as a quarantined experiment (full audit parity with production)."""
    return _run_gap_detection(scope, "experiment", experiment_summary(scope, hypothesis))


@mcp.tool()
def validate_procedure_graph(path: str) -> dict:
    """Parse a PG file and validate it (publish-time rules; non-blocking)."""
    try:
        pg = pg_loader.load_from_file(path)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    report = validate_for_publish(pg, _state.get("kg"), _state.get("ontology"))
    return {"status": "ok", "path": path, **report}


@mcp.tool()
def get_validation(version_hash: str) -> dict:
    """Return the cached validation report for a published PG version."""
    report = _cached_validation(_get_workspace(), version_hash)
    if report is None:
        return {"status": "error", "message": "not validated yet (or ontology changed)"}
    return {"status": "ok", "validation": report}


@mcp.tool()
def diff_procedure_graphs(path_a: str, path_b: str) -> dict:
    """Parse two PG files and return their structural diff."""
    try:
        pg_a = pg_loader.load_from_file(path_a)
        pg_b = pg_loader.load_from_file(path_b)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "ok", "diff": diff_pg(pg_a, pg_b)}


@mcp.tool()
def get_explanation(target: str) -> dict:
    """Return the sectioned explanation for a gap target (e.g. 'SYS.5.BP3:acceptance_criteria_defined')."""
    exp = _state.get("explanations", {}).get(target)
    if not exp:
        return {"status": "error", "message": f"No explanation for target '{target}'. Run detect_gaps first."}
    return {"status": "ok", "explanation": exp}


@mcp.tool()
def get_status() -> dict:
    """Developer/debugging tool. Returns current server state."""
    kg = _state.get("kg")
    pg = _state.get("pg")
    logic_id = _state.get("current_logic_id")
    production = None
    if logic_id and _state.get("ws"):
        prod = _state["ws"].current_production(logic_id)
        production = prod.activation_id if prod else None

    return {
        "kg": {
            "loaded": kg is not None,
            "node_count": kg.node_count() if kg else 0,
        },
        "pg": {
            "loaded": pg is not None,
            "name": getattr(pg, "name", None) if pg else None,
            "version": getattr(pg, "version", None) if pg else None,
            "step_count": len(pg.steps) if pg and hasattr(pg, "steps") else 0,
        },
        "lifecycle": {
            "workspace_open": _state.get("ws") is not None,
            "logic_id": logic_id,
            "production_id": production,
        },
    }


@mcp.tool()
def publish_pg(level: str = "draft", rationale: str = None) -> dict:
    """Publish the currently loaded Procedure Graph; runs (non-blocking) validation."""
    pg = _state.get("pg")
    if not pg:
        return {"status": "error", "message": "No Procedure Graph loaded."}
    try:
        ws = _get_workspace()
        raw = open(pg.source_path, "r", encoding="utf-8").read() if pg.source_path else ""
        version = ws.publish_pg(raw, level=level, rationale=rationale,
                                metadata={"name": pg.name, "version": pg.version})
        _state["current_logic_id"] = pg.name
        _state["pg_version"] = version.hash

        report = validate_for_publish(pg, _state.get("kg"), _state.get("ontology"))
        report["ontology_version"] = _state.get("ontology_version")
        ws.record_validation(version.hash, json.dumps(report), report["ok"])

        return {"status": "ok", "version_hash": version.hash, "level": level,
                "validation": {"ok": report["ok"], "error_count": len(report["errors"]),
                               "warning_count": len(report["warnings"])}}
    except Exception as e:
        logger.exception("publish_pg failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def promote_pg(version_hash: str, rationale: str) -> dict:
    """Promote a published PG version (rationale required); refused if validation has errors."""
    try:
        ws = _get_workspace()
        report = _cached_validation(ws, version_hash)
        if report is None:
            # Cold path: reload the PG by hash, re-validate, and cache the result.
            raw = ws.get_record(version_hash)
            if not raw:
                return {"status": "error", "message": f"Unknown version '{version_hash}'."}
            pg = pg_loader.load_from_string(raw)
            report = validate_for_publish(pg, _state.get("kg"), _state.get("ontology"))
            report["ontology_version"] = _state.get("ontology_version")
            ws.record_validation(version_hash, json.dumps(report), report["ok"])

        if report["errors"]:
            return {"status": "error", "message": "validation failed; cannot promote",
                    "validation": report}

        ws.promote(version_hash, rationale)
        return {"status": "ok", "version_hash": version_hash, "level": "promoted",
                "validation": {"ok": True, "warning_count": len(report["warnings"])}}
    except Exception as e:
        logger.exception("promote_pg failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def activate(activation_id: str, version_hash: str, weights: dict = None,
             set_as_production: bool = False, kg_version: str = None) -> dict:
    """Activate a promoted PG version with frozen weights."""
    pg = _state.get("pg")
    logic_id = _state.get("current_logic_id") or (pg.name if pg else None)
    if not logic_id:
        return {"status": "error", "message": "No PG logic id known; load and publish a PG first."}
    try:
        ws = _get_workspace()
        cfg = config_store.load_config(_resolve_ws_root())
        effective_weights = weights if weights is not None else cfg["weights"]
        act = ws.activate(activation_id, logic_id, version_hash,
                          weights=effective_weights, kg_version=kg_version)
        if set_as_production:
            act = ws.set_production(activation_id)
        return {"status": "ok", "activation_id": act.activation_id,
                "is_production": act.is_production,
                "frozen_weights": effective_weights,
                "weights_last_changed": cfg["last_changed"]}
    except Exception as e:
        logger.exception("activate failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def set_production(activation_id: str) -> dict:
    """Make the given activation the production one for its PG logic."""
    try:
        act = _get_workspace().set_production(activation_id)
        return {"status": "ok", "production_id": act.activation_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_production() -> dict:
    """Return the current production activation for the loaded PG logic."""
    logic_id = _state.get("current_logic_id")
    if not logic_id:
        return {"active": False, "message": "No PG logic loaded."}
    act = _get_workspace().current_production(logic_id)
    if not act:
        return {"active": False, "message": f"No production activation for '{logic_id}'."}
    return {"active": True, "activation_id": act.activation_id,
            "pg_version": act.pg_version, "is_production": act.is_production}


@mcp.tool()
def list_versions() -> dict:
    """List published PG versions in the workspace."""
    try:
        versions = _get_workspace().list_versions()
        return {"versions": [{"hash": v.hash, "kind": v.kind} for v in versions]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_audit_log() -> dict:
    """Return the workspace audit event log."""
    try:
        events = _get_workspace().read_audit()
        return {"events": [{"ts": e.ts, "type": e.event_type, "subject": e.subject,
                            "rationale": e.rationale} for e in events]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def list_runs(kind: str = None) -> dict:
    """List execution runs, optionally filtered by kind (production|experiment)."""
    try:
        runs = _get_workspace().list_runs(kind)
        return {"runs": [{"run_id": r.run_id, "kind": r.kind, "status": r.status,
                          "gaps_found": r.gaps_found} for r in runs]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_run(run_id: str) -> dict:
    """Return a single run record."""
    try:
        r = _get_workspace().get_run(run_id)
        if not r:
            return {"status": "error", "message": f"Run '{run_id}' not found."}
        return {"status": "ok", "run_id": r.run_id, "kind": r.kind, "status_": r.status,
                "gaps_found": r.gaps_found, "pg_version": r.pg_version,
                "kg_version": r.kg_version, "explanation_hashes": r.explanation_hashes}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def reconstruct_run(run_id: str) -> dict:
    """Reconstruct a past run, reporting any missing frozen artifacts."""
    try:
        rep = _get_workspace().reconstruct(run_id)
        return {"status": "ok", "run_id": rep.run_id, "complete": rep.complete,
                "pg_version_present": rep.pg_version_present,
                "kg_version_present": rep.kg_version_present,
                "explanations_present": rep.explanations_present,
                "explanations_total": rep.explanations_total, "missing": rep.missing}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def query_events(event_type: str = None, run_id: str = None) -> dict:
    """Query the Temporal Graph: by event_type, or all events for a run_id."""
    try:
        ws = _get_workspace()
        if run_id:
            events = ws.events_for_run(run_id)
        else:
            events = ws.query_events(event_type)
        return {"events": [{"ts": e.ts, "type": e.event_type, "subject": e.subject,
                            "links": [{"relation": r, "kind": k, "id": i} for (r, k, i) in e.links]}
                           for e in events]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_quality_metrics() -> dict:
    """Production-only quality view; experiment runs are excluded (quarantine boundary)."""
    try:
        return {"status": "ok", **quality_metrics(_get_workspace())}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def adopt_experiment(run_id: str, activation_id: str, rationale: str) -> dict:
    """Promote an experiment's PG version into production via the gated lifecycle."""
    try:
        run = _get_workspace().get_run(run_id)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    if not run:
        return {"status": "error", "message": f"Run '{run_id}' not found."}
    if run.kind != "experiment":
        return {"status": "error", "message": f"Run '{run_id}' is not an experiment."}
    if not run.pg_version:
        return {"status": "error", "message": "Experiment run has no frozen PG version to adopt."}

    promo = promote_pg(run.pg_version, rationale)
    if promo["status"] != "ok":
        return promo  # validation gate / rationale enforcement surfaced as-is

    act = activate(activation_id, run.pg_version, set_as_production=True)
    if act["status"] != "ok":
        return act
    return {"status": "ok", "activation_id": activation_id,
            "version_hash": run.pg_version, "promoted": True, "is_production": True}


@mcp.tool()
def get_config() -> dict:
    """Return the governed configuration (weights + retention + last_changed)."""
    cfg = config_store.load_config(_resolve_ws_root())
    return {"status": "ok", "weights": cfg["weights"], "retention": cfg["retention"],
            "last_changed": cfg["last_changed"]}


@mcp.tool()
def set_weight(name: str, value: float, rationale: str) -> dict:
    """Set a prioritization weight (rationale required; audited)."""
    if not rationale:
        return {"status": "error", "message": "rationale is required"}
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return {"status": "error", "message": "value must be a number"}
    root = _resolve_ws_root()
    cfg = config_store.load_config(root)
    cfg["weights"][name] = float(value)
    cfg["last_changed"] = int(time.time() * 1000)
    config_store.save_config(root, cfg)
    _get_workspace().record_event("config_changed", f"weight:{name}",
                                  details={"value": str(value)}, rationale=rationale)
    return {"status": "ok", "weights": cfg["weights"], "last_changed": cfg["last_changed"]}


@mcp.tool()
def set_retention(key: str, days: int, rationale: str) -> dict:
    """Set a retention-policy value in days (rationale required; audited; not enforced)."""
    if not rationale:
        return {"status": "error", "message": "rationale is required"}
    if isinstance(days, bool) or not isinstance(days, int):
        return {"status": "error", "message": "days must be an integer"}
    root = _resolve_ws_root()
    cfg = config_store.load_config(root)
    cfg["retention"][key] = int(days)
    cfg["last_changed"] = int(time.time() * 1000)
    config_store.save_config(root, cfg)
    _get_workspace().record_event("config_changed", f"retention:{key}",
                                  details={"days": str(days)}, rationale=rationale)
    return {"status": "ok", "retention": cfg["retention"], "last_changed": cfg["last_changed"]}


@mcp.tool()
def enforce_retention() -> dict:
    """Purge runs (+ orphaned explanations) older than the configured retention windows."""
    try:
        ws = _get_workspace()
        retention = config_store.load_config(_resolve_ws_root())["retention"]
        now_ms = int(time.time() * 1000)
        day_ms = 86_400_000
        prod_cutoff = max(0, now_ms - int(retention.get("production_days", 365)) * day_ms)
        exp_cutoff = max(0, now_ms - int(retention.get("experiment_days", 30)) * day_ms)
        rep = ws.enforce_retention(prod_cutoff, exp_cutoff)
        return {"status": "ok", "runs_deleted": rep.runs_deleted,
                "explanations_deleted": rep.explanations_deleted, "run_ids": rep.run_ids}
    except Exception as e:
        logger.exception("enforce_retention failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def list_capabilities() -> dict:
    """Return the MCP capability taxonomy grouped by domain."""
    return {
        "domains": {
            "knowledge": ["load_knowledge_graph", "load_ontology"],
            "pg_lifecycle": ["load_procedure_graph", "publish_pg", "promote_pg",
                             "activate", "set_production", "get_production", "list_versions"],
            "execution_reasoning": ["run_procedure", "detect_gaps", "get_explanation"],
            "experimentation": ["run_experiment", "get_quality_metrics", "adopt_experiment"],
            "governance": ["get_config", "set_weight", "set_retention"],
            "validation": ["validate_procedure_graph", "diff_procedure_graphs", "get_validation"],
            "audit": ["get_audit_log", "get_status", "list_capabilities",
                      "list_runs", "get_run", "reconstruct_run", "query_events"],
        },
        "auth": "deferred (POC: no authentication/authorization)",
    }


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Run the MCP server."""
    logger.info("Starting grap-mcp-server (POC mode)")
    mcp.run()


if __name__ == "__main__":
    main()
