"""Compare extraction checkpoints with the pre-simplification implementation.

Run with the built pygrap binding and mcp-server on PYTHONPATH. Uses temporary
stores and an offline counting embedder. Baseline Python source is loaded from
the specified Git revision; both versions use the same current Rust binding.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import types

from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.working_graph import WorkingGraph


class CountingEmbedder(HashingEmbedder):
    def __init__(self):
        super().__init__(64)
        self.texts = 0

    def embed(self, texts):
        self.texts += len(texts)
        return super().embed(texts)


def baseline(revision):
    def load(file):
        name = f"grap_mcp.kee._benchmark_before_{file}"
        module = types.ModuleType(name)
        sys.modules[name] = module
        source = subprocess.check_output([
            "git", "show", f"{revision}:mcp-server/grap_mcp/kee/{file}.py"],
            cwd=Path(__file__).resolve().parents[2]).decode("utf-8")
        exec(compile(source, f"{revision}/{file}.py", "exec"), module.__dict__)
        return module
    store, working = load("graph_store"), load("working_graph")
    working.GraphStore = store.GraphStore
    working.GraphState = store.GraphState
    return working.WorkingGraph


def measure(cls, count, repeats):
    with tempfile.TemporaryDirectory(prefix="grap-checkpoint-bench-") as root:
        embedder = CountingEmbedder()
        wg = cls(embedder, workdir=root, merge_threshold=1.1)
        for i in range(count):
            wg.upsert_fragment({"outcomes": [{"name": f"Concept {i}",
                                              "characteristics": []}]}, f"chunk-{i}")
        wg.save()
        unchanged, changed = [], []
        for _ in range(repeats):
            start = time.perf_counter()
            wg.save()
            unchanged.append(time.perf_counter() - start)
        for i in range(repeats):
            # One chunk fingerprint changes; concepts and retrieval stay identical.
            wg.manifest["chunk-0"] = f"hash-{i}"
            start = time.perf_counter()
            wg.save()
            changed.append(time.perf_counter() - start)
        wg.close()
        before = embedder.texts
        start = time.perf_counter()
        reopened = cls.open(root, embedder)
        reopen_seconds = time.perf_counter() - start
        reopen_embeddings = embedder.texts - before
        reopened.close()
        return {"concepts": count, "unchanged_checkpoint_ms": round(statistics.median(unchanged)*1000, 2),
                "one_row_checkpoint_ms": round(statistics.median(changed)*1000, 2),
                "reopen_ms": round(reopen_seconds*1000, 2),
                "reopen_embedded_texts": reopen_embeddings}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="903beb1")
    parser.add_argument("--counts", nargs="+", type=int, default=[200, 1000])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    before = baseline(args.baseline)
    for count in args.counts:
        for label, cls in [("before", before), ("after", WorkingGraph)]:
            print(json.dumps({"implementation": label, **measure(cls, count, args.repeats)}), flush=True)
