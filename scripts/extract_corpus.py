#!/usr/bin/env python3
"""Batch ontology extraction over a large corpus.

Parallel across one or more OpenAI-compatible endpoints (e.g. two Ollama
instances pinned to two GPUs), bigger chunks (fewer LLM calls), and
checkpointed/resumable (kill any time; rerun continues). Output is the SAME
candidate-ontology schema the demo GUI loads.

Two GPUs example (one Ollama per GPU):
  # terminal 1: $env:CUDA_VISIBLE_DEVICES="0"; $env:OLLAMA_HOST="127.0.0.1:11434"; ollama serve
  # terminal 2: $env:CUDA_VISIBLE_DEVICES="1"; $env:OLLAMA_HOST="127.0.0.1:11435"; ollama serve
  python scripts/extract_corpus.py real-data-law-ro \
      --endpoints http://localhost:11434/v1,http://localhost:11435/v1 \
      --model llama3.2:3b --chunk-chars 2000 --workers 4 --out out/legal-ontology

Output (in --out):
  fragments.jsonl          per-chunk checkpoint (resumable; delete to restart)
  candidate-ontology.yaml  load via the GUI: Knowledge -> Load ontology
  relations.json           project via POST /api/relations/project (or the legal graph)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))
from grap_mcp.kee import embeddings, extract, llm, pipeline, reconcile  # noqa: E402


def _load_checkpoint(frag_path: Path):
    """Return (done_indices, records_by_index) from an existing checkpoint."""
    done, by_i = set(), {}
    if frag_path.exists():
        for line in frag_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            i = rec.get("i")
            by_i[i] = rec                       # last write wins (retried chunks)
            if not rec.get("error"):
                done.add(i)                     # only successes count as done
    return done, by_i


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="corpus folder (.md/.txt/.json) or single file")
    ap.add_argument("--endpoints",
                    default=os.environ.get("GRAP_LLM_BASE_URL", "http://localhost:11434/v1"),
                    help="comma-separated OpenAI-compatible base URLs (one per GPU)")
    ap.add_argument("--model", default=os.environ.get("GRAP_LLM_MODEL", "llama3.2:3b"))
    ap.add_argument("--api-key", default=os.environ.get("GRAP_LLM_API_KEY"))
    ap.add_argument("--chunk-chars", type=int, default=2000,
                    help="chunk size in chars (bigger = fewer calls; keep modest for small models)")
    ap.add_argument("--max-files", type=int, default=None, help="cap number of source files")
    ap.add_argument("--workers", type=int, default=None,
                    help="concurrent requests (default = number of endpoints)")
    ap.add_argument("--sim", type=float, default=0.82, help="reconcile similarity threshold")
    ap.add_argument("--out", default="out/corpus-ontology", help="output directory")
    ap.add_argument("--reconcile-only", action="store_true",
                    help="skip extraction; just (re)build the ontology from fragments.jsonl")
    args = ap.parse_args(argv)

    endpoints = [e.strip() for e in args.endpoints.split(",") if e.strip()]
    providers = [llm.OpenAICompatProvider(base_url=e, model=args.model,
                                          api_key=args.api_key, timeout=180.0)
                 for e in endpoints]
    workers = args.workers or len(providers)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    frag_path = outdir / "fragments.jsonl"

    if not args.reconcile_only:
        chunks = pipeline.read_chunks(args.path, max_files=args.max_files,
                                      chunk_chars=args.chunk_chars)
        total = len(chunks)
        done, _ = _load_checkpoint(frag_path)
        print(f"corpus={args.path}  chunks={total}  chunk_chars={args.chunk_chars}")
        print(f"model={args.model}  endpoints={endpoints}  workers={workers}")
        if done:
            print(f"resuming: {len(done)} chunks already done")

        lock = threading.Lock()
        fout = open(frag_path, "a", encoding="utf-8")
        state = {"done": len(done), "ok": 0, "fail": 0}
        t0 = time.time()
        start_done = len(done)

        def work(item):
            i, (src, text) = item
            prov = providers[i % len(providers)]
            try:
                raw = prov.complete(extract.SYSTEM_PROMPT, extract.build_user_prompt(text))
                obj = extract.extract_json(raw)
                frag = (extract.normalize_fragment(obj, src) if obj
                        else {"source": src, "outcomes": [], "relations": []})
            except Exception as exc:               # endpoint/parse error — non-fatal
                frag = {"source": src, "outcomes": [], "relations": [], "error": str(exc)}
            rec = {"i": i, **frag}
            with lock:
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fout.flush()
                state["done"] += 1
                state["fail" if frag.get("error") else "ok"] += 1
                d = state["done"]
                if d % 20 == 0 or d == total:
                    el = time.time() - t0
                    rate = (d - start_done) / el if el > 0 else 0
                    eta = (total - d) / rate / 3600 if rate > 0 else 0
                    print(f"  {d}/{total}  ok={state['ok']} fail={state['fail']}  "
                          f"{rate:.2f} chunk/s  ETA {eta:.1f}h")

        items = [(i, c) for i, c in enumerate(chunks) if i not in done]
        try:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(work, items))
        finally:
            fout.close()
        print(f"extraction phase: {(time.time() - t0) / 60:.1f} min")

    # ---- reconcile all fragments into a GUI-compatible candidate ontology ----
    print("reconciling fragments -> candidate ontology…")
    _, by_i = _load_checkpoint(frag_path)
    frags = [{"source": r.get("source", "?"), "outcomes": r.get("outcomes", []),
              "relations": r.get("relations", [])}
             for r in by_i.values() if r.get("outcomes") or r.get("relations")]
    emb = embeddings.get_embedder()
    onto = reconcile.reconcile(frags, emb, sim=args.sim, version="corpus-candidate")
    yaml_path = outdir / "candidate-ontology.yaml"
    rel_path = outdir / "relations.json"
    yaml_path.write_text(pipeline.to_yaml(onto), encoding="utf-8")
    rel_path.write_text(json.dumps(onto.get("relations", []), ensure_ascii=False, indent=2),
                        encoding="utf-8")

    print(f"\nfragments used={len(frags)}  outcomes={len(onto['process_outcomes'])}  "
          f"relations={len(onto.get('relations', []))}  embedder={emb.name}")
    print("GUI-compatible output:")
    print(f"  ontology : {yaml_path}   (GUI: Knowledge -> Load ontology)")
    print(f"  relations: {rel_path}    (POST /api/relations/project, or the Legal graph)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
