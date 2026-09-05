"""Small, source-labeled extraction check against a local model.

The cases are synthetic development checks, not an independent acceptance set.
Compare prompt versions using the same provider, parser and Rust-backed resolver.
Preserve raw replies for review; expected relations measure selected recall only.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import time
import types

from grap_mcp.kee import extract
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.llm import OpenAICompatProvider, StubProvider
from grap_mcp.kee.working_graph import WorkingGraph


def norm(value):
    value = value.strip().lower()
    if value.startswith("the "):
        value = value[4:]
    return {"use": "uses", "issue": "issues", "amend": "amends", "require": "requires"}.get(value, value)


def triple(row):
    return tuple(norm(value) for value in row)


def score(case, raw):
    frag, reason = extract.extract_chunk(StubProvider(default=raw), case["id"], case["text"])
    if frag is None:
        return {"parsed": False, "failure_reason": reason, "expected": len(case.get("relations", [])),
                "expected_emitted": 0, "expected_retained": 0, "forbidden_assertions": 0,
                "requirements_expected": len(case.get("requirements", [])),
                "requirements_retained": 0, "unwanted_requirement_concepts": 0,
                "forbidden_requirements": 0,
                "claims": 0, "quoted_claims": 0, "rejected_quotes": 0,
                "empty_correct": False if case.get("empty") else None}
    emitted = {triple((r["from"], r["type"], r["to"])) for r in frag["relations"]}
    with WorkingGraph(HashingEmbedder(64), merge_threshold=1.1) as wg:
        wg.upsert_fragment(frag, case["id"])
        retained = {triple((wg.canonical[e["from_id"]], e["type"], wg.canonical[e["to_id"]]))
                    for e in wg.relations}
    expected = {triple(r) for r in case.get("relations", [])}
    forbidden = {triple(r) for r in case.get("forbidden", [])}
    names = {norm(o["name"]) for o in frag["outcomes"]}
    endpoints = {norm(r[key]) for r in frag["relations"] for key in ("from", "to")}
    blocked_names = {norm(n) for n in case.get("forbidden_entities", [])}
    claims = frag["outcomes"] + frag["relations"]
    by_name = {}
    for outcome in frag["outcomes"]:
        by_name.setdefault(norm(outcome["name"]), []).extend(outcome["characteristics"])
    requirements = case.get("requirements", [])
    return {"parsed": True, "failure_reason": None, "expected": len(expected),
            "expected_emitted": len(expected & emitted), "expected_retained": len(expected & retained),
            "forbidden_assertions": len(forbidden & emitted) + len(blocked_names & (names | endpoints)),
            "requirements_expected": len(requirements),
            "requirements_retained": sum(any(term.lower() in value.lower()
                                               for value in by_name.get(norm(name), []))
                                         for name, term in requirements),
            "unwanted_requirement_concepts": sum(bool(by_name.get(norm(name)))
                                                  for name in case.get("no_requirements", [])),
            "forbidden_requirements": sum(any(term.lower() in value.lower()
                                                for value in by_name.get(norm(name), []))
                                          for name, term in case.get("forbidden_requirements", [])),
            "claims": len(claims), "quoted_claims": sum(c["evidence"]["status"] == "quoted" for c in claims),
            "rejected_quotes": frag["rejected_evidence"],
            "empty_correct": not claims if case.get("empty") else None}


def summarize(rows):
    for model in dict.fromkeys(r["model"] for r in rows):
        for arm in ("before", "after"):
            group = [r for r in rows if r["model"] == model and r["arm"] == arm]
            sums = {key: sum(r.get(key, 0) for r in group) for key in
                    ("expected", "expected_emitted", "expected_retained", "forbidden_assertions", "claims", "quoted_claims",
                     "requirements_expected", "requirements_retained", "unwanted_requirement_concepts")}
            print(json.dumps({"model": model, "arm": arm, "parsed": sum(r["parsed"] for r in group), **sums,
                              "median_seconds": statistics.median(r["seconds"] for r in group)}), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    ap.add_argument("--models", nargs="+", default=["llama3.1:8b-instruct-q4_K_M"])
    ap.add_argument("--baseline", default="bd27c20")
    ap.add_argument("--output", required=True)
    ap.add_argument("--cases", type=Path, default=Path(__file__).with_name("extraction-cases.json"))
    ap.add_argument("--rescore", help="Score saved raw replies without calling a model")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.rescore:
        rows = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        by_id = {case["id"]: case for case in cases}
        for row in rows:
            row.update(score(by_id[row["case"]], row["raw"]))
        Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        summarize(rows)
        return
    old = types.ModuleType("old_extract")
    source = subprocess.check_output(["git", "show", f"{args.baseline}:mcp-server/grap_mcp/kee/extract.py"], cwd=root)
    exec(compile(source.decode("utf-8"), "baseline_extract.py", "exec"), old.__dict__)
    rows = []
    for model in args.models:
        provider = OpenAICompatProvider(base_url=args.base_url, model=model, timeout=120)
        provider.retries = 0
        provider.max_tokens = 1200
        # Exclude initial model loading from the comparison.
        provider.complete("Reply briefly.", "Ready?")
        for arm, prompt in (("before", old.SYSTEM_PROMPT), ("after", extract.SYSTEM_PROMPT)):
            for case in cases:
                start = time.perf_counter()
                raw = provider.complete(prompt, extract.build_user_prompt(case["text"]))
                row = {"model": model, "arm": arm, "case": case["id"],
                       "seconds": round(time.perf_counter() - start, 3),
                       "raw": raw, **score(case, raw)}
                rows.append(row)
                Path(args.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                print(model, arm, case["id"], row["seconds"], flush=True)
    summarize(rows)


if __name__ == "__main__":
    main()
