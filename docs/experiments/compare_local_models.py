"""Compare installed models through the project's HTTP payload and Rust resolver.

Usage: set PYTHONPATH=mcp-server, then run with --models-config and --output.
Models run sequentially; raw replies and request settings are saved for review.
No downloads, model installation, or application configuration changes.
"""
import argparse
import json
from pathlib import Path
import statistics
import time
import urllib.request

from evaluate_extraction import score
from grap_mcp.kee import extract
from grap_mcp.kee.llm import OpenAICompatProvider


def complete(provider, system, user):
    payload = provider._payload(system, user, stream=False)
    request = urllib.request.Request(provider.base_url + "/chat/completions",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=provider.timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    choice = body["choices"][0]
    message = choice["message"]
    return {"raw": message.get("content") or "", "seconds": round(elapsed, 3),
            "finish_reason": choice.get("finish_reason"), "usage": body.get("usage"),
            "reasoning_characters": len(message.get("reasoning") or message.get("reasoning_content") or "")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=Path, nargs="+", default=[
        Path(__file__).with_name("extraction-cases.json"),
        Path(__file__).with_name("extraction-requirement-cases.json")])
    args = parser.parse_args()
    cases = [case for path in args.cases for case in json.loads(path.read_text(encoding="utf-8"))]
    config = json.loads(args.models_config.read_text(encoding="utf-8"))
    report = {"prompt_version": extract.PROMPT_VERSION, "system_prompt": extract.SYSTEM_PROMPT,
              "cases": cases, "config": config, "runs": []}
    for spec in config:
        provider = OpenAICompatProvider(base_url=spec.get("base_url", "http://127.0.0.1:11434/v1"),
                                       model=spec["model"], timeout=spec.get("timeout", 90),
                                       extra_body=spec.get("extra_body", {}))
        provider.max_tokens = spec.get("max_tokens", 2048)
        run = {"model": spec["model"], "label": spec.get("label", spec["model"]), "rows": []}
        report["runs"].append(run)
        try:
            warm = complete(provider, "Return ONLY JSON.", 'Return {"ready":true}.')
            run["warmup_seconds"] = warm["seconds"]
        except Exception as exc:
            run["warmup_error"] = str(exc)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(run["label"], "warmup failed:", str(exc), flush=True)
            continue
        for case in cases:
            started = time.perf_counter()
            try:
                reply = complete(provider, extract.SYSTEM_PROMPT, extract.build_user_prompt(case["text"]))
            except Exception as exc:
                reply = {"raw": "", "seconds": round(time.perf_counter() - started, 3), "error": str(exc)}
            row = {"case": case["id"], **reply, **score(case, reply["raw"])}
            run["rows"].append(row)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(run["label"], case["id"], row["seconds"], row["parsed"], flush=True)
            if "error" in reply:
                break  # a failed server/model is not retried for the entire corpus
        keys = ("expected", "expected_retained", "forbidden_assertions", "requirements_expected",
                "requirements_retained", "unwanted_requirement_concepts", "forbidden_requirements", "claims", "quoted_claims")
        run["summary"] = {k: sum(r[k] for r in run["rows"]) for k in keys}
        run["summary"].update(parsed=sum(r["parsed"] for r in run["rows"]),
                              cases=len(run["rows"]), median_seconds=statistics.median(r["seconds"] for r in run["rows"]),
                              empty_correct=all(r["empty_correct"] for r in run["rows"] if r["empty_correct"] is not None))
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(run["label"], json.dumps(run["summary"]), flush=True)


if __name__ == "__main__":
    main()
