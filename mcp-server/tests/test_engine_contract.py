"""Exercise the actual Rust binding used by the harness, without LLM services."""

import pytest
import pygrap


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
