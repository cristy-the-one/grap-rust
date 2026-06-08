"""
Smoke test for the pygrap Phase 1 bindings.
Tests: add_edge, get_node_text, get_node_label, update_node, merge_nodes,
       neighbors_by_relation, predecessors_by_relation, traverse_by_relation,
       MergeReport, TraversalResult, get_node_content_hash, verify_node.
"""
import tempfile, os
import pygrap

def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "test-graph")
        g = pygrap.IndexedKnowledgeGraph(path)

        # 1. Add nodes with embeddings
        a = g.add_node("node a", label="a", embedding=[1.0, 0.0])
        b = g.add_node("node b", label="b", embedding=[0.0, 1.0])
        c = g.add_node("node c", label="c", embedding=[0.5, 0.5])
        print(f"Added nodes: a={a}, b={b}, c={c}")

        # 2. add_edge with properties
        g.add_edge(a, b, 0.9, {"relation_type": "cites", "confidence": 0.85})
        g.add_edge(b, c, 0.7, {"relation_type": "cites", "confidence": 0.70})
        g.add_edge(a, c, 0.5, {"relation_type": "related"})
        print("Edges added")

        # 3. get_node_text
        text_a = g.get_node_text(a)
        assert text_a == "node a", f"Expected 'node a', got {text_a!r}"
        print(f"get_node_text OK: {text_a!r}")

        # 4. get_node_label
        label_a = g.get_node_label(a)
        assert label_a == "a", f"Expected 'a', got {label_a!r}"
        print(f"get_node_label OK: {label_a!r}")

        # 5. neighbors_by_relation
        cites = g.neighbors_by_relation(a, "relation_type", "cites")
        assert len(cites) == 1, f"Expected 1 cite from a, got {cites}"
        assert cites[0][0] == b, f"Expected b={b}, got {cites[0][0]}"
        print(f"neighbors_by_relation OK: {cites}")

        # 6. predecessors_by_relation
        preds = g.predecessors_by_relation(b, "relation_type", "cites")
        assert len(preds) == 1, f"Expected 1 predecessor of b, got {preds}"
        assert preds[0][0] == a, f"Expected a={a}, got {preds[0][0]}"
        print(f"predecessors_by_relation OK: {preds}")

        # 7. traverse_by_relation
        chain = g.traverse_by_relation(a, "relation_type", "cites", depth=3)
        node_ids = [r.node_id for r in chain]
        assert b in node_ids, f"Expected b={b} in chain, got {node_ids}"
        assert c in node_ids, f"Expected c={c} in chain, got {node_ids}"
        for r in chain:
            print(f"  TraversalResult: {r!r}")
        print("traverse_by_relation OK")

        # 8. update_node
        g.update_node(a, "updated node a", embedding=[0.9, 0.1])
        new_text = g.get_node_text(a)
        assert new_text == "updated node a", f"Expected 'updated node a', got {new_text!r}"
        print(f"update_node OK: {new_text!r}")

        # 9. merge_nodes
        report = g.merge_nodes(a, b)
        print(f"merge_nodes report: {report!r}")
        assert isinstance(report, pygrap.MergeReport)
        total = report.edges_redirected + report.edges_merged + report.self_loops_dropped
        assert total > 0, f"Expected some edges processed in merge, got {report!r}"
        print("merge_nodes OK")

        # 10. search_and_traverse_hybrid with traversal_weight_property
        qvec = [0.9, 0.1]
        results = g.search_and_traverse_hybrid(
            "node a",
            vector=qvec,
            text_weight=0.4,
            vector_weight=0.4,
            graph_weight=0.2,
            traversal_weight_property="confidence",
            max_results=5,
        )
        print(f"search_and_traverse_hybrid returned {len(results)} results")

        # 11. get_node_content_hash / verify_node
        fresh_node = g.add_node("hash test node", label="ht")
        h = g.get_node_content_hash(fresh_node)
        assert h is not None, "Expected a content hash, got None"
        assert len(h) == 64, f"Expected 64-char hex string, got len={len(h)}: {h!r}"
        assert all(c in "0123456789abcdef" for c in h), f"Hash is not hex: {h!r}"
        print(f"get_node_content_hash OK: {h[:16]}...")
        v = g.verify_node(fresh_node)
        assert v == "match", f"Expected 'match', got {v!r}"
        print(f"verify_node OK: {v!r}")

    print("\nAll smoke tests passed!")

if __name__ == "__main__":
    main()
