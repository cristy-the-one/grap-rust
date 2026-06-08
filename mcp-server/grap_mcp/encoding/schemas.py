"""Per-tool wire schemas: (tag, response-key, columns, intern-column indices)."""
SCHEMAS = {
    "graph_neighbors": {
        "enc_id": "gn1",
        "scalars": ["count"],
        "tables": [("n", "neighbors", ["node_id", "relation"], [1])],
    },
    "query_events": {
        "enc_id": "qe1",
        "scalars": ["count"],
        "tables": [("e", "events", ["id", "event_type", "subject", "ts"], [1])],
    },
    "legal_graph_from_relations": {
        "enc_id": "lg1",
        "scalars": ["node_count", "edge_count"],
        "tables": [("g", "edges", ["source", "target", "relation", "strength"], [2])],
    },
}
