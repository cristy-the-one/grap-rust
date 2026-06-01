"""End-to-end ASPICE gap-detection demo.

Drives the *real* grap MCP server over stdio and narrates the full vertical:
knowledge + ontology + procedure graph, validation-gated publish/activation with
governed weights, hybrid gap detection + explanations, quarantined experiments,
the Temporal Graph + reconstruction, retention enforcement, and role-based auth.

Run:
    cd mcp-server && python demo_aspice_e2e.py

Prerequisites: pygrap installed (maturin), and the `mcp` SDK (a project dependency).
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pygrap
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent          # mcp-server/
REPO = HERE.parent
ONTOLOGY = REPO / "docs" / "examples" / "minimal-aspice-ontology.yaml"

PG_YAML = """name: Brake Verification Procedure
description: Demo procedure for ASPICE gap detection
version: 0.1.0
steps:
  - id: select-requirements
    operation: Select brake requirements in scope
    outputs:
      requirements: list
    references:
      - kg:aspice.process_outcome.SYS.5.BP3
  - id: detect-gaps
    operation: Detect verification-criteria gaps
    inputs:
      requirements: select-requirements.outputs.requirements
    outputs:
      gaps: list
    references:
      - kg:aspice.process_outcome.SYS.5.BP3
"""


def _setup(tmp: str):
    """Build a small KG on disk + write a PG file; return their paths."""
    kg_path = str(Path(tmp) / "kg")
    kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(kg_path, 2)
    kg.add_node("requirement: the brake pedal position signal shall be checked", label="req-1")
    kg.add_node("software unit: brake controller module with an interface", label="req-2")
    kg.save_to_dir(kg_path)
    pg_path = Path(tmp) / "demo.pg.yaml"
    pg_path.write_text(PG_YAML, encoding="utf-8")
    return kg_path, str(pg_path)


def _unwrap(res):
    """Extract a tool result dict from an MCP CallToolResult."""
    sc = getattr(res, "structuredContent", None)
    if isinstance(sc, dict):
        # FastMCP may wrap a non-dict under {"result": ...}; dicts come through as-is.
        return sc.get("result", sc) if set(sc.keys()) == {"result"} else sc
    for c in getattr(res, "content", []) or []:
        text = getattr(c, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return {}


async def call(session, tool, **args):
    res = await session.call_tool(tool, args)
    return _unwrap(res)


def narrate(step, title, why):
    print(f"\n=== {step}. {title} ===")
    print(f"    {why}")


def show(result, *keys):
    if not keys:
        print(f"    -> {result}")
        return
    slim = {k: result.get(k) for k in keys}
    print(f"    -> {slim}")


async def run_demo():
    tmp = tempfile.mkdtemp(prefix="grap-demo-")
    workspace = str(Path(tmp) / "ws")
    kg_path, pg_path = _setup(tmp)

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "grap_mcp.server"],
        cwd=str(HERE),
        env={**os.environ, "GRAP_WORKSPACE": workspace,
             "GRAP_ROLE": "admin", "PYTHONPATH": str(HERE)},
    )

    print(f"grap MCP end-to-end demo (workspace: {workspace})")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            narrate(1, "Capability surface", "Every interaction is an MCP capability.")
            caps = await call(session, "list_capabilities")
            print(f"    domains: {list(caps.get('domains', {}))}")

            narrate(2, "Knowledge Graph", "Load the KG — the source of truth.")
            show(await call(session, "load_knowledge_graph", path=kg_path), "status", "node_count")

            narrate(3, "Ontology", "Project + version the ASPICE ontology into the KG.")
            show(await call(session, "load_ontology", path=str(ONTOLOGY)),
                 "status", "outcomes_projected", "characteristics_projected")

            narrate(4, "Procedure Graph", "Load the PG — the executable process (TG/PG).")
            show(await call(session, "load_procedure_graph", path=pg_path),
                 "status", "name", "step_count")

            narrate(5, "Validation", "Publish-time rules: structure + reference validity.")
            show(await call(session, "validate_procedure_graph", path=pg_path), "status", "ok")

            narrate(6, "Publish (Draft)", "Content-addressed version + stored validation report.")
            pub = await call(session, "publish_pg", level="draft")
            show(pub, "status", "version_hash", "validation")
            vhash = pub["version_hash"]

            narrate(7, "Promote (gated)", "Promotion is refused if validation has errors.")
            show(await call(session, "promote_pg", version_hash=vhash, rationale="reviewed"),
                 "status", "level")

            narrate(8, "Govern + Activate", "Set a weight, then freeze it at activation.")
            show(await call(session, "set_weight", name="lexical_threshold", value=0.85,
                            rationale="demo tuning"), "status", "weights")
            act = await call(session, "activate", activation_id="act-1",
                             version_hash=vhash, set_as_production=True)
            show(act, "status", "is_production", "frozen_weights", "weights_last_changed")

            narrate(9, "Detect gaps + explain", "Hybrid reasoning over KG + ontology (production run).")
            gaps = await call(session, "detect_gaps")
            show(gaps, "status", "run_id", "gaps_found")
            if gaps.get("gaps"):
                g = gaps["gaps"][0]
                target = f"{g['outcome_id']}:{g['missing_characteristic']}"
                exp = await call(session, "get_explanation", target=target)
                sections = list(exp.get("explanation", {}).get("sections", {}))
                print(f"    explanation[{target}] sections: {sections}")

            narrate(10, "Experiment + quarantine", "Experiments are audited but excluded from production quality.")
            exp_run = await call(session, "run_experiment", hypothesis="try a wider scope")
            show(exp_run, "status", "run_id", "kind")
            show(await call(session, "get_quality_metrics"),
                 "production_runs", "experiments_excluded", "last_production_run")

            narrate(11, "Adopt experiment", "Promote experiment learnings through the gated path.")
            show(await call(session, "adopt_experiment", run_id=exp_run["run_id"],
                            activation_id="act-adopted", rationale="experiment looked good"),
                 "status", "is_production")

            narrate(12, "Temporal Graph", "Run records + relational event graph + reconstruction.")
            runs = await call(session, "list_runs")
            print(f"    runs: {[r['run_id'] + ':' + r['kind'] for r in runs.get('runs', [])]}")
            rec = await call(session, "reconstruct_run", run_id=gaps["run_id"])
            show(rec, "complete", "explanations_present", "missing")
            evs = await call(session, "query_events", run_id=gaps["run_id"])
            print(f"    events for {gaps['run_id']}: {sorted({e['type'] for e in evs.get('events', [])})}")

            narrate(13, "Retention enforcement", "Purge experiment runs past a (demo) zero-day window.")
            show(await call(session, "set_retention", key="experiment_days", days=0,
                            rationale="demo purge"), "status")
            show(await call(session, "enforce_retention"), "status", "runs_deleted", "explanations_deleted")

            narrate(14, "Role-based access", "Mutations require a sufficient role.")
            await call(session, "set_role", role="viewer")
            denied = await call(session, "publish_pg")
            print(f"    viewer publish: {denied.get('status')} ({denied.get('message')})")
            await call(session, "set_role", role="admin")

            narrate(15, "Summary", "Final workspace state.")
            versions = await call(session, "list_versions")
            audit = await call(session, "get_audit_log")
            print(f"    versions: {len(versions.get('versions', []))}, "
                  f"audit events: {len(audit.get('events', []))}")

    print("\n[OK] demo complete")


if __name__ == "__main__":
    asyncio.run(run_demo())
