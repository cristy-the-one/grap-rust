"""Controlled runtime experiments; frozen graph-context prompts, no graph writes.

Warm state and inference options are varied without changing instructions.
The source fixture remains development data, not independent legal validation.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request

from grap_mcp.kee import extract, qualified
from grap_mcp.kee.embeddings import OpenAICompatEmbedder
from grap_mcp.kee.working_graph import WorkingGraph
from profile_llm_ingestion import ProfileProvider

ROOT = Path(__file__).resolve().parents[2]
MODEL = "grap-legal-gemma4"
SCREEN = ("semantic-02", "semantic-06", "semantic-09", "semantic-13")
CONFIGS = {
    "greedy": {"temperature": 0, "top_p": 0.95, "num_ctx": 16384},
    "mild": {"temperature": 0.2, "top_p": 0.95, "num_ctx": 16384},
    "recommended": {"temperature": 1.0, "top_p": 0.95, "num_ctx": 16384},
    "context8k": {"temperature": 0, "top_p": 0.95, "num_ctx": 8192},
    "batch128": {"temperature": 0, "top_p": 0.95, "num_ctx": 16384, "num_batch": 128},
    "units_first": {"temperature": 0, "top_p": 0.95, "num_ctx": 16384},
}


def native(payload):
    started = time.perf_counter()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as response:
        result = json.load(response)
    result["client_wall_s"] = time.perf_counter() - started
    return result


def prepare(path):
    receipts = json.loads((ROOT / "docs/experiments/llm-ingress-confirmed.json").read_bytes())["runs"][0]["result"]["ontology"]["extraction_evidence"]
    cases = []
    for file in ("qualified-rules-additional-cases-2026-09.json", "chunking-accuracy-cases-2026-09.json"):
        cases.extend(json.loads((ROOT / "docs/experiments" / file).read_bytes())["cases"])
    rows = []
    with WorkingGraph(OpenAICompatEmbedder(model="bge-m3")) as graph:
        for case in cases:
            raw = (ROOT / case["source_file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
            text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
            plan = qualified.plan(text, str((ROOT / case["source_file"]).resolve()))
            for chunk in case["arms"]["statements"]:
                source = case["id"] + ":" + chunk["id"]
                context = qualified.slice_plan(plan, chunk["start"], chunk["end"])
                provider = extract.configure_provider(ProfileProvider(MODEL), context)
                user = extract.build_user_prompt_with_context(chunk["text"], graph.context_for(chunk["text"]), rule_context=context)
                rows.append({"case": case, "source": source, "text": chunk["text"], "context": context,
                             "system": extract.system_prompt(context, provider), "user": user,
                             "response_format": provider.extra_body["response_format"]})
                graph.upsert_fragment(copy.deepcopy(receipts[source]), source)
    path.write_text(json.dumps(rows, ensure_ascii=False) + "\n", encoding="utf-8")
    return rows


def score(row, fragment):
    case = row["case"]
    rules = (fragment or {}).get("qualified_rules", [])
    targets = [r for r in rules if case["text"] in r["statement"]]
    return {"target_correct": len(targets) == 1 and all(r["kind"] == case["expected_kind"]
                and r["subject"] in case["accepted_subjects"]
                and all(q in r["statement"] for q in case["required_text"]) for r in targets),
            "unresolved": (fragment or {}).get("rule_coverage", {}).get("unresolved", 0),
            "entity_names": [o["name"] for o in (fragment or {}).get("outcomes", [])],
            "source_spans_valid": all(row["context"]["text"][s["start"]:s["end"]] == s["quote"] for r in rules
                                      for s in r["evidence"] + r.get("context_evidence", [])),
            "rejected_evidence": (fragment or {}).get("rejected_evidence", 0)}


def run(args):
    os.environ["GRAP_KEE_STRUCTURED_OUTPUT"] = "1"
    rows = json.loads(args.prompts.read_bytes()) if args.prompts.exists() else prepare(args.prompts)
    selected = rows if args.all_cases else [r for r in rows if r["case"]["id"] in SCREEN]
    if args.case_limit:
        selected = selected[:args.case_limit]
    report = {"model": MODEL, "prompt_file_sha256": hashlib.sha256(args.prompts.read_bytes()).hexdigest(),
              "mode": args.mode, "rows": []}
    names = args.configs.split(",")
    for name in names:
        options = {**CONFIGS[name], "seed": args.seed}
        conditions = ["cold", "preload", "prefix"] if args.mode == "warmup" else ["resident"]
        for condition in conditions:
            if args.mode == "warmup":
                native({"model": MODEL, "keep_alive": 0})
            preparation = None
            if condition in ("preload", "resident"):
                preparation = native({"model": MODEL, "options": options, "keep_alive": "10m"})
            elif condition == "prefix":
                preparation = native({"model": MODEL, "options": {**options, "num_predict": 1}, "think": False,
                    "stream": False, "messages": [{"role": "system", "content": selected[0]["system"]},
                                                   {"role": "user", "content": "{}"}], "keep_alive": "10m"})
            for row in selected:
                provider = ProfileProvider(MODEL, options=options, think=args.think)
                provider.extra_body["response_format"] = copy.deepcopy(row["response_format"])
                if name == "units_first":
                    schema = provider.extra_body["response_format"]["json_schema"]["schema"]
                    schema["properties"] = {key: schema["properties"][key] for key in ("units", "outcomes", "relations")}
                    schema["required"] = ["units", "outcomes", "relations"]
                started = time.perf_counter()
                before_repair, fragment, failure = None, None, None
                try:
                    raw = provider.complete(row["system"], row["user"])
                    first, failure = extract.parse_reply(raw, row["source"], row["text"], provider, row["context"])
                    before_repair = score(row, first)
                    fragment, failure = extract.finalize_reply(raw, row["source"], row["text"], provider, row["context"])
                except Exception as exc:
                    failure = str(exc)
                result = {"config": name, "condition": condition, "seed": args.seed,
                          "case": row["case"]["id"], "wall_s": time.perf_counter() - started,
                          "first_pass": before_repair, "score": score(row, fragment), "failure": failure,
                          "calls": provider.calls, "fragment": fragment}
                result["schema_wire_sha256"] = hashlib.sha256(json.dumps(provider.extra_body["response_format"]).encode()).hexdigest()
                if preparation is not None:
                    result["preparation"] = {k: v for k, v in preparation.items() if k != "message"}
                    preparation = None
                report["rows"].append(result)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({"config": name, "condition": condition, "case": result["case"],
                    "seconds": result["wall_s"], "correct": result["score"]["target_correct"],
                    "calls": len(provider.calls), "unresolved": result["score"]["unresolved"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", type=Path, default=Path(".logs/gemma-frozen-prompts.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--configs", default="greedy,mild,recommended,context8k,batch128")
    parser.add_argument("--mode", choices=["screen", "warmup"], default="screen")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--all-cases", action="store_true")
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--case-limit", type=int)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a fresh output file")
    run(args)
