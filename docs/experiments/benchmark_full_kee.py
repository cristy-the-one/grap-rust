"""Bounded full-KEE timing probe; execution success is not semantic accuracy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import time

from grap_mcp.kee import pipeline, graph_extract
from grap_mcp.kee.embeddings import OpenAICompatEmbedder
from exercise_legal_rules_live import RecordingProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--chunks", type=int, default=24)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists() or not 1 <= args.chunks <= 100:
        raise SystemExit("Choose a fresh output and 1..100 chunks")
    root = Path(__file__).resolve().parents[2]
    corpus = root / "real-data-law-ro"
    ids = set()
    manifest = []
    for path in sorted(corpus.glob("*.json")):
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8-sig"))
        ids.add(str(document.get("id") or path.stem))
        manifest.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
    counts = []
    for size in (800, 1600, 2400):
        stats = {}
        all_chunks = pipeline.read_chunks(str(corpus), chunk_chars=size, stats=stats)
        chunks = [(label, text) for label, text in all_chunks if label.rsplit("#", 1)[0] in ids]
        counts.append({"chunk_chars": size, "json_source_chunks": len(chunks),
                       "non_json_chunks_excluded": len(all_chunks)-len(chunks),
                       "tabular_chunks_skipped": stats["tabular_skipped"]})
        if size == 800:
            selected = random.Random(20260907).sample(chunks, min(args.chunks, len(chunks)))
    os.environ["GRAP_KEE_STRUCTURED_OUTPUT"] = "1"
    provider = RecordingProvider(model="grap-legal-gemma4", extra_body={"reasoning_effort": "none"})
    provider.retries, provider.timeout, provider.max_tokens = 0, 90, 2048
    embedder = OpenAICompatEmbedder(model="bge-m3")
    embedder.retries = 0
    report = {"mode": "full graph-augmented KEE, randomly sampled JSON-source chunks; not legal accuracy scoring",
              "seed": 20260907, "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
              "population_json_files": len(manifest), "counts": counts,
              "selected_chunks": [{"id": label, "text": text} for label, text in selected],
              "model": provider.model, "embedder": embedder.model, "calls": provider.calls}
    print(json.dumps({"counts": counts, "sample_chunks": len(selected)}), flush=True)
    started = time.perf_counter()
    try:
        report["result"] = graph_extract.graph_extract_ontology(selected, provider, embedder, concurrency=1)
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        report["seconds"] = round(time.perf_counter()-started, 3)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"seconds": report["seconds"], "calls": len(provider.calls),
                      "error": report.get("error"),
                      "stats": {k:v for k,v in report.get("result",{}).items() if k != "ontology"}}), flush=True)


if __name__ == "__main__":
    main()
