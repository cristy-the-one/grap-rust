"""Per-tool wire schemas: (tag, response-key, columns, intern-column indices).

Only tools whose response maps cleanly to flat tables are listed. Tools with
nested fields (e.g. query_events.links) are intentionally omitted until nested
encoding is designed.
"""
SCHEMAS = {
    "graph_neighbors": {
        "enc_id": "gn1",
        "scalars": ["status", "center", "total", "returned", "next_cursor"],
        "tables": [
            # `kind` repeats (entity/act) → intern index 2
            ("n", "neighbors", ["id", "label", "kind", "title"], [2]),
            # `type` repeats (cites/mentions) → intern index 2
            ("e", "edges", ["source", "target", "type"], [2]),
        ],
    },
    "list_runs": {
        "enc_id": "lr1",
        "scalars": ["status", "count"],
        "tables": [
            ("r", "runs", ["run_id", "kind", "status", "gaps_found", "activation_id"], []),
        ],
    },
    "query_events": {
        "enc_id": "qe1",
        "scalars": ["status", "count"],
        "tables": [
            ("e", "events", ["ts", "type", "subject"], [1, 2]),  # type and subject often repeat
        ],
    },
    "detect_gaps": {
        "enc_id": "dg1",
        "scalars": ["status", "gaps_found", "scope"],
        "tables": [
            ("g", "gaps", ["outcome_id", "characteristic", "score"], []),
        ],
    },
    "get_blast_radius": {
        "enc_id": "br1",
        "scalars": ["status", "center", "total", "returned", "next_cursor"],
        "tables": [
            ("i", "impacted", ["id"], []),
        ],
    },
    "find_orphans": {
        "enc_id": "fo1",
        "scalars": ["status", "orphan_count", "total", "returned", "next_cursor"],
        "tables": [
            ("o", "orphans", ["id"], []),
        ],
    },
    "find_cycles": {
        "enc_id": "fc1",
        "scalars": ["status", "cycle_count", "total", "returned", "next_cursor"],
        "tables": [
            ("c", "cycles", ["cycle"], []),  # cycles are lists; represent as stringified for wire POC
        ],
    },
}
