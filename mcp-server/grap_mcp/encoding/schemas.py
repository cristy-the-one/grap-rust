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
            ("r", "runs", ["run_id", "kind", "status", "gaps_found"], [1, 2]),
        ],
    },
    "query_events": {
        "enc_id": "qe1",
        "scalars": ["status", "count"],
        "tables": [
            ("e", "events", ["ts", "type", "subject"], [1, 2]),  # type and subject often repeat
        ],
    },
    # Columns must match the gap dicts produced by reasoner.detect_gaps.
    "detect_gaps": {
        "enc_id": "dg2",
        "scalars": ["status", "run_id", "kind", "scope", "gaps_found"],
        "tables": [
            ("g", "gaps",
             ["outcome_id", "outcome_name", "missing_characteristic", "confidence"],
             [0, 1]),
        ],
    },
    # get_blast_radius / find_orphans / find_cycles have NO schema on purpose:
    # they return plain int lists (and cycles: lists of lists), which the
    # row-of-dicts table encoder cannot represent. Without a schema they fall
    # back to JSON even when compact is requested — correct output beats a
    # broken encoding. Add schemas only together with encoder support for
    # scalar-list tables.
}
