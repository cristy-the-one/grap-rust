"""Replay source-derived same/different entity pairs through the real Rust graph.

Vectors are fetched once per installed embedding model and cached locally.
Threshold sweeps and subsequent implementation comparisons make no model calls.
"""
import argparse
import json
from pathlib import Path
import time

from grap_mcp.kee.embeddings import HashingEmbedder, OpenAICompatEmbedder
from grap_mcp.kee.working_graph import WorkingGraph


class CachedEmbedder:
    name = "evaluation-cache"

    def __init__(self, model, vectors):
        self.model = model
        self.vectors = vectors
        self.dim = len(next(iter(vectors.values())))

    def embed(self, texts):
        return [self.vectors[text] for text in texts]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path(".logs/real-law-embedding-cache.json"))
    args = parser.parse_args()
    pairs = json.loads(Path("docs/experiments/real-law-entity-pairs.json").read_text(encoding="utf-8"))
    names = list(dict.fromkeys(name for pair in pairs for name in (pair["a"], pair["b"])))
    cache = json.loads(args.cache.read_text(encoding="utf-8")) if args.cache.exists() else {}
    report = {"pairs": pairs, "runs": []}
    for model in ("hashing", "bge-m3:latest", "qwen3-embedding:0.6b"):
        if model not in cache or set(cache[model]["vectors"]) != set(names):
            embedder = HashingEmbedder() if model == "hashing" else OpenAICompatEmbedder(model=model)
            start = time.perf_counter(); vectors = embedder.embed(names)
            cache[model] = {"seconds": round(time.perf_counter()-start, 3), "vectors": dict(zip(names,vectors))}
            args.cache.write_text(json.dumps(cache,ensure_ascii=False),encoding="utf-8")
        vectors = cache[model]["vectors"]
        embedder = CachedEmbedder(model, vectors)
        for threshold in (.8, .86, .9, .95, 1.1):
            rows = []
            for pair in pairs:
                a, b = pair["a"], pair["b"]
                with WorkingGraph(embedder, merge_threshold=threshold) as graph:
                    graph.upsert_fragment({"outcomes": [{"name": n, "characteristics": []} for n in (a,b)],
                                           "relations": [{"from": a, "type": "test-link", "to": b}]}, "pair")
                    merged = graph.node_count() == 1
                    rows.append({**pair, "cosine": sum(x*y for x,y in zip(vectors[a],vectors[b])),
                                 "merged": merged, "correct": merged == pair["same"],
                                 "relations_retained": len(graph.relations)})
            summary = {"false_merges": sum(r["merged"] and not r["same"] for r in rows),
                       "missed_aliases": sum(not r["merged"] and r["same"] for r in rows),
                       "distinct_pairs": sum(not r["same"] for r in rows),
                       "alias_pairs": sum(r["same"] for r in rows)}
            report["runs"].append({"model":model,"threshold":threshold,"embedding_batch_seconds":cache[model]["seconds"],
                                   "dimensions":embedder.dim,"summary":summary,"rows":rows})
            args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
            print(model,threshold,summary,flush=True)


if __name__ == "__main__":
    main()
