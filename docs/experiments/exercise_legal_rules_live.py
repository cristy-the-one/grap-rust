"""Live whole-document trial through the public API, SQLite receipts and Rust search."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import pygrap
from grap_mcp.application import GrapApplication
from grap_mcp.kee.legal_rules import VERSION
from grap_mcp.kee.llm import OpenAICompatProvider


class RecordingProvider(OpenAICompatProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []  # Shared by request-scoped provider copies.

    def complete(self, system, user):
        call = {"system": system, "user": user, "extra_body": self.extra_body}
        self.calls.append(call)
        started = time.perf_counter()
        try:
            call["reply"] = super().complete(system, user)
            return call["reply"]
        except Exception as exc:
            call["error"] = str(exc)
            raise
        finally:
            call["seconds"] = round(time.perf_counter() - started, 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemma4:latest")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    workspace, output = Path(args.workspace).resolve(), Path(args.output).resolve()
    if workspace.exists() or output.exists():
        raise SystemExit("Choose fresh workspace/output paths; previous results are preserved.")
    cases = json.loads((root / "docs/experiments/real-law-extraction-cases.json").read_text(encoding="utf-8"))
    sources = {c["source_file"]: c["source_sha256"] for c in cases}
    for file, digest in sources.items():
        assert hashlib.sha256((root / file).read_bytes()).hexdigest() == digest
    provider = RecordingProvider(model=args.model, extra_body={"reasoning_effort": "none"})
    provider.retries = 0
    app = GrapApplication(workspace, role="operator", provider_factory=lambda **_: provider)
    report = {"mode": "live complete documents; no replay or truncation", "model": args.model,
              "extractor_sha256": hashlib.sha256((root / "mcp-server/grap_mcp/kee/legal_rules.py").read_bytes()).hexdigest(),
              "prompt_version": VERSION, "max_tokens": provider.max_tokens, "runs": [],
              "receipts": [], "calls": provider.calls}

    def checkpoint():
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    for file in sources:
        started = time.perf_counter()
        result = app.extract_legal_claims(str(root / file), max_files=1, max_chars=8000, model=args.model)
        report["runs"].append({"source_file": file, "seconds": round(time.perf_counter() - started, 3), **result})
        checkpoint()
        print(json.dumps(report["runs"][-1], ensure_ascii=False), flush=True)

    ready = [s for s in app.get_legal_sources(limit=200)["sources"] if s["status"] == "ready"]
    for source in ready:
        receipt = app.get_legal_claims(revision_id=source["accepted_revision"])
        original = json.loads(Path(source["source_file"]).read_text(encoding="utf-8-sig"))["text_complet"]
        assert receipt["source_text"] == original
        for claim in receipt["claims"]:
            for span in [*claim["evidence"], *claim["context_evidence"],
                         *([claim["subject_evidence"]] if "subject_evidence" in claim else []),
                         *([claim["provision_evidence"]] if claim["provision_evidence"] else [])]:
                assert original[span["start"]:span["end"]] == span["quote"]
        report["receipts"].append(receipt)
    before = len(provider.calls)
    app = GrapApplication(workspace, role="operator", provider_factory=lambda **_: provider)
    started = time.perf_counter()
    report["corpus_scan"] = app.extract_legal_claims(str(root / "real-data-law-ro"), max_files=0)
    report["corpus_scan"]["seconds"] = round(time.perf_counter() - started, 3)
    assert len(provider.calls) == before
    assert report["corpus_scan"]["counts"]["unchanged"] == len(ready)
    definition_source = next(s for s in ready if Path(s["source_file"]).stem == "18185")
    report["definition_lookup"] = app.resolve_legal_term("cnvm", definition_source["source_id"])
    assert report["definition_lookup"]["resolution"] == "unique_definition"
    report["snapshot"] = app.export_legal_claim_graph()
    assert report["snapshot"]["status"] == "ok"
    graph = pygrap.IndexedKnowledgeGraph.with_merge_policy(report["snapshot"]["graph_path"], 2)
    hits = graph.search_and_traverse("indemnizație", max_seeds=5, required_properties=[],
        min_strength=0., max_depth=0, max_results=5, text_weight=1., graph_weight=0.)
    report["native"] = {"nodes": graph.node_count(), "edges": graph.edge_count(), "search_hits": len(hits)}
    claims = [c for receipt in report["receipts"] for c in receipt["claims"]]
    assert hits and graph.edge_count() == len(claims)
    report["summary"] = {"ready_documents": len(ready), "claims": len(claims), "model_calls": before,
        "unresolved_subjects": sum(not c["subject"] for c in claims),
        "claims_with_review_issues": sum(bool(c["review_issues"]) for c in claims),
        "explicit_definitions": sum(c["extraction_method"] == "explicit-definition" for c in claims)}
    checkpoint()
    print(json.dumps(report["summary"]), flush=True)


if __name__ == "__main__":
    main()
