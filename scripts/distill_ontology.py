"""Resume automatic vocabulary discovery from a KEE export or durable workdir."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))
from grap_mcp.kee.distill import distill_ontology, load_input
from grap_mcp.kee.llm import OpenAICompatProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="KEE JSON/YAML export, timing probe JSON, or durable extraction directory")
    parser.add_argument("--out", required=True, help="Output directory (cached successful batches resume here)")
    parser.add_argument("--model", default="grap-legal-gemma4")
    parser.add_argument("--max-calls", type=int, default=30)
    parser.add_argument("--min-documents", type=int, default=2)
    args = parser.parse_args()
    ontology, sources = load_input(args.input)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    provider = OpenAICompatProvider(model=args.model, extra_body={"reasoning_effort": "none"})
    provider.timeout = 90
    report = distill_ontology(ontology, provider, sources=sources, cache_dir=out / "cache",
        max_calls=args.max_calls, min_documents=args.min_documents,
        progress=lambda event: print(json.dumps(event), flush=True))
    # Keep distinct input/model results; a resumed run updates only its own report.
    target = out / (report["input_sha256"][:16]+"-"+args.model.replace(":", "_").replace("/", "_")+".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    temporary.replace(target)
    print(json.dumps({"report": str(target), **{k: report[k] for k in
        ("status", "calls", "cache_hits", "pending", "counts", "failures")}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
