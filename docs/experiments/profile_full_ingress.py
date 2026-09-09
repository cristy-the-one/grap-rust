"""Paired full graph-augmented legal ingress using the production HTTP provider."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from grap_mcp.kee import extract, qualified, graph_extract
from grap_mcp.kee.llm import OpenAICompatProvider
from embedding_schedule_benchmark import MeasuredEmbedder

ROOT = Path(__file__).resolve().parents[2]


class Provider(OpenAICompatProvider):
    def __init__(self):
        super().__init__(base_url="http://127.0.0.1:11434/v1", model="grap-legal-gemma4",
                         extra_body={"reasoning_effort": "none", "seed": 42}, timeout=180)
        self.retries, self.max_tokens = 0, 2048
        self.calls = []

    def complete(self, system, user):
        row = {"purpose": "extraction_or_relook" if system.startswith("You are an information-extraction") else "classification_repair",
               "prompt_sha256": hashlib.sha256((system + "\0" + user).encode()).hexdigest()}
        started = time.perf_counter()
        try:
            row["raw"] = super().complete(system, user)
            return row["raw"]
        except Exception as exc:
            row["error"] = str(exc)
            raise
        finally:
            row["wall_s"] = time.perf_counter() - started
            self.calls.append(row)


def run(args):
    cases, chunks, contexts, documents = [], [], {}, {}
    for fixture in ("qualified-rules-additional-cases-2026-09.json", "chunking-accuracy-cases-2026-09.json"):
        cases.extend(json.loads((ROOT / "docs/experiments" / fixture).read_bytes())["cases"])
    for case in cases:
        raw = (ROOT / case["source_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
        text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
        documents[str((ROOT / case["source_file"]).resolve())] = text
        plan = qualified.plan(text, str((ROOT / case["source_file"]).resolve()))
        for chunk in case["arms"]["statements"]:
            source = case["id"] + ":" + chunk["id"]
            chunks.append((source, chunk["text"]))
            contexts[source] = qualified.slice_plan(plan, chunk["start"], chunk["end"])
    warm = Provider()
    warm.complete("Return JSON.", 'Return {"ready":true}.')
    MeasuredEmbedder("bge-m3").embed(["Încălzire"])
    report = {"scope": "12 development targets, full graph-augmented loop, real generation/embeddings, no production writes",
              "transport": "production OpenAI-compatible provider; zero retries, temperature 0, seed 42, max tokens 2048",
              "source_hashes": {c["source_file"]: c["source_sha256"] for c in cases}, "runs": []}
    report["code_sha256"] = {str(Path(module.__file__).relative_to(ROOT)):
        hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        for module in (extract, qualified, graph_extract)}
    previous = os.environ.get("GRAP_KEE_STRUCTURED_OUTPUT")
    try:
        with tempfile.TemporaryDirectory(prefix="grap-ingress-profile-") as tmp:
            for mode in (["structured", "baseline"] if args.reverse else ["baseline", "structured"]):
                os.environ["GRAP_KEE_STRUCTURED_OUTPUT"] = "1" if mode == "structured" else "0"
                provider, embedder = Provider(), MeasuredEmbedder("bge-m3")
                started = time.perf_counter()
                result = graph_extract.graph_extract_ontology(chunks, provider, embedder, rule_contexts=contexts,
                    concurrency=1, workdir=str(Path(tmp) / mode))
                elapsed = time.perf_counter() - started
                rules = result["ontology"].get("qualified_legal", {}).get("rules", [])
                scores = []
                for case in cases:
                    source_file = str((ROOT / case["source_file"]).resolve())
                    targets = [r for r in rules if r["source_file"] == source_file and case["text"] in r["statement"]]
                    scores.append({"id": case["id"], "correct": len(targets) == 1 and all(
                        r["kind"] == case["expected_kind"] and r["subject"] in case["accepted_subjects"]
                        and r["classification_status"] != "conflict"
                        and all(q in r["statement"] for q in case["required_text"])
                        and all(documents[source_file][s["start"]:s["end"]] == s["quote"] for s in r["evidence"])
                        for r in targets)})
                row = {"mode": mode, "wall_s": elapsed, "llm_s": sum(c["wall_s"] for c in provider.calls),
                       "embedding_s": sum(c["seconds"] for c in embedder.requests), "embedding_requests": len(embedder.requests),
                       "calls": provider.calls, "scores": scores, "result": result}
                report["runs"].append(row)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({"mode": mode, "seconds": elapsed, "calls": len(provider.calls),
                                  "correct": sum(s["correct"] for s in scores), "relooks": result["relooks"]}), flush=True)
    finally:
        if previous is None:
            os.environ.pop("GRAP_KEE_STRUCTURED_OUTPUT", None)
        else:
            os.environ["GRAP_KEE_STRUCTURED_OUTPUT"] = previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a fresh output path")
    run(args)
