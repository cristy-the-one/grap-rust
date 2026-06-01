"""
grap-mcp-server
Minimal MCP server for the grap engine POC.

This is the starting point for making the narrow-but-vertical ASPICE gap-detection POC real.
"""

from mcp.server.fastmcp import FastMCP
import pygrap
from pathlib import Path
import logging

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
    "activations": {},          # activation_id -> Activation dict
    "current_activation_id": None,
}

class Activation:
    """Simple activation record for the POC."""
    def __init__(self, activation_id: str, pg, weights: dict, kg_snapshot: dict = None):
        self.activation_id = activation_id
        self.pg = pg
        self.weights = weights or {}
        self.kg_snapshot = kg_snapshot or {}
        self.is_production = False

    def to_dict(self):
        return {
            "activation_id": self.activation_id,
            "pg_name": getattr(self.pg, "name", "unknown"),
            "pg_version": getattr(self.pg, "version", "unknown"),
            "weights": self.weights,
            "is_production": self.is_production,
        }



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
    if _state.get("current_activation_id"):
        activation = _state["activations"].get(_state["current_activation_id"])

    pg = activation.pg if activation else _state.get("pg")
    kg = _state.get("kg")

    if not kg:
        return {"status": "error", "message": "No Knowledge Graph loaded."}
    if not pg:
        return {"status": "error", "message": "No Procedure Graph loaded or activated."}

    # POC stub — real execution + gap detection coming next
    try:
        node_count = kg.node_count()
        step_count = len(pg.steps) if hasattr(pg, "steps") else 0

        weights = activation.weights if activation else {}

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
    """
    Developer / debugging tool.
    Returns the current state of the MCP server (loaded graphs, activations, etc.).
    Very useful during POC development.
    """
    kg = _state.get("kg")
    pg = _state.get("pg")
    current_id = _state.get("current_activation_id")
    current_act = _state["activations"].get(current_id) if current_id else None

    return {
        "kg": {
            "loaded": kg is not None,
            "node_count": kg.node_count() if kg else 0,
            "path": getattr(kg, "path", None) if kg else None,
        },
        "pg": {
            "loaded": pg is not None,
            "name": getattr(pg, "name", None) if pg else None,
            "version": getattr(pg, "version", None) if pg else None,
            "step_count": len(pg.steps) if pg and hasattr(pg, "steps") else 0,
        },
        "activations": {
            "count": len(_state["activations"]),
            "current_id": current_id,
            "production_id": next(
                (aid for aid, a in _state["activations"].items() if a.is_production), None
            ),
        },
        "current_activation": current_act.to_dict() if current_act else None,
    }


@mcp.tool()
def list_activations() -> dict:
    """List all current activations with basic info. Useful for debugging."""
    result = []
    for aid, act in _state["activations"].items():
        result.append({
            "activation_id": aid,
            "pg_name": getattr(act.pg, "name", "unknown"),
            "weights": act.weights,
            "is_production": act.is_production,
        })
    return {"activations": result, "count": len(result)}


@mcp.tool()
def get_current_activation() -> dict:
    """Return details about the currently active activation (if any)."""
    current_id = _state.get("current_activation_id")
    if not current_id:
        return {"active": False, "message": "No activation is currently active."}

    act = _state["activations"].get(current_id)
    if not act:
        return {"active": False, "message": f"Current activation ID {current_id} not found."}

    return {
        "active": True,
        "activation": act.to_dict(),
    }


@mcp.tool()
def set_production(activation_id: str) -> dict:
    """
    Explicitly set which activation is the production one.
    Useful for switching production without creating a new activation.
    """
    logger.info(f"Setting production to activation_id={activation_id}")

    if activation_id not in _state["activations"]:
        return {"status": "error", "message": f"Activation '{activation_id}' not found."}

    # Clear previous production flag
    for act in _state["activations"].values():
        act.is_production = False

    _state["activations"][activation_id].is_production = True
    _state["current_activation_id"] = activation_id

    return {
        "status": "ok",
        "production_id": activation_id,
        "message": "Production flag moved successfully."
    }


@mcp.tool()
def get_current_activation() -> dict:
    """Return details about the currently active activation (if any)."""
    current_id = _state.get("current_activation_id")
    if not current_id:
        return {"active": False, "message": "No activation is currently active."}

    act = _state["activations"].get(current_id)
    if not act:
        return {"active": False, "message": f"Current activation ID {current_id} not found."}

    return {
        "active": True,
        "activation": act.to_dict(),
    }


@mcp.tool()
def activate(
    activation_id: str,
    weights: dict = None,
    set_as_production: bool = False
) -> dict:
    """
    Activate a loaded Procedure Graph with a specific set of (frozen) weights.
    This is a key P0 concept for the POC.
    """
    logger.info(f"Activating PG with id={activation_id}")

    if not _state.get("pg"):
        return {"status": "error", "message": "No Procedure Graph loaded."}

    pg = _state["pg"]
    kg = _state.get("kg")

    # Capture a lightweight snapshot of the current KG (POC version)
    kg_snapshot = {}
    if kg:
        kg_snapshot = {
            "node_count": kg.node_count(),
            "path": getattr(kg, "path", None),
        }

    activation = Activation(
        activation_id=activation_id,
        pg=pg,
        weights=weights or {},
        kg_snapshot=kg_snapshot,
    )

    # If setting as production, clear previous production flag
    if set_as_production:
        for act in _state["activations"].values():
            act.is_production = False
        activation.is_production = True

    _state["activations"][activation_id] = activation
    _state["current_activation_id"] = activation_id

    return {
        "status": "ok",
        "activation": activation.to_dict(),
        "message": "Activation created successfully (POC mode)"
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
