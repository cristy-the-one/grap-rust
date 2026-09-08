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
from grap_mcp.kee.llm import OpenAICompatProvider, _check_finish_reason


def save_report(path, report):
    """Keep the previous complete checkpoint if interrupted during a write."""
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(6):
        try:
            staging.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            # Windows indexers briefly hold freshly written result files.
            time.sleep(0.05 * (attempt + 1))


def summarize(rows):
    keys = ("expected", "expected_retained", "forbidden_assertions", "requirements_expected",
            "requirements_retained", "unwanted_requirement_concepts", "forbidden_requirements", "claims", "quoted_claims")
    empty = [r["empty_correct"] for r in rows if r["empty_correct"] is not None]
    return {**{k: sum(r[k] for r in rows) for k in keys},
            "parsed": sum(r["parsed"] for r in rows), "cases": len(rows),
            "median_seconds": statistics.median(r["seconds"] for r in rows) if rows else None,
            "empty_correct": all(empty) if empty else None}


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
    result = {"raw": message.get("content") or "", "seconds": round(elapsed, 3),
            "finish_reason": choice.get("finish_reason"), "usage": body.get("usage"),
            "reasoning_characters": len(message.get("reasoning") or message.get("reasoning_content") or "")}
    try:
        _check_finish_reason(choice)
    except RuntimeError as exc:
        result["incomplete"] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="Continue saved settings/cases without repeating completed requests")
    parser.add_argument("--prompt-file", type=Path, help="Experimental prompt; does not change the application default")
    parser.add_argument("--cases", type=Path, nargs="+", default=[
        Path(__file__).with_name("extraction-cases.json"),
        Path(__file__).with_name("extraction-requirement-cases.json")])
    args = parser.parse_args()
    cases = [case for path in args.cases for case in json.loads(path.read_text(encoding="utf-8"))]
    config = json.loads(args.models_config.read_text(encoding="utf-8"))
    system_prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else extract.SYSTEM_PROMPT
    prompt_version = "experimental:" + args.prompt_file.name if args.prompt_file else extract.PROMPT_VERSION
    report = {"prompt_version": prompt_version, "system_prompt": system_prompt,
              "cases": cases, "config": config, "runs": []}
    if len({case["id"] for case in cases}) != len(cases):
        parser.error("case ids must be unique")
    if args.output.exists():
        if not args.resume:
            parser.error("output exists; use --resume or choose a new output")
        saved = json.loads(args.output.read_text(encoding="utf-8"))
        if any(saved.get(key) != report[key] for key in ("prompt_version", "system_prompt", "cases", "config")):
            parser.error("cannot resume: prompt, cases or model settings changed")
        report = saved
    for index, spec in enumerate(config):
        provider = OpenAICompatProvider(base_url=spec.get("base_url", "http://127.0.0.1:11434/v1"),
                                       model=spec["model"], timeout=spec.get("timeout", 90),
                                       extra_body=spec.get("extra_body", {}))
        provider.max_tokens = spec.get("max_tokens", 2048)
        if index < len(report["runs"]):
            run = report["runs"][index]
        else:
            run = {"model": spec["model"], "label": spec.get("label", spec["model"]), "rows": []}
            report["runs"].append(run)
        # Transport errors remain recorded; a resumed run retries just those cases.
        completed = {row["case"] for row in run["rows"] if "error" not in row}
        pending = [case for case in cases if case["id"] not in completed]
        if not pending:
            run["summary"] = summarize(run["rows"])
            save_report(args.output, report)
            continue
        try:
            warm = complete(provider, "Return ONLY JSON.", 'Return {"ready":true}.')
            run["warmup_seconds"] = warm["seconds"]
            run.pop("warmup_error", None)
        except Exception as exc:
            run["warmup_error"] = str(exc)
            save_report(args.output, report)
            print(run["label"], "warmup failed:", str(exc), flush=True)
            continue
        for case in pending:
            started = time.perf_counter()
            try:
                reply = complete(provider, system_prompt, extract.build_user_prompt(case["text"]))
            except Exception as exc:
                reply = {"raw": "", "seconds": round(time.perf_counter() - started, 3), "error": str(exc)}
            row = {"case": case["id"], **reply, **score(case, "" if "incomplete" in reply else reply["raw"])}
            if "incomplete" in reply:
                row["failure_reason"] = "incomplete"
            previous = [r for r in run["rows"] if r["case"] == case["id"]]
            if previous:
                run.setdefault("failed_attempts", []).extend(previous)
                run["rows"] = [r for r in run["rows"] if r["case"] != case["id"]]
            run["rows"].append(row)
            run["summary"] = summarize(run["rows"])
            save_report(args.output, report)
            print(run["label"], case["id"], row["seconds"], row["parsed"], flush=True)
            if "error" in reply:
                break  # a failed server/model is not retried for the entire corpus
        print(run["label"], json.dumps(run["summary"]), flush=True)


if __name__ == "__main__":
    main()
