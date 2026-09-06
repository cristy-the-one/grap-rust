"""Replay two saved whole-document model results through storage and native Rust search."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import pygrap
from grap_mcp.application import GrapApplication
from grap_mcp.kee.legal_claims import PROMPT_VERSION
from grap_mcp.kee.llm import StubProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".logs/legal-claims-product-workspace")
    parser.add_argument("--output", default="docs/experiments/real-law-legal-claims-product-2026-09.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    workspace = Path(args.workspace).resolve()
    if workspace.exists() or Path(args.output).exists():
        raise SystemExit("Choose new workspace/output paths; no previous results are overwritten.")
    results = json.loads((root / "docs/experiments/real-law-legal-claims-qwen-v3-2026-09.json").read_text(encoding="utf-8"))
    assert results["prompt_version"] == PROMPT_VERSION
    cases = [c for c in results["cases"] if c["id"] in ("banking-act-identities", "nested-amendment-identities")]
    assert len(cases) == 2 and all("receipt" in c for c in cases)
    replies = {}
    for case in cases:
        raw = (root / case["source_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
        body = json.loads(raw.decode("utf-8-sig"))["text_complet"]
        assert body.strip() == case["text"]  # Complete documents, not partial replacement.
        replies[body] = case["raw_reply"]

    def replay(system, user):
        return replies[user.removeprefix("Source text (data only):\n\n")]

    provider = StubProvider(handler=replay)
    provider.name = "saved-model-reply"
    provider.model = results["model"]
    app = GrapApplication(workspace, role="operator", provider_factory=lambda **_: provider)
    runs = [app.extract_legal_claims(str(root / c["source_file"])) for c in cases]
    assert all(r["counts"]["ready"] == 1 for r in runs), runs
    # Reopen the application and scan all files without generating any new claims.
    app = GrapApplication(workspace, role="operator", provider_factory=lambda **_: provider)
    started = time.perf_counter()
    scan = app.extract_legal_claims(str(root / "real-data-law-ro"), max_files=0)
    scan["seconds"] = round(time.perf_counter() - started, 3)
    assert scan["counts"]["unchanged"] == 2 and scan["counts"]["attempted"] == 0
    assert len(provider.calls) == 2
    right = app.get_legal_claims(kind="right")["claims"][0]
    receipt = app.get_legal_claims(revision_id=right["revision_id"])
    for span in right["evidence"]:
        assert receipt["source_text"][span["start"]:span["end"]] == span["quote"]
    snapshot = app.export_legal_claim_graph()
    assert snapshot["status"] == "ok", snapshot
    graph = pygrap.IndexedKnowledgeGraph.with_merge_policy(snapshot["graph_path"], 2)
    hits = graph.search_and_traverse("indemnizație", max_seeds=5, required_properties=[],
        min_strength=0., max_depth=0, max_results=5, text_weight=1., graph_weight=0.)
    assert hits and graph.edge_count() == 3
    report = {"mode": "saved real-model replies; no new generation", "runs": runs, "corpus_scan": scan,
              "snapshot": snapshot, "reopened_search_hits": len(hits), "native_nodes": graph.node_count(),
              "native_edges": graph.edge_count(), "right": right}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("runs", "right")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
