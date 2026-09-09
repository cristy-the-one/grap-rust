import threading
import runpy
from pathlib import Path

import pygrap
import pytest

from grap_mcp.application import ApplicationState
from grap_mcp.ingest import ingest_paths
from grap_mcp.native_graph import SerializedGraph
other_thread_progresses = runpy.run_path(str(
    Path(__file__).resolve().parents[2] / "docs/experiments/glue_benchmark.py"
))["other_thread_progresses"]


def test_cached_native_methods_do_not_retain_the_graph_in_a_self_cycle():
    import weakref
    class Handle:
        def node_count(self): return 0
    handle = Handle()
    reference = weakref.ref(handle)
    graph = SerializedGraph(handle)
    del handle
    assert graph.node_count() == 0
    del graph
    assert reference() is None


def test_native_batch_conversion_and_validation_precede_mutation(tmp_path):
    graph = pygrap.IndexedKnowledgeGraph(str(tmp_path / "graph"))
    with pytest.raises(TypeError):
        graph.add_nodes([("first", None, None), (object(), None, None)])
    assert graph.node_count() == 0
    with pytest.raises(Exception, match="consistent dimension"):
        graph.add_nodes([("first", None, [1.0]), ("bad", None, [1.0, 2.0])])
    assert graph.node_count() == 0
    assert graph.add_nodes([("lege română", "label", [1.0, 0.0]), ("other", None, None)]) == [0, 1]
    graph.save()
    assert pygrap.IndexedKnowledgeGraph(str(tmp_path / "graph")).vector_search([1.0, 0.0], 2) == [(0, 1.0)]


def test_save_releases_gil_and_shared_handle_serializes_access(tmp_path):
    raw = pygrap.IndexedKnowledgeGraph(str(tmp_path / "graph"))
    raw.add_nodes([(f"document {i} " + " " * (128 * 1024), None, None) for i in range(512)])
    assert other_thread_progresses(raw.save)["other_thread_progressed"]
    state = ApplicationState()
    state.set_kg(raw)
    # The caller waiting on this lock must release the GIL so the native save
    # can finish. No borrow errors or deadlocks are acceptable in the adapter.
    errors = []
    gate = threading.Barrier(3)
    def read():
        try:
            gate.wait()
            for _ in range(50):
                assert state.kg.node_count() == 512
        except Exception as exc:
            errors.append(exc)
    workers = [threading.Thread(target=read) for _ in range(2)]
    for thread in workers: thread.start()
    gate.wait()
    state.kg.save()
    for thread in workers:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert not errors


def test_ingestion_embeds_before_reading_the_whole_corpus(tmp_path, monkeypatch):
    from grap_mcp import ingest
    for i in range(10):
        (tmp_path / f"{i}.txt").write_text(f"rule {i}")
    seen = []
    original = ingest.read_document
    def read(path):
        seen.append(path)
        return original(path)
    monkeypatch.setattr(ingest, "read_document", read)
    class Embedder:
        def embed(self, texts):
            if len(seen) <= 3: assert len(seen) == 3
            else: assert calls
            calls.append(len(texts))
            return [[1.0] for _ in texts]
    calls = []
    graph = pygrap.IndexedKnowledgeGraph(str(tmp_path / "graph"))
    assert ingest_paths(graph, str(tmp_path), Embedder(), batch_size=3)["nodes_added"] == 10
    assert calls == [3, 3, 3, 1]
