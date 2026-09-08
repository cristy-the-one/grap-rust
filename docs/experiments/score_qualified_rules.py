"""Replay frozen live replies through the final parser and durable WorkingGraph."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.kee import extract, qualified, legal_rules
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.working_graph import WorkingGraph


def score(fixture, results, repairs=None):
    reports = []
    for case in fixture["cases"]:
        path = ROOT / case["source_file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["source_sha256"]
        text = json.loads(path.read_text(encoding="utf-8-sig"))["text_complet"]
        plan = qualified.plan(text, str(path.resolve()))
        for arm in ("legacy", "statements"):
            fragments = []
            for chunk in case["arms"][arm]:
                matches = [r for r in results["rows"] if (r["case"], r["arm"], r["chunk"]) == (case["id"], arm, chunk["id"])]
                assert len(matches) == 1
                row = matches[0]
                assert row["finish_reason"] == "stop" and "error" not in row and "incomplete" not in row
                raw = row["raw"]
                repaired = next((r["repair"] for r in (repairs or {}).get("rows", []) if (r["case"], r["arm"], r["chunk"]) == (row["case"], row["arm"], row["chunk"])), None)
                if repaired and "units" in repaired:
                    obj = extract.extract_json(raw)
                    obj["units"] = {**obj.get("units", {}), **repaired["units"]}
                    raw = json.dumps(obj, ensure_ascii=False)
                frag, failure = extract.parse_reply(raw, row["chunk"], chunk["text"], StubProvider(), qualified.slice_plan(plan, chunk["start"], chunk["end"]))
                assert failure is None
                fragments.append(frag)
            with tempfile.TemporaryDirectory(prefix="grap-qualified-eval-") as tmp:
                workdir = str(Path(tmp) / "graph")
                with WorkingGraph(HashingEmbedder(64), workdir=workdir, merge_threshold=1.1) as wg:
                    for frag in fragments:
                        wg.upsert_fragment(frag, frag["source"])
                    wg.save()
                with WorkingGraph.open(workdir, HashingEmbedder(64), merge_threshold=1.1) as wg:
                    exported = wg.to_candidate_ontology()["qualified_legal"]
                    targets = [r for r in exported["rules"] if case["text"] in r["statement"]]
                    spans_valid = all(text[s["start"]:s["end"]] == s["quote"] for r in targets for s in r["evidence"])
                    faithful = len(targets) == 1 and spans_valid and all(
                        r["kind"] == case["expected_kind"] and r["subject"] in case["accepted_subjects"]
                        and r["classification_status"] != "conflict" and all(q in r["statement"] for q in case["required_text"])
                        for r in targets)
                    reports.append({"case": case["id"], "arm": arm, "full_statement_retained": bool(targets),
                                    "kind_subject_and_qualifications_correct": faithful,
                                    "target_rules": targets, "all_rules": exported,
                                    "relations": [{"from": wg.canonical[e["from_id"]], "type": e["type"], "to": wg.canonical[e["to_id"]]} for e in wg.relations]})
    return reports


if __name__ == "__main__":
    import argparse
    root = ROOT / "docs/experiments"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=root / "chunking-accuracy-cases-2026-09.json")
    parser.add_argument("--results", type=Path, default=root / "qualified-rules-results-2026-09.json")
    parser.add_argument("--output", type=Path, default=root / "qualified-rules-audit-2026-09.json")
    parser.add_argument("--repairs", type=Path)
    args = parser.parse_args()
    fixture_path, results_path = args.fixture, args.results
    repairs = json.loads(args.repairs.read_bytes()) if args.repairs else None
    if repairs:
        assert repairs["results_sha256"] == hashlib.sha256(results_path.read_bytes()).hexdigest()
    rows = score(json.loads(fixture_path.read_bytes()), json.loads(results_path.read_bytes()), repairs)
    output = {"scope": "Development regression against seven pre-existing source targets. Live generation with v5 prompt; saved replies reparsed with final qualification/filter fixes. Faithful now means complete source statement + correct kind and literal governed subject; not a flat-triple or population-accuracy score. All rows were saved, reopened and exported through WorkingGraph without fuzzy merging.",
              "results_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
              "repairs_sha256": hashlib.sha256(args.repairs.read_bytes()).hexdigest() if args.repairs else None,
              "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
              "code_sha256": {str(Path(m.__file__).relative_to(ROOT)): hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in (extract, qualified, legal_rules)},
              "summary": {a: {"targets": sum(r["arm"] == a for r in rows),
                                "full_statement_retained": sum(r["arm"] == a and r["full_statement_retained"] for r in rows),
                                "kind_subject_and_qualifications_correct": sum(r["arm"] == a and r["kind_subject_and_qualifications_correct"] for r in rows)} for a in ("legacy", "statements")},
              "rows": rows}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"]))
