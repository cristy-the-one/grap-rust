#!/usr/bin/env python3
"""Portable, offline, headless tour of *this* version of grap.

One self-contained script: builds a tiny automotive-style knowledge graph in a
throwaway workspace and walks every capability added in the recent phases -
hybrid RRF retrieval, graph analytics, the SQLite entity index, content-hash
integrity, the session journal + context bundle, opt-in telemetry, the compact
#MUNCH wire encoding, and tool tiering - printing each result as it goes.

No web server, no browser, no network, no embeddings model required. It drives
`grap_mcp` in-process (the same way the GUI's bridge does) so it exercises the
real MCP tools, not a mock.

Run it via `scripts/portable-demo.ps1`, or directly:

    set PYTHONPATH=<repo>/mcp-server        (so `grap_mcp` imports)
    python demo/python/portable_demo.py

Requires `pygrap` to be built/installed (see scripts/install.ps1). Everything
else is Python stdlib.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# --- make grap_mcp importable (mirrors aspice-gui/backend/bridge.py) ----------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MCP_ROOT = _REPO_ROOT / "mcp-server"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

# Isolated, throwaway workspace + opt-in telemetry, set BEFORE importing the
# server (the tool tier is read from GRAP_TOOL_TIER at import; the launcher may
# set it - we just report whatever is active).
_WORK = Path(tempfile.mkdtemp(prefix="grap-portable-demo-"))
os.environ.setdefault("GRAP_WORKSPACE", str(_WORK / "workspace"))
os.environ.setdefault("GRAP_TELEMETRY", "1")  # so get_telemetry has something to show

from grap_mcp.application import GrapApplication

srv = GrapApplication()


# --- tiny pretty-printing helpers --------------------------------------------
def section(n: int, title: str) -> None:
    print(f"\n{'=' * 72}\n  {n}. {title}\n{'=' * 72}")


def show(label: str, value) -> None:
    import json

    if isinstance(value, (dict, list)):
        text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    else:
        text = str(value)
    if len(text) > 1400:
        text = text[:1400] + "\n  ... (truncated)"
    print(f"-> {label}:\n{text}")


def ok(result: dict, what: str) -> dict:
    """Print a one-line status for a tool call and return the result."""
    status = result.get("status", "ok") if isinstance(result, dict) else "ok"
    print(f"   [{status}] {what}")
    return result


def main() -> int:
    kg_dir = str(_WORK / "kg")
    print("grap - portable demo")
    print(f"workspace : {_WORK}")
    print(f"python    : {sys.executable}")

    # 0. Orientation + active tool tier ---------------------------------------
    section(0, "grap_guide - orientation & active tool tier")
    guide = srv.grap_guide()
    print(f"   active tier: {guide['active_tier']}  "
          f"(set GRAP_TOOL_TIER=core|standard|full to change the tool surface)")
    show("core tools", guide["tiers"]["core"])

    # 1. Create a fresh KG -----------------------------------------------------
    section(1, "create_knowledge_graph")
    ok(srv.create_knowledge_graph(kg_dir), f"created on-disk KG at {kg_dir}")
    kg = srv._state["kg"]  # the live pygrap IndexedKnowledgeGraph the tools use

    # 2. Build a small, deterministic automotive-style graph (engine API) ------
    #    Requirements / signals / test-concepts + a deliberate cycle and an
    #    isolated node, so each analytic below has something obvious to show.
    section(2, "build a tiny KG (pygrap add_node / add_edge)")
    req = kg.add_node("REQ-1: The vehicle shall provide brake-by-wire redundancy.", label="REQ-1")
    sig = kg.add_node("SIG-1: Brake_Pedal_Position signal, range 0-100%.", label="SIG-1")
    tc = kg.add_node("TC-1: Validate brake pedal signal range and response time.", label="TC-1")
    tpl = kg.add_node("TPL-1: Signal Validation Template v2.1.", label="TPL-1")
    glo = kg.add_node("GLOSSARY: Redundancy means duplication of critical components.", label="GLOSSARY")
    a = kg.add_node("MOD-A references MOD-B.", label="MOD-A")
    b = kg.add_node("MOD-B references MOD-C.", label="MOD-B")
    c = kg.add_node("MOD-C references MOD-A.", label="MOD-C")

    def cite(s, t):
        kg.add_edge(s, t, 0.9, properties={"relation_type": "cites"})

    def mention(s, t):
        kg.add_edge(s, t, 0.6, properties={"relation_type": "mentions"})

    cite(req, sig)      # the requirement cites the signal
    cite(tc, req)       # the test-concept cites the requirement
    mention(tc, sig)    # ... and mentions the signal
    cite(tc, tpl)       # ... and cites its template
    cite(a, b); cite(b, c); cite(c, a)   # deliberate dependency cycle
    kg.save_to_dir(kg_dir)
    print(f"   built {kg.node_count()} nodes; REQ-1=#{req} SIG-1=#{sig} TC-1=#{tc} "
          f"(cycle MOD-A/B/C; GLOSSARY is isolated)")

    # 3. Document ingestion (incremental, hash-diffed) -------------------------
    section(3, "ingest_documents - chunk + add .md/.txt as evidence nodes")
    corpus = _WORK / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "brake-notes.md").write_text(
        "Brake-by-wire redundancy\n\n"
        "Redundant actuation ensures the brake command survives a single fault.\n\n"
        "The Brake_Pedal_Position signal must be validated for range and latency.",
        encoding="utf-8",
    )
    ok(srv.ingest_documents(str(corpus)), "ingested sample corpus")
    print(f"   KG now has {kg.node_count()} nodes (evidence chunks added)")

    # 4. Hybrid retrieval (RRF fusion) + a budget-packed context bundle --------
    section(4, "get_context_bundle - RRF-ranked, token-budgeted KG context (PG 'fetch context' step)")
    bundle = ok(srv.get_context_bundle("brake redundancy", token_budget=400),
                "retrieved context bundle for 'brake redundancy'")
    for hit in bundle.get("bundle", []):
        print(f"     #{hit['node']:<3} score={hit['score']:<7} {str(hit['label']):<10} "
              f"{hit['text'][:60]!r}")
    print(f"   tokens_used={bundle.get('tokens_used')} / budget={bundle.get('token_budget')} "
          f"truncated={bundle.get('truncated')}")
    # a deliberate miss -> recorded as negative evidence in the session journal
    ok(srv.get_context_bundle("quantum teleportation manual", token_budget=400),
       "queried an off-topic term (expect 0 hits = negative evidence)")

    # 5. SQLite entity index: resolve a name -> node id ------------------------
    section(5, "entity index - reindex_entities + resolve_entity (SQLite sidecar)")
    ok(srv.reindex_entities(), "rebuilt the alias index from node labels")
    res = srv.resolve_entity("req-1")  # case-insensitive
    show("resolve_entity('req-1')", res)

    # 6. Graph analytics over the KG ------------------------------------------
    section(6, "graph analytics - blast radius, cycles, orphans, health")
    show("get_blast_radius(SIG-1) - what transitively depends on the signal",
         srv.get_blast_radius(sig))
    show("find_cycles() - dependency cycles", srv.find_cycles())
    show("find_orphans() - nodes unreachable from any root", srv.find_orphans())
    show("kg_health() - structural snapshot", srv.kg_health())

    # 7. Navigation aid --------------------------------------------------------
    section(7, "suggest_queries - proposed next steps for a node")
    show("suggest_queries(REQ-1)", srv.suggest_queries(req))

    # 8. Content-hash integrity (engine) --------------------------------------
    section(8, "content-hash integrity - verify_node")
    print(f"   verify_node(REQ-1) = {kg.verify_node(req)!r}  "
          f"(SHA-256 of the node's text still matches its stored hash)")

    # 9. Compact #MUNCH wire encoding (+ feeds telemetry) ----------------------
    section(9, "compact wire encoding - #MUNCH payload + token savings")
    from grap_mcp.encoding import encode_response

    neighbors = srv.graph_neighbors(req, types=["cites", "mentions"])
    payload, meta = encode_response("graph_neighbors", neighbors, requested_format="compact")
    print(payload)
    show("encoding meta", meta)

    # 10. Session journal + telemetry -----------------------------------------
    section(10, "session journal + telemetry")
    show("get_session_context() - what this session touched/queried",
         srv.get_session_context())
    show("get_telemetry() - opt-in call/savings ledger (GRAP_TELEMETRY=1)",
         srv.get_telemetry())

    # 11. Capability surface + what tiering actually hides ---------------------
    section(11, "list_capabilities - taxonomy, active tier, and the client-visible surface")
    import asyncio

    caps = srv.list_capabilities()
    taxonomy_total = sum(len(v) for v in caps["domains"].values())
    registered = len(asyncio.run(srv.mcp.list_tools()))  # what a real MCP client sees
    print(f"   active_tier = {caps.get('active_tier')}")
    print(f"   tools a client SEES (registered under this tier) = {registered}")
    print(f"   full capability taxonomy lists = {taxonomy_total} tools "
          f"across {len(caps['domains'])} domains")
    print("   (re-run with GRAP_TOOL_TIER=core to shrink the client-visible surface to "
          "the core set; all functions stay callable in-process either way)")

    print(f"\n{'=' * 72}\n  Done. Artifacts (KG + entity_index.db + audit log) under:\n  {_WORK}\n"
          f"  Re-run with GRAP_TOOL_TIER=core to see the tool surface shrink.\n{'=' * 72}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
