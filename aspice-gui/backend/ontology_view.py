"""Presentation transform: export_ontology dict -> graph snapshot for the UI.

Pure and side-effect-free. Lives in the GUI layer because it is a view concern,
not part of the kee/graph engine.
"""

from __future__ import annotations

from typing import Any, Dict


def ontology_snapshot(export: Dict[str, Any]) -> Dict[str, Any]:
    """Map an export_ontology payload to {nodes, edges, stats}.

    Nodes come from process_outcomes (id, name, characteristics, provenance).
    Edges come from relations, whose endpoint *names* are resolved to concept
    ids; an edge whose endpoint has no matching concept is dropped.
    """
    onto = export.get("ontology", export)  # accept the wrapped or bare dict
    outcomes = onto.get("process_outcomes", [])
    provenance = onto.get("provenance", {})

    name_to_id = {o["name"]: o["id"] for o in outcomes}
    nodes = [
        {
            "id": o["id"],
            "name": o["name"],
            "characteristics": list(o.get("required_characteristics", [])),
            "provenance": list(provenance.get(o["id"], [])),
        }
        for o in outcomes
    ]

    edges = []
    for r in onto.get("relations", []):
        src = name_to_id.get(r.get("from"))
        tgt = name_to_id.get(r.get("to"))
        if src is None or tgt is None:
            continue
        edges.append({"source": src, "type": r.get("type"), "target": tgt,
                      "sources": list(r.get("sources", []))})

    stats = {k: export.get(k, 0) for k in
             ("concepts", "edges", "added", "changed", "removed", "relooks", "parked")}
    stats["concepts"] = stats["concepts"] or len(nodes)
    stats["edges"] = stats["edges"] or len(edges)
    return {"nodes": nodes, "edges": edges, "stats": stats}
