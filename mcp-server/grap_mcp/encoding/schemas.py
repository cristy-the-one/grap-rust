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
}
