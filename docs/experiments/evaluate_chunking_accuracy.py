"""Paired, source-frozen KEE chunking probe; semantic judgments are separate.

prepare freezes source spans and the existing reference claims before inference.
run checkpoints every local-model response and never writes an extraction store.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp-server"))
from grap_mcp.ingest import chunk_text
from grap_mcp.kee import extract
from grap_mcp.kee.chunking import chunk_spans
from grap_mcp.kee.tabular import is_tabular
from grap_mcp.kee.llm import OpenAICompatProvider
from compare_local_models import complete, save_report


def source_chunks(text, strategy):
    if strategy == "statements":
        return [(s.start, s.end) for s in chunk_spans(text, 800)]
    spans, cursor = [], 0
    for piece in chunk_text(text, max_chars=800):
        start = text.index(piece, cursor)
        cursor = start + len(piece)
        spans.append((start, cursor))
    return spans


def prepare(path):
    refs = json.loads((ROOT / "docs/experiments/real-law-semantic-cases.json").read_text(encoding="utf-8"))
    selected = {"semantic-01", "semantic-06", "semantic-09", "semantic-10", "semantic-13", "semantic-15"}
    cases = [c for c in refs["cases"] if c["id"] in selected]
    file = ROOT / "real-data-law-ro/17101.json"
    raw = file.read_bytes()
    text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
    start = text.index("Comisia centrala de arbitraj se numeşte")
    end = text.index("potrivit legii.", start) + len("potrivit legii.")
    cases.append({"id": "article-59", "source_file": "real-data-law-ro/17101.json",
                  "source_sha256": hashlib.sha256(raw).hexdigest(), "start": start, "end": end,
                  "text": text[start:end], "expected_kind": "fact",
                  "accepted_subjects": ["Comisia centrala de arbitraj"],
                  "required_text": ["potrivit legii"],
                  "forbidden_interpretation": "Commission appoints itself or carries itself out"})
    for case in cases:
        raw = (ROOT / case["source_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
        text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
        assert text[case["start"]:case["end"]] == case["text"]
        case["arms"] = {}
        for arm in ("legacy", "statements"):
            case["arms"][arm] = [
                {"id": f"{Path(case['source_file']).stem}#{i}", "start": start, "end": end,
                 "text": text[start:end], "tabular_skipped": is_tabular(text[start:end])}
                for i, (start, end) in enumerate(source_chunks(text, arm))
                if start < case["end"] and end > case["start"]]
    fixture = {"scope": "Purposive seven-case diagnostic, not representative corpus accuracy; existing assistant-authored references, not expert legal labels.",
               "selection": "Six existing reference cases spanning obligations, exceptions, prohibitions and institutional facts, plus the known Article 59 truncation. All full-document chunks intersecting the reference span; no synthetic chunk phase or text edits.",
               "rubric": "Per target/arm: faithful only when emitted propositions preserve the governed subject, operative meaning and all material qualifications; partial when some target content survives but qualifications/meaning are lost; absent when not recovered. Record target-related unsupported claims separately. Exact quotes alone do not establish semantic correctness. Review normalized fragments across all intersecting chunks; no credit for content present only in quotes. No assertion of current legal validity.",
               "target_chars": 800, "cases": cases}
    save_report(path, fixture)


def run(fixture_path, output):
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    for case in fixture["cases"]:
        raw = (ROOT / case["source_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
        text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
        for chunks in case["arms"].values():
            for chunk in chunks:
                assert text[chunk["start"]:chunk["end"]] == chunk["text"]
    settings = {"model": "grap-legal-gemma4", "temperature": 0, "max_tokens": 2048,
                "extra_body": {"reasoning_effort": "none", "seed": 20260908},
                "context": "Stateless extraction to isolate chunk boundaries; no known-graph feedback or embedding merges"}
    identity = {"fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                "settings": settings, "system_prompt": extract.SYSTEM_PROMPT,
                "prompt_version": extract.PROMPT_VERSION}
    report = {**identity, "rows": []}
    if output.exists():
        report = json.loads(output.read_text(encoding="utf-8"))
        if any(report[k] != v for k, v in identity.items()):
            raise ValueError("Cannot resume changed fixture/settings/prompt")
    p = OpenAICompatProvider(model=settings["model"], timeout=180,
                             temperature=0, extra_body=settings["extra_body"])
    p.max_tokens = settings["max_tokens"]
    p.retries = 0
    done = {(r["case"], r["arm"], r["chunk"]) for r in report["rows"]}
    for i, case in enumerate(fixture["cases"]):
        # Alternate arm order so one arm is not systematically colder.
        for arm in (("legacy", "statements") if i % 2 == 0 else ("statements", "legacy")):
            for chunk in case["arms"][arm]:
                key = (case["id"], arm, chunk["id"])
                if key in done:
                    continue
                row = dict(zip(("case", "arm", "chunk"), key))
                if chunk["tabular_skipped"]:
                    row["skipped"] = "table_filter"
                else:
                    try:
                        row.update(complete(p, extract.SYSTEM_PROMPT, extract.build_user_prompt(chunk["text"])))
                        if "incomplete" not in row:
                            row["fragment"], row["parse_failure"] = extract.parse_reply(row["raw"], chunk["id"], chunk["text"], p)
                    except Exception as exc:
                        row["error"] = str(exc)
                report["rows"].append(row)
                save_report(output, report)
                print(json.dumps({k: v for k, v in row.items() if k not in ("raw", "fragment")}), flush=True)


def audit(fixture_path, results_path, judgments_path, output):
    """Replay saved fragments through Rust without models or fuzzy entity merging."""
    from collections import Counter
    from grap_mcp.kee.embeddings import HashingEmbedder
    from grap_mcp.kee.working_graph import WorkingGraph
    fixture_bytes, result_bytes = fixture_path.read_bytes(), results_path.read_bytes()
    fixture, results = json.loads(fixture_bytes), json.loads(result_bytes)
    judgments = json.loads(judgments_path.read_text(encoding="utf-8"))
    if judgments.get("results_sha256") != hashlib.sha256(result_bytes).hexdigest():
        raise ValueError("Semantic judgments belong to different model results")
    if results["fixture_sha256"] != hashlib.sha256(fixture_bytes).hexdigest():
        raise ValueError("Result fixture mismatch")
    expected = {(c["id"], arm, z["id"]) for c in fixture["cases"] for arm, zs in c["arms"].items() for z in zs}
    actual = [(r["case"], r["arm"], r["chunk"]) for r in results["rows"]]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Missing or duplicate result rows")
    judgment_keys = [(j["case"], j["arm"]) for j in judgments["judgments"]]
    if len(judgment_keys) != len(set(judgment_keys)) or set(judgment_keys) != {(c, a) for c, a, _ in expected}:
        raise ValueError("Incomplete semantic judgments")
    summary, replays = {}, []
    for arm in ("legacy", "statements"):
        rows = [r for r in results["rows"] if r["arm"] == arm]
        fragments = [r["fragment"] for r in rows if r.get("fragment")]
        claims = [c for f in fragments for c in f["outcomes"] + f["relations"]]
        verdicts = Counter(j["verdict"] for j in judgments["judgments"] if j["arm"] == arm)
        summary[arm] = {"requests": sum("raw" in r or "error" in r for r in rows),
                        "fragments": len(fragments), "failures": sum(bool(r.get("error") or r.get("incomplete") or r.get("parse_failure")) for r in rows),
                        "table_skips": sum(bool(r.get("skipped")) for r in rows),
                        "normalized_claims": len(claims),
                        "quoted_claims": sum(c.get("evidence", {}).get("status") == "quoted" for c in claims),
                        "rejected_evidence": sum(f["rejected_evidence"] for f in fragments),
                        "structural_outcomes_dropped": sum(len(f["rejected_structural"]) for f in fragments),
                        "relations_before_rust": sum(len(f["relations"]) for f in fragments),
                        "seconds_including_cold_start_and_cache_effects": round(sum(r.get("seconds", 0) for r in rows), 3),
                        "completion_tokens": sum(r.get("usage", {}).get("completion_tokens", 0) for r in rows),
                        "target_verdicts": dict(verdicts)}
        for case in fixture["cases"]:
            with WorkingGraph(HashingEmbedder(64), merge_threshold=1.1) as wg:
                for row in rows:
                    if row["case"] == case["id"] and row.get("fragment"):
                        wg.upsert_fragment(row["fragment"], row["chunk"])
                replays.append({"case": case["id"], "arm": arm,
                                "relations": [{"from": wg.canonical[r["from_id"]], "type": r["type"],
                                               "to": wg.canonical[r["to_id"]]} for r in wg.relations],
                                "parked": wg.parked})
        summary[arm]["relations_after_rust"] = sum(len(r["relations"]) for r in replays if r["arm"] == arm)
    save_report(output, {"fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                         "results_sha256": hashlib.sha256(result_bytes).hexdigest(),
                         "judgments_sha256": hashlib.sha256(judgments_path.read_bytes()).hexdigest(),
                         "scope": "Semantic verdicts concern normalized fragments, not full graph-augmented KEE. Rust replay is a separate mechanical retention check with fuzzy merging disabled; no embedding model loaded.",
                         "summary": summary, "rust_replay": replays})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run", "audit"])
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--judgments", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.fixture.exists():
            parser.error("Choose a fresh fixture path")
        prepare(args.fixture)
    elif args.mode == "run":
        if not args.output:
            parser.error("run requires --output")
        run(args.fixture, args.output)
    else:
        if not all((args.output, args.results, args.judgments)):
            parser.error("audit requires --results, --judgments and --output")
        audit(args.fixture, args.results, args.judgments, args.output)
