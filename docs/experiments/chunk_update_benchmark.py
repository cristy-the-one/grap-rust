"""Measure new empty chunks in a populated Rust-backed extraction graph."""
import argparse
import json
import statistics
import tempfile
import time

from checkpoint_benchmark import baseline, CountingEmbedder
from grap_mcp.kee.working_graph import WorkingGraph


class GraphCalls:
    def __init__(self, graph):
        self.graph = graph
        self.clears = 0

    def __getattr__(self, name):
        return getattr(self.graph, name)

    def clear_edges(self):
        self.clears += 1
        return self.graph.clear_edges()


def measure(cls, count, chunks):
    with tempfile.TemporaryDirectory(prefix="grap-chunk-bench-") as root:
        embedder = CountingEmbedder()
        graph = cls(embedder, workdir=root, merge_threshold=1.1)
        # One native insertion batch; fixture construction is outside the timed path.
        graph.upsert_fragment({"outcomes": [
            {"name": f"Concept {i}", "characteristics": [f"criterion {i}"]}
            for i in range(count)]}, "existing")
        graph.kg = GraphCalls(graph.kg)
        embedded_before = embedder.texts
        samples = []
        for repeat in range(3):
            start = time.perf_counter()
            for i in range(chunks):
                graph.replace_fragment({"outcomes": [], "relations": []}, f"new-{repeat}-{i}")
            samples.append(time.perf_counter() - start)
        result = {"concepts": count, "new_chunks": chunks,
                  "median_ms": round(statistics.median(samples) * 1000, 3),
                  "embedded_texts": embedder.texts - embedded_before,
                  "rust_edge_rebuilds": graph.kg.clears}
        assert len(graph.canonical) == count
        graph.close()
        return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--baseline", default="2b80e8f")
    ap.add_argument("--concepts", type=int, default=1000)
    ap.add_argument("--chunks", type=int, default=1000)
    args = ap.parse_args()
    for label, cls in (("before", baseline(args.baseline)), ("after", WorkingGraph)):
        print(json.dumps({"implementation": label, **measure(cls, args.concepts, args.chunks)}), flush=True)
