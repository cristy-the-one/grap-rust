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

        # 12. fusion_mode + rrf_k + centrality_weight + channel_ranks
        # Add a few more text-searchable nodes so the hybrid search has something to rank
        m1 = g.add_node("memory safety in rust", label="ms1")
        m2 = g.add_node("memory safety guarantees", label="ms2")
        g.add_edge(m1, m2, 0.8, {"relation_type": "related"})

        hits = g.search_and_traverse_hybrid(
            "memory safety", vector=None, max_seeds=5, required_properties=[],
            min_strength=0.0, max_depth=2, max_results=10,
            text_weight=1.0, vector_weight=0.6, graph_weight=0.5,
            centrality_weight=0.4, fusion_mode="rrf", rrf_k=60.0,
        )
        assert hits, "expected results from rrf search"
        print("channel_ranks:", hits[0].channel_ranks)

        # also confirm linear mode still works
        hits_lin = g.search_and_traverse_hybrid(
            "memory safety", vector=None, max_seeds=5, required_properties=[],
            min_strength=0.0, max_depth=2, max_results=10,
            fusion_mode="linear",
        )
        assert hits_lin, "expected linear results"
        assert hits_lin[0].channel_ranks == [], "linear mode should not populate channel_ranks"
        print(f"linear fusion returned {len(hits_lin)} results, channel_ranks={hits_lin[0].channel_ranks!r}")
        print("fusion_mode + rrf_k + centrality_weight + channel_ranks OK")

        # 13. graph analytics: blast_radius, find_cycles, find_orphans, health
        # Build a small chain: a2 -> b2 -> c2
        a2 = g.add_node("analytics node a", label="an-a")
        b2 = g.add_node("analytics node b", label="an-b")
        c2 = g.add_node("analytics node c", label="an-c")
        g.add_edge(a2, b2, 1.0)
        g.add_edge(b2, c2, 1.0)

        # blast_radius of c2 should include a2 and b2 (transitive dependents)
        br = g.blast_radius(c2)
        assert a2 in br, f"Expected a2={a2} in blast_radius({c2}), got {br}"
        assert b2 in br, f"Expected b2={b2} in blast_radius({c2}), got {br}"
        print(f"blast_radius OK: {br}")

        # No cycles in a simple chain
        cycles = g.find_cycles()
        assert cycles == [], f"Expected no cycles, got {cycles}"
        print(f"find_cycles OK: {cycles}")

        # find_orphans with explicit entry_points = [a2]: c2 should be reachable, nothing orphaned
        orphans_explicit = g.find_orphans(entry_points=[a2])
        assert c2 not in orphans_explicit, f"c2 should be reachable from a2, orphans={orphans_explicit}"
        print(f"find_orphans (explicit) OK: {orphans_explicit}")

        # health report
        h = g.health()
        assert isinstance(h, dict), f"Expected dict, got {type(h)}"
        assert h["live_nodes"] >= 3, f"Expected >= 3 live nodes, got {h['live_nodes']}"
        assert h["cycle_count"] == 0, f"Expected 0 cycles, got {h['cycle_count']}"
        assert "top_central" in h, "Missing top_central in health report"
        print(f"health OK: live_nodes={h['live_nodes']}, cycle_count={h['cycle_count']}, top_central count={len(h['top_central'])}")

    # 14. remove_node / is_node_deleted
        _rm = g.add_node(text="to be removed", label="tmp")
        assert g.remove_node(_rm) is True, "remove_node should return True for a live node"
        assert g.is_node_deleted(_rm) is True, "node should be tombstoned after remove_node"
        print("remove_node OK")

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

    # 15. ShardedGraph: search_and_traverse_hybrid with fusion controls
    with tempfile.TemporaryDirectory() as shard_tmpdir:
        shard_path = os.path.join(shard_tmpdir, "sharded-graph")
        sg = pygrap.ShardedGraph(shard_path, 2)
        s1 = sg.add_node("rust memory safety", label="s1", embedding=[1.0, 0.0, 0.0])
        s2 = sg.add_node("memory safety concepts", label="s2", embedding=[0.9, 0.1, 0.0])
        s3 = sg.add_node("unrelated node xyz", label="s3", embedding=[0.0, 0.0, 1.0])
        sg.add_edge(s1, s2, 0.8)
        sg.add_edge(s2, s3, 0.5)

        # RRF mode (default)
        rrf_results = sg.search_and_traverse_hybrid(
            "memory safety",
            vector=None,
            max_seeds=5,
            max_results=10,
            fusion_mode="rrf",
            rrf_k=60.0,
            centrality_weight=0.4,
        )
        assert isinstance(rrf_results, list), "Expected a list from sharded rrf search"
        print(f"ShardedGraph rrf search returned {len(rrf_results)} results")

        # Linear mode
        lin_results = sg.search_and_traverse_hybrid(
            "memory safety",
            vector=None,
            max_seeds=5,
            max_results=10,
            fusion_mode="linear",
            centrality_weight=0.2,
        )
        assert isinstance(lin_results, list), "Expected a list from sharded linear search"
        print(f"ShardedGraph linear search returned {len(lin_results)} results")

        # traversal_weight_property pass-through (no exception expected)
        twp_results = sg.search_and_traverse_hybrid(
            "memory",
            traversal_weight_property="confidence",
            fusion_mode="rrf",
        )
        assert isinstance(twp_results, list), "Expected a list from sharded twp search"
        print(f"ShardedGraph traversal_weight_property pass-through OK ({len(twp_results)} results)")

    print("ShardedGraph fusion controls OK")

    # 13. wire.encode_tables
    wire = pygrap.wire.encode_tables(
        tool="graph_neighbors", enc_id="gn1",
        scalars=[("count", 2)],
        tables=[("n", "neighbors", ["node_id", "relation"], [1],
                 [[7, "cites"], [9, "cites"]])],
    )
    assert wire.startswith("#MUNCH/1 tool=graph_neighbors enc=gn1"), wire
    assert "@1=cites" in wire, wire
    print("wire encode_tables OK:\n" + wire)

    # --- open_mmap: read-mostly memory-mapped open ---
    _mm_dir = tempfile.mkdtemp(prefix="pygrap-mmap-")
    _mg = pygrap.IndexedKnowledgeGraph.with_merge_policy(_mm_dir, 2)
    _r = _mg.add_node(text="brake-by-wire redundancy requirement", label="REQ-1")
    _s = _mg.add_node(text="brake pedal position signal", label="SIG-1")
    _mg.add_edge(_r, _s, 0.9, properties={"relation_type": "cites"})
    _mg.save_to_dir(_mm_dir)
    del _mg  # release the writable handle before mapping the same dir
    _mm = pygrap.IndexedKnowledgeGraph.open_mmap(_mm_dir)
    assert _mm.get_node_text(_r) == "brake-by-wire redundancy requirement", _mm.get_node_text(_r)
    hits = _mm.search_and_traverse_hybrid("brake", vector=None, max_seeds=5, max_results=10)
    assert hits is not None, "open_mmap search returned None"
    print("open_mmap OK:", len(hits), "hits; first node text read zero-copy from the map")

    print("\nAll smoke tests passed!")

if __name__ == "__main__":
    main()
