"""Model-free Python/native call and ingest-buffer probes. Outputs JSON."""
import argparse
import json
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

import pygrap
from grap_mcp.ingest import ingest_paths
from grap_mcp.native_graph import SerializedGraph


def other_thread_progresses(operation):
    """Suppress normal bytecode switching; only native GIL release lets worker run."""
    ready, start, progressed = threading.Event(), threading.Event(), threading.Event()
    def worker():
        ready.set()
        start.wait()
        progressed.set()
    thread = threading.Thread(target=worker)
    thread.start()
    ready.wait()
    interval = sys.getswitchinterval()
    try:
        sys.setswitchinterval(10.0)
        start.set()
        began = time.perf_counter()
        operation()
        elapsed = time.perf_counter() - began
        observed = progressed.is_set()
    finally:
        sys.setswitchinterval(interval)
        start.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    return {"other_thread_progressed": observed, "seconds": elapsed}


def run():
    report = {"binding": pygrap.__file__, "native": []}
    with tempfile.TemporaryDirectory(prefix="grap-glue-") as tmp:
        root = Path(tmp)
        for length, dimension in [(24, 0), (800, 0), (800, 256)]:
            count = 4000
            rows = [(f"rule {i} " + "evidence " * (length // 9), str(i),
                     [1.0] * dimension if dimension else None) for i in range(count)]
            for mode in ["single", "batch", "serialized_single", "serialized_batch"]:
                if mode.endswith("batch") and not hasattr(pygrap.IndexedKnowledgeGraph, "add_nodes"):
                    continue
                timings = []
                for trial in range(3):
                    graph = pygrap.IndexedKnowledgeGraph(str(root / f"{length}-{dimension}-{mode}-{trial}"))
                    if mode.startswith("serialized"):
                        graph = SerializedGraph(graph)
                    began = time.perf_counter()
                    if mode.endswith("single"):
                        ids = [graph.add_node(t, label=l, embedding=e) for t, l, e in rows]
                    else:
                        ids = []
                        for offset in range(0, len(rows), 128):
                            ids.extend(graph.add_nodes(rows[offset:offset + 128]))
                    timings.append(time.perf_counter() - began)
                    assert ids == list(range(count)) and graph.node_count() == count
                    del graph
                report["native"].append({"chars_approx": length, "dimension": dimension,
                    "nodes": count, "mode": mode, "median_seconds": statistics.median(timings)})
        graph = pygrap.IndexedKnowledgeGraph(str(root / "gil"))
        for i in range(512):
            graph.add_node(f"document {i} " + " " * (128 * 1024))
        report["save_gil"] = other_thread_progresses(graph.save)
        del graph
        docs = root / "docs"
        docs.mkdir()
        for i in range(128):
            (docs / f"{i:04}.txt").write_text(("evidence " * 80 + "\n\n") * 90, encoding="utf-8")
        class Sink:
            def add_node(self, text, label=None, embedding=None): return 0
            def add_nodes(self, rows): return [0 for _ in rows]
        tracemalloc.start()
        counts = ingest_paths(Sink(), str(docs))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report["ingest_buffer"] = {"python_peak_bytes": peak, **counts}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
