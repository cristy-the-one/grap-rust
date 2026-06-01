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

from . import procedure_graph as pg_loader
from .validation import validate_pg
from .diff import diff_pg
from .procedure_graph import _default_resolver

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grap-mcp")

# Create the MCP server
mcp = FastMCP("Grap Engine")

# In-memory state for the POC
_state = {
    "kg": None,
    "pg": None,                 # Will hold a ProcedureGraph object
    "ws": None,                 # lifecycle Workspace (Rust-backed)
    "current_logic_id": None,   # pg_logic_id of the loaded/active PG
}


def _get_workspace():
    """Lazily open the lifecycle workspace (env GRAP_WORKSPACE or ./grap-workspace)."""
    if _state["ws"] is None:
        root = os.environ.get("GRAP_WORKSPACE", "./grap-workspace")
        _state["ws"] = pygrap.Workspace.open(root)
    return _state["ws"]


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
def load_procedure_graph(path: str) -> dict:
    """
    Load a Procedure Graph from a .pg.yaml file using the POC flat format.
    """
    logger.info(f"Loading Procedure Graph from: {path}")
    try:
        pg = pg_loader.load_from_file(path)
        _state["pg"] = pg
        _state["current_logic_id"] = pg.name

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

    # POC stub — real execution + gap detection coming next
    try:
        node_count = kg.node_count()
        step_count = len(pg.steps) if hasattr(pg, "steps") else 0

        weights = {}

        return {
            "status": "ok",
            "message": "Procedure executed (POC stub)",
            "scope": scope,
            "activation_id": activation.activation_id if activation else None,
            "kg_node_count": node_count,
            "pg_step_count": step_count,
            "weights_used": weights,
            "gaps_found": 0,
            "note": "Real gap detection + explanations coming next."
        }
    except Exception as e:
        logger.exception("Execution failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def detect_gaps(scope: str = "all") -> dict:
    """
    POC version of gap detection.
    Currently very basic — will evolve into real grap-native lightweight reasoning
    using the loaded ontology.
    """
    if not _state.get("kg") or not _state.get("pg"):
        return {"status": "error", "message": "KG and PG must be loaded first."}

    kg = _state["kg"]
    pg = _state["pg"]

    # Extremely naive POC gap detection:
    # In a real implementation this will use the ontology + inference engine.
    try:
        # For now just report that we have the structures needed
        return {
            "status": "ok",
            "gaps": [],
            "message": "Gap detection executed (POC stub)",
            "kg_nodes": kg.node_count(),
            "pg_steps": len(pg.steps) if hasattr(pg, "steps") else 0,
            "note": "Real ontology-based gap detection coming in next iteration."
        }
    except Exception as e:
        logger.exception("Gap detection failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def validate_procedure_graph(path: str) -> dict:
    """Parse a PG file and validate it (structural + cross-KG when a KG is loaded)."""
    try:
        pg = pg_loader.load_from_file(path)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    kg = _state.get("kg")
    resolver = _default_resolver(kg) if kg else None
    report = validate_pg(pg, resolver=resolver)
    return {"status": "ok", "path": path, **report}


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
    """POC stub for retrieving explanations."""
    return {
        "status": "ok",
        "target": target,
        "explanation": "Explanation retrieval not yet implemented.",
        "note": "Will return sectioned structured + statistical explanations."
    }


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
    """Publish the currently loaded Procedure Graph at the given level (draft|promoted)."""
    pg = _state.get("pg")
    if not pg:
        return {"status": "error", "message": "No Procedure Graph loaded."}
    try:
        ws = _get_workspace()
        raw = open(pg.source_path, "r", encoding="utf-8").read() if pg.source_path else ""
        version = ws.publish_pg(raw, level=level, rationale=rationale,
                                metadata={"name": pg.name, "version": pg.version})
        _state["current_logic_id"] = pg.name
        return {"status": "ok", "version_hash": version.hash, "level": level}
    except Exception as e:
        logger.exception("publish_pg failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def promote_pg(version_hash: str, rationale: str) -> dict:
    """Promote a previously published PG version (rationale required)."""
    try:
        _get_workspace().promote(version_hash, rationale)
        return {"status": "ok", "version_hash": version_hash, "level": "promoted"}
    except Exception as e:
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
        act = ws.activate(activation_id, logic_id, version_hash,
                          weights=weights or {}, kg_version=kg_version)
        if set_as_production:
            act = ws.set_production(activation_id)
        return {"status": "ok", "activation_id": act.activation_id,
                "is_production": act.is_production}
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


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Run the MCP server."""
    logger.info("Starting grap-mcp-server (POC mode)")
    mcp.run()


if __name__ == "__main__":
    main()
