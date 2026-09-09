"""Exercise the actual Rust binding used by the harness, without LLM services."""

import pytest
import pygrap


def test_vector_backend_is_visible_and_compaction_preserves_current_values(tmp_path):
    path = str(tmp_path / "vectors")
    graph = pygrap.IndexedKnowledgeGraph(path)
    assert graph.vector_index_stats() == {"backend": "none", "dimension": 0, "vector_count": 0}
    nodes = [graph.add_node(f"rule {i}", embedding=[0.0, 1.0]) for i in range(100)]
    graph.update_node(nodes[0], "updated rule", embedding=[1.0, 0.0])
    graph.remove_node(nodes[1])
    before = graph.vector_index_stats()
    assert before["backend"] in {"brute_force", "hnsw"}
    assert before["dimension"] == 2
    assert before["vector_count"] == 99
    graph.compact_vector_index()
    assert graph.vector_index_stats() == before
    assert graph.vector_search([1.0, 0.0], 1000)[0] == (nodes[0], 1.0)
    graph.save()
    loaded = pygrap.IndexedKnowledgeGraph(path)
    assert loaded.vector_index_stats() == before
    assert loaded.vector_search([1.0, 0.0], 1000)[0] == (nodes[0], 1.0)


def test_centrality_is_explicitly_opt_in(tmp_path):
    graph = pygrap.IndexedKnowledgeGraph(str(tmp_path / "graph"))
    a = graph.add_node("alpha")
    b = graph.add_node("alpha beta")
    graph.add_edge(a, b, 0.9)
    default = graph.search_and_traverse_hybrid("alpha")
    explicit = graph.search_and_traverse_hybrid("alpha", centrality_weight=0.4)
    assert default and explicit
    assert all("centrality" not in dict(hit.channel_ranks) for hit in default)
    assert all("centrality" in dict(hit.channel_ranks) for hit in explicit)


def test_mutation_search_and_reload_agree(tmp_path):
    path = str(tmp_path / "graph")
    graph = pygrap.IndexedKnowledgeGraph(path)
    node = graph.add_node("obsolete amber violet")
    graph.update_node(node, "fresh indigo", embedding=[1.0, 0.0])
    assert graph.search_and_traverse("obsolete") == []
    assert graph.search_and_traverse("fresh")[0].node == node
    assert graph.vector_search([1.0, 0.0], 10)[0][0] == node
    graph.save()

    loaded = pygrap.IndexedKnowledgeGraph(path)
    assert loaded.search_and_traverse("obsolete") == []
    assert loaded.search_and_traverse("fresh")[0].node == node
    assert loaded.remove_node(node)
    assert loaded.vector_search([1.0, 0.0], 10) == []
    assert loaded.search_and_traverse("fresh") == []
    loaded.save()
    reopened = pygrap.IndexedKnowledgeGraph(path)
    assert reopened.search_and_traverse("fresh") == []
    assert reopened.vector_search([1.0, 0.0], 10) == []


def test_relation_traversal_uses_strongest_bounded_path(tmp_path):
    graph = pygrap.IndexedKnowledgeGraph(str(tmp_path / "graph"))
    start, bridge, target = [graph.add_node(text) for text in ("seed", "bridge", "target")]
    graph.add_edge(start, target, 0.1, {"relation": "cites"})
    graph.add_edge(start, bridge, 0.9, {"relation": "cites"})
    graph.add_edge(bridge, target, 0.9, {"relation": "cites"})
    hits = graph.traverse_by_relation(start, "relation", "cites", depth=2)
    hit = next(hit for hit in hits if hit.node_id == target)
    assert hit.strength == pytest.approx(0.81)
    assert hit.depth == 2
    assert graph.traverse_by_relation(start, "missing", "cites") == []
    assert graph.traverse_by_relation(start, "relation", "cites", max_results=0) == []
    assert graph.search_and_traverse("seed", required_properties=["missing"]) == []
    assert graph.search_and_traverse("seed", max_results=0) == []
    assert graph.traverse_by_relation(2**32 - 1, "relation", "cites") == []


@pytest.mark.parametrize("fusion_mode", ["rrf", "linear"])
def test_tied_retrieval_survives_native_save_and_reload(tmp_path, fusion_mode):
    """The harness's default exact vector backend must resolve top-k ties early."""
    path = str(tmp_path / "graph")
    graph = pygrap.IndexedKnowledgeGraph(path)
    nodes = [graph.add_node(f"legal concept {i}", embedding=[1.0, 0.0]) for i in range(16)]

    def check(current):
        assert [node for node, _ in current.vector_search([1.0, 0.0], 4)] == nodes[:4]
        hits = current.search_and_traverse_hybrid(
            "", vector=[1.0, 0.0], max_seeds=4, max_depth=0,
            min_strength=0.0, max_results=4, fusion_mode=fusion_mode,
        )
        assert [hit.node for hit in hits] == nodes[:4]
        return [(hit.node, hit.fused_score, hit.channel_ranks) for hit in hits]

    before = check(graph)
    graph.save()
    assert check(pygrap.IndexedKnowledgeGraph(path)) == before


@pytest.mark.parametrize("sharded", [False, True])
def test_corrupt_saved_graph_is_not_silently_replaced(tmp_path, sharded):
    path = tmp_path / "graph"
    create = (lambda: pygrap.ShardedGraph(str(path), 3)) if sharded else (lambda: pygrap.IndexedKnowledgeGraph(str(path)))
    graph = create()
    graph.add_node("preserve this legal evidence")
    graph.save()
    corrupt = path / "ghost_edges.json" if sharded else path / "text_index" / "manifest.json"
    corrupt.write_text("broken", encoding="utf-8")
    with pytest.raises(pygrap.GraphError):
        create()
    assert corrupt.read_text(encoding="utf-8") == "broken"


def test_native_sharded_paths_and_rebalance_survive_reload(tmp_path):
    path = str(tmp_path / "shards")
    graph = pygrap.ShardedGraph(path, 3)
    nodes = [graph.add_node(text, embedding=[1.0, float(i)]) for i, text in enumerate(["uniqueseed", "bridge", "target"])]
    graph.add_edge(nodes[0], nodes[2], 0.1, {"cites": True})
    graph.add_edge(nodes[0], nodes[1], 0.9, {"cites": True})
    graph.add_edge(nodes[1], nodes[2], 0.9, {"cites": True})
    hits = graph.search_and_traverse("uniqueseed", max_seeds=1, min_strength=0.0, max_depth=2, required_properties=["cites"])
    assert len(hits) == 3
    assert next(h.graph_strength for h in hits if h.depth == 2) == pytest.approx(0.81)
    graph.save()
    loaded = pygrap.ShardedGraph(path, 3)
    assert len(loaded.search_and_traverse("uniqueseed", max_seeds=1, min_strength=0.0, max_depth=2)) == 3
    mapping = loaded.execute_rebalance_to(5)
    assert set(mapping) == set(nodes)
    assert len(loaded.search_and_traverse("uniqueseed", max_seeds=1, min_strength=0.0, max_depth=2, required_properties=["cites"])) == 3
    assert loaded.remove_node(mapping[nodes[2]])
    assert len(loaded.search_and_traverse("uniqueseed", max_seeds=1, min_strength=0.0, max_depth=2)) == 2
