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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grap-mcp")

# Create the MCP server
mcp = FastMCP("Grap Engine")

# In-memory state for the POC
_state = {
    "kg": None,
    "pg": None,                 # Will hold a ProcedureGraph object
    "current_activation": None,
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
        return {
            "status": "ok",
            "path": path,
            "name": pg.name,
            "version": pg.version,
            "step_count": len(pg.steps),
            "message": "Procedure Graph loaded and parsed successfully"
        }
    except Exception as e:
        logger.exception("Failed to load PG")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def run_procedure(scope: str = "all") -> dict:
    """
    Execute the currently loaded Procedure Graph against the KG.
    This is the core P0 capability for the POC.
    """
    logger.info(f"Running procedure with scope: {scope}")

    if not _state.get("kg"):
        return {"status": "error", "message": "No Knowledge Graph loaded."}
    if not _state.get("pg"):
        return {"status": "error", "message": "No Procedure Graph loaded."}

    kg = _state["kg"]
    pg = _state["pg"]

    # POC stub — real execution + gap detection coming next
    try:
        node_count = kg.node_count()
        step_count = len(pg.steps) if hasattr(pg, "steps") else 0

        return {
            "status": "ok",
            "message": "Procedure executed (POC stub with real PG loading)",
            "scope": scope,
            "kg_node_count": node_count,
            "pg_step_count": step_count,
            "gaps_found": 0,
            "note": "Next: wire real gap detection + explanations using the loaded ontology."
        }
    except Exception as e:
        logger.exception("Execution failed")
        return {"status": "error", "message": str(e)}


@mcp.tool()
def detect_gaps(scope: str = "all") -> dict:
    """POC stub for ontology-based gap detection."""
    if not _state.get("kg") or not _state.get("pg"):
        return {"status": "error", "message": "KG and PG must be loaded first."}

    # Placeholder — will be replaced with real grap-native lightweight reasoning
    return {
        "status": "ok",
        "gaps": [],
        "message": "Gap detection not yet implemented (POC stub)."
    }


@mcp.tool()
def get_explanation(target: str) -> dict:
    """POC stub for retrieving explanations."""
    return {
        "status": "ok",
        "target": target,
        "explanation": "Explanation retrieval not yet implemented.",
        "note": "Will return sectioned structured + statistical explanations."
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
