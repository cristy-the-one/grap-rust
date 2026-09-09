"""Live local embeddings, fixed legal extraction replies; temporary graphs only.

This isolates embedding scheduling, not generation speed or legal accuracy.
Run with PYTHONPATH=mcp-server. No model downloads or source writes.
"""
import argparse
import json
from pathlib import Path
import tempfile
import time

from grap_mcp.kee.embeddings import OpenAICompatEmbedder
from grap_mcp.kee.graph_extract import graph_extract_ontology
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.working_graph import WorkingGraph


class MeasuredEmbedder(OpenAICompatEmbedder):
    def __init__(self, model):
        super().__init__(base_url="http://127.0.0.1:11434/v1", model=model)
        self.requests = []

    def _embed_once(self, texts):
        started = time.perf_counter()
        vectors = super()._embed_once(texts)
        self.requests.append({"texts": len(texts), "seconds": time.perf_counter() - started})
        return vectors


def run(model):
    cases = json.loads(Path(__file__).with_name("real-law-extraction-cases.json").read_text(encoding="utf-8"))[:6]
    chunks = [(case["id"], case["text"]) for case in cases]
    def reply(system, user):
        case = next(case for case in cases if case["text"] in user)
        names = list(dict.fromkeys(name for relation in case.get("relations", [])
                                   for name in (relation[0], relation[2])))
        if not names:
            names = list(dict.fromkeys(row[0] for row in case.get("requirements", [])))
        return json.dumps({"outcomes": [{"name": name} for name in names], "relations": []})
    warm = MeasuredEmbedder(model)
    warm.embed(["Încălzire pentru măsurarea procesării documentelor juridice."])
    original = WorkingGraph.prefetch_extraction
    reports, outputs, prompts = [], [], []
    with tempfile.TemporaryDirectory(prefix="grap-embedding-schedule-") as tmp:
        try:
            for i, mode in enumerate(["legacy", "batched", "batched", "legacy"]):
                WorkingGraph.prefetch_extraction = original if mode == "batched" else lambda *args: None
                embedder = MeasuredEmbedder(model)
                provider = StubProvider(handler=reply)
                started = time.perf_counter()
                result = graph_extract_ontology(chunks, provider, embedder,
                                                sim=1.1, concurrency=1, relook_ratio=0,
                                                workdir=str(Path(tmp) / str(i)))
                reports.append({"mode": mode, "seconds": time.perf_counter() - started,
                                "embedding_requests": len(embedder.requests),
                                "embedding_seconds": sum(r["seconds"] for r in embedder.requests),
                                "batch_sizes": [r["texts"] for r in embedder.requests]})
                outputs.append(result)
                prompts.append(provider.calls)
        finally:
            WorkingGraph.prefetch_extraction = original
    return {"scope": "Live local embedding scheduling with fixed replies; no generation or legal accuracy measurement",
            "model": model, "source_cases": [case["id"] for case in cases],
            "runs": reports, "identical_results": all(out == outputs[0] for out in outputs),
            "identical_prompts": all(prompt == prompts[0] for prompt in prompts)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="bge-m3:latest")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.model)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
