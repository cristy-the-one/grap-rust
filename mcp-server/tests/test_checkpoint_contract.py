import sqlite3

import pytest

from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.graph_store import GraphState, GraphStore
from grap_mcp.kee.working_graph import WorkingGraph


def add(wg, name, source):
    wg.upsert_fragment({"outcomes": [{"name": name, "characteristics": []}]}, source)
    return next(cid for cid, text in wg.canonical.items() if text == name)


class CountingEmbedder(HashingEmbedder):
    def __init__(self, dim=64):
        super().__init__(dim)
        self.texts = 0

    def embed(self, texts):
        self.texts += len(texts)
        return super().embed(texts)


def test_checkpoint_preserves_views_and_reopen_needs_no_embeddings(tmp_path):
    embedder = CountingEmbedder()
    with WorkingGraph(embedder, workdir=str(tmp_path)) as wg:
        add(wg, "Verification", "a")
        graph, index, calls = wg.kg, wg.idx, embedder.texts
        wg.save()
        wg.save()
        assert wg.kg is graph and wg.idx is index
        assert embedder.texts == calls
    with WorkingGraph.open(str(tmp_path), embedder) as reopened:
        assert reopened.canonical == {0: "Verification"}
        assert embedder.texts == calls
    changed_model = CountingEmbedder(dim=128)
    with WorkingGraph.open(str(tmp_path), changed_model) as reopened:
        assert changed_model.texts == 1
        assert len(reopened._embed_one("Verification")) == 128


def test_concept_ids_survive_holes_and_never_reuse_high_water_mark(tmp_path):
    with WorkingGraph(HashingEmbedder(), merge_threshold=1.1, workdir=str(tmp_path)) as wg:
        removed = add(wg, "Removed", "a")
        kept = add(wg, "Kept", "b")
        high = add(wg, "Highest removed", "c")
        wg.retract_chunk("a")
        wg.retract_chunk("c")
        assert wg.kg.node_count() == 1  # correct even before checkpoint
        assert wg.canonical == {kept: "Kept"}
        wg.save()
    with WorkingGraph.open(str(tmp_path), HashingEmbedder(), merge_threshold=1.1) as wg:
        assert wg.canonical == {kept: "Kept"}
        assert wg._concept_to_node[kept] == 0 != kept
        assert wg._resolve_outcome("Kept").node_id == kept
        assert wg.context_for("Kept").entities == ["Kept"]
        new = add(wg, "Brand new", "d")
        assert new > high > removed
        wg.upsert_fragment({"relations": [{"from": "Kept", "to": "Brand new", "type": "uses"}]}, "d")
        assert wg.kg.edge_count() == 1
        wg.save()
    with WorkingGraph.open(str(tmp_path), HashingEmbedder(), merge_threshold=1.1) as wg:
        assert wg.relations == [{"from_id": kept, "to_id": new, "type": "uses"}]
        assert wg.focused_context(type("Signals", (), {"conflicts": [{"names": ["Kept"]}], "correlations": []})()).entities == ["Kept"]


def test_edge_retraction_updates_live_topology_and_component_membership(tmp_path):
    with WorkingGraph(HashingEmbedder(), workdir=str(tmp_path), merge_threshold=1.1) as wg:
        a = add(wg, "Alpha", "a")
        b = add(wg, "Beta", "b")
        wg.upsert_fragment({"relations": [{"from": "Alpha", "to": "Beta", "type": "uses"}]}, "edge")
        assert wg._find(a) == wg._find(b)
        wg.retract_chunk("edge")
        assert wg.kg.edge_count() == 0
        assert wg.kg.node_count() == 2
        assert wg._find(a) != wg._find(b)
        assert wg._has_edge == set()
        assert wg.context_for("Alpha Beta").relations == []


def test_store_writes_only_changes_and_rolls_back_failed_checkpoint(tmp_path):
    state = GraphState(concepts={7: "Seven"}, chars={7: ["old"]})
    with GraphStore(str(tmp_path / "graph_store.db")) as store:
        assert store.save(state) > 0
        assert store.save(state) == 0
        state.manifest["chunk"] = "hash"
        assert store.save(state) == 1
        store.conn.execute("CREATE TRIGGER fail_characteristic BEFORE INSERT ON characteristics "
                           "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        state.concepts[7] = "Edited"
        state.chars[7] = ["new"]
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            store.save(state)
        loaded = store.load()
        assert loaded.concepts == {7: "Seven"}
        assert loaded.chars == {7: ["old"]}


def test_reopen_discards_uncommitted_graph_and_alias_changes(tmp_path):
    with WorkingGraph(HashingEmbedder(), workdir=str(tmp_path), merge_threshold=1.1) as wg:
        add(wg, "Committed", "a")
        wg.save()
        add(wg, "Uncommitted", "b")
        # Derived data may survive a process failure; the SQLite checkpoint wins.
        wg.kg.save_to_dir(str(tmp_path / "graph"))
    with WorkingGraph.open(str(tmp_path), HashingEmbedder()) as wg:
        assert wg.canonical == {0: "Committed"}
        assert wg.kg.node_count() == 1
        assert not wg.idx.resolve("Uncommitted")


def test_empty_checkpoint_overrides_stale_projection_and_retains_id_counter(tmp_path):
    with WorkingGraph(HashingEmbedder(), workdir=str(tmp_path)) as wg:
        old = add(wg, "Old", "a")
        wg.kg.save_to_dir(str(tmp_path / "graph"))
        wg.retract_chunk("a")
        wg.save()
    with WorkingGraph.open(str(tmp_path), HashingEmbedder()) as wg:
        assert wg.kg.node_count() == 0
        assert add(wg, "New", "b") > old


def test_broken_embedding_cache_does_not_hide_durable_concepts(tmp_path):
    embedder = CountingEmbedder()
    with WorkingGraph(embedder, workdir=str(tmp_path)) as wg:
        add(wg, "Committed", "a")
        wg.save()
    with sqlite3.connect(tmp_path / "graph_store.db") as conn:
        conn.execute("UPDATE embeddings SET vector='broken json'")
    before = embedder.texts
    with WorkingGraph.open(str(tmp_path), embedder) as wg:
        assert wg.canonical == {0: "Committed"}
        assert embedder.texts == before + 1
