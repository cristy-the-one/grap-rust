"""Local Ollama phase timings through the real extraction/parser boundary.

Uses native /api/chat solely to obtain server phase durations. Both experiment
arms use this same transport; this does not assume native and compatible APIs
have identical latency. Sources and production graphs are read-only.
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
from grap_mcp.kee.llm import OpenAICompatProvider, _check_finish_reason

COMPACT = "\nEmit compact JSON: no indentation or optional whitespace outside strings. Preserve all required fields, complete string values, evidence and legal classifications."
ROOT = Path(__file__).resolve().parents[2]


class ProfileProvider(OpenAICompatProvider):
    def __init__(self, model):
        super().__init__(model=model, extra_body={"reasoning_effort": "none"}, timeout=180)
        self.calls = []
        self.max_tokens = 2048
        self.retries = 0

    def complete(self, system, user):
        payload = {"model": self.model, "stream": False, "think": False,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": 0, "seed": 42, "num_predict": self.max_tokens}}
        response_format = self.extra_body.get("response_format", {})
        if response_format.get("type") == "json_schema":
            payload["format"] = response_format["json_schema"]["schema"]
        started = time.perf_counter()
        req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                     data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            body = json.load(response)
        row = {"purpose": "extraction" if system.startswith("You are an information-extraction") else "classification",
               "wall_s": time.perf_counter() - started,
               "prompt_sha256": hashlib.sha256((system + "\0" + user).encode()).hexdigest(),
               "prompt_chars": len(system) + len(user), "raw": body["message"]["content"],
               "finish_reason": body.get("done_reason"),
               **{key: body.get(key) for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration", "prompt_eval_count", "eval_count")}}
        self.calls.append(row)
        _check_finish_reason({"finish_reason": body.get("done_reason")})
        return row["raw"]


def run(args):
    fixture_path = ROOT / args.fixture
    fixture = json.loads(fixture_path.read_bytes())
    provider = ProfileProvider(args.model)
    provider.complete("Return JSON.", 'Return {"ready":true}.')
    report = {"model": args.model, "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
              "transport": "Ollama native chat; temperature 0, seed 42, think false, max output 2048, no retries",
              "warmup": provider.calls[:], "compact_instruction": COMPACT, "rows": []}
    report["code_sha256"] = hashlib.sha256(Path(extract.__file__).read_bytes()).hexdigest()
    original = extract.SYSTEM_PROMPT
    original_legal = extract.LEGAL_SYSTEM_PROMPT
    old_structured = os.environ.get("GRAP_KEE_STRUCTURED_OUTPUT")
    try:
        for index, case in enumerate(fixture["cases"]):
            raw = (ROOT / case["source_file"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
            text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
            plan = qualified.plan(text, str((ROOT / case["source_file"]).resolve()))
            chunks = case["arms"]["statements"]
            candidate = args.candidate
            for mode in (["baseline", candidate] if (index + args.reverse) % 2 == 0 else [candidate, "baseline"]):
                os.environ["GRAP_KEE_STRUCTURED_OUTPUT"] = "1" if mode == "structured" else "0"
                extract.SYSTEM_PROMPT = original + (COMPACT if mode == "compact" else "")
                extract.LEGAL_SYSTEM_PROMPT = original_legal + (COMPACT if mode == "compact" else "")
                fragments, calls, failures = [], [], []
                started = time.perf_counter()
                for chunk in chunks:
                    before = len(provider.calls)
                    context = qualified.slice_plan(plan, chunk["start"], chunk["end"])
                    frag, failure = extract.extract_chunk(provider, chunk["id"], chunk["text"], rule_context=context)
                    calls.extend(copy.deepcopy(provider.calls[before:]))
                    if frag is not None:
                        fragments.append(frag)
                    if failure:
                        failures.append(failure)
                rules = [r for f in fragments for r in f.get("qualified_rules", [])]
                targets = {r["id"]: r for r in rules if case["text"] in r["statement"]}
                correct = len(targets) == 1 and all(r["kind"] == case["expected_kind"]
                    and r["subject"] in case["accepted_subjects"]
                    and all(q in r["statement"] for q in case["required_text"])
                    and all(text[s["start"]:s["end"]] == s["quote"] for s in r["evidence"])
                    for r in targets.values())
                row = {"case": case["id"], "mode": mode, "wall_s": time.perf_counter() - started,
                       "correct_target": correct, "failures": failures, "calls": calls,
                       "fragments": fragments}
                report["rows"].append(row)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({key: row[key] for key in ("case", "mode", "wall_s", "correct_target", "failures")}), flush=True)
    finally:
        extract.SYSTEM_PROMPT = original
        extract.LEGAL_SYSTEM_PROMPT = original_legal
        if old_structured is None:
            os.environ.pop("GRAP_KEE_STRUCTURED_OUTPUT", None)
        else:
            os.environ["GRAP_KEE_STRUCTURED_OUTPUT"] = old_structured


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="grap-legal-gemma4")
    parser.add_argument("--fixture", default="docs/experiments/qualified-rules-additional-cases-2026-09.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--candidate", choices=["compact", "structured"], default="compact")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Choose a fresh output path")
    run(args)
