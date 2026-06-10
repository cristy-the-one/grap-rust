"""Phase 2: GraphStore sqlite round-trip."""

from grap_mcp.kee.graph_store import GraphStore, GraphState


def _state():
    return GraphState(
        concepts={0: "Test Plan", 1: "Verification"},
        aliases={0: ["Test Plan", "test plan"], 1: ["Verification"]},
        chars={0: ["coverage"], 1: []},
        edges=[(1, 0, "uses")],
        concept_sources={0: ["a#0", "b#0"], 1: ["a#0"]},
        char_sources={(0, "coverage"): ["a#0"]},
        edge_sources={(1, 0): ["a#0"]},
        manifest={"a#0": "h1", "b#0": "h2"},
        rel_vocab=["uses"],
        parked=[{"from": "X", "type": "rel", "to": "Y", "chunk_id": "a#0", "reason": "ungrounded"}],
    )


def test_save_load_round_trip(tmp_path):
    path = str(tmp_path / "graph_store.db")
    s = _state()
    with GraphStore(path) as store:
        store.save(s)
    with GraphStore(path) as store:
        loaded = store.load()
    assert loaded == s


def test_save_replaces_previous_state(tmp_path):
    path = str(tmp_path / "graph_store.db")
    with GraphStore(path) as store:
        store.save(_state())
        smaller = GraphState(concepts={0: "Only"}, aliases={0: ["Only"]}, chars={0: []},
                             edges=[], concept_sources={0: ["z#0"]}, char_sources={},
                             edge_sources={}, manifest={"z#0": "h"}, rel_vocab=[], parked=[])
        store.save(smaller)
        assert store.load() == smaller


def test_empty_store_loads_empty_state(tmp_path):
    with GraphStore(str(tmp_path / "graph_store.db")) as store:
        loaded = store.load()
    assert loaded.concepts == {} and loaded.edges == [] and loaded.manifest == {}
