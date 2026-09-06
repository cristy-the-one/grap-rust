"""One bounded live pass; rerun in a new process with the same workspace to resume."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from grap_mcp.application import GrapApplication
from grap_mcp.kee.legal_rules import plan_rules
from exercise_legal_rules_live import RecordingProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-batches", type=int, required=True)
    parser.add_argument("--model", default="gemma4:latest")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = Path(args.output).resolve()
    if out.exists():
        raise SystemExit("Choose a fresh report path; prior reports are preserved.")
    cases = json.loads((root / "docs/experiments/real-law-resume-cases.json").read_text(encoding="utf-8"))
    case = next(c for c in cases if c["source_file"] == args.source)
    file = root / args.source
    raw = file.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
    body = json.loads(raw.decode("utf-8-sig"))["text_complet"]
    batches, _ = plan_rules(body)
    p = RecordingProvider(model=args.model, extra_body={"reasoning_effort": "none"})
    p.retries, p.max_tokens = 0, 2048
    app = GrapApplication(Path(args.workspace).resolve(), role="operator", provider_factory=lambda **_: p)
    before = app.get_legal_sources()["sources"]
    started = time.perf_counter()
    result = app.extract_legal_claims(str(file), model=args.model, max_batches=args.max_batches,
                                    max_chars=100000, max_files=1, refresh=args.refresh)
    seconds = round(time.perf_counter() - started, 3)
    assert len(p.calls) == result["batches"]["attempted"] <= args.max_batches
    source = app.get_legal_sources()["sources"][0]
    history = app.get_legal_sources(source_id=source["source_id"])
    claims = app.get_legal_claims(limit=200)
    report = {"mode": "one live pass in a separate process", "case": case, "model": args.model,
              "planned_batches": len(batches),
              "before": before, "result": result, "seconds": seconds, "history": history,
              "current_claim_count": claims["total"], "calls": p.calls}
    report["snapshot"] = app.export_legal_claim_graph()
    assert report["snapshot"]["status"] == "ok"
    if source["status"] != "ready":
        assert claims["total"] == 0 and report["snapshot"]["sources"] == 0
    else:
        receipt = app.get_legal_claims(revision_id=source["accepted_revision"])
        report["receipt"] = receipt
        assert receipt["source_text"] == body
        assert receipt["completion"]["completed_batches"] == len(batches)
        assert len(receipt["units"]) == sum(len(batch) for batch in batches)
        for claim in receipt["claims"]:
            for span in [*claim["evidence"], *claim["context_evidence"],
                         *([claim["subject_evidence"]] if "subject_evidence" in claim else []),
                         *([claim["provision_evidence"]] if claim["provision_evidence"] else [])]:
                assert body[span["start"]:span["end"]] == span["quote"]
        import pygrap
        graph = pygrap.IndexedKnowledgeGraph.with_merge_policy(report["snapshot"]["graph_path"], 2)
        assert graph.node_count() == claims["total"] + 1 and graph.edge_count() == claims["total"]
        report["native"] = {"nodes": graph.node_count(), "edges": graph.edge_count()}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source": args.source, "seconds": seconds, "counts": result["counts"],
                      "batches": result["batches"], "claims": claims["total"]}), flush=True)


if __name__ == "__main__":
    main()
