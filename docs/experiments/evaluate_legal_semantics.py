"""Score frozen source references separately from mechanical grounding."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

from grap_mcp.kee import legal_rules
from exercise_legal_rules_live import RecordingProvider


def score(case, claims):
    one = claims[0] if len(claims) == 1 else {}
    kind = one.get("kind") == case["expected_kind"]
    subject = one.get("subject") in case["accepted_subjects"]
    qualifications = bool(one) and all(q in one.get("statement", "") for q in case["required_text"])
    grounded = bool(claims) and all(c.get("evidence") for c in claims) and all(
        0 <= s["start"] < s["end"] <= len(case["text"]) and case["text"][s["start"]:s["end"]] == s["quote"]
        for c in claims for s in c.get("evidence", []) + c.get("context_evidence", [])
        + ([c["subject_evidence"]] if "subject_evidence" in c else []))
    return {"kind_correct": kind, "subject_correct": subject, "qualifications_retained": qualifications,
            "joint_correct": kind and subject and qualifications, "grounded": grounded,
            "missing": not claims, "extra_claims": max(len(claims) - 1, 0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True, choices=["dev", "holdout"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="grap-legal-gemma4")
    parser.add_argument("--extractor-file")
    parser.add_argument("--fixture", help="Optional frozen reference JSON; default is the original 17 cases")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    fixture = Path(args.fixture) if args.fixture else root / "docs/experiments/real-law-semantic-cases.json"
    cases = [c for c in json.loads(fixture.read_text(encoding="utf-8"))["cases"] if c["split"] == args.split]
    if not cases:
        raise SystemExit("No cases for this split in the selected fixture.")
    out = Path(args.output)
    if out.exists():
        raise SystemExit("Choose a fresh output path.")
    extractor = legal_rules
    if args.extractor_file:
        spec = importlib.util.spec_from_file_location("frozen_extractor", args.extractor_file)
        extractor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(extractor)
    for case in cases:
        raw = (root / case["source_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["source_sha256"]
        assert json.loads(raw.decode("utf-8-sig"))["text_complet"][case["start"]:case["end"]] == case["text"]
    p = RecordingProvider(model=args.model, extra_body={"reasoning_effort": "none"})
    p.retries, p.max_tokens = 0, 2048
    result = {"model": args.model, "split": args.split, "prompt_version": extractor.VERSION,
              "extractor_sha256": hashlib.sha256(Path(extractor.__file__).read_bytes()).hexdigest(),
              "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(), "cases": []}
    for case in cases:
        started = time.perf_counter()
        before = len(p.calls)
        row = {"id": case["id"]}
        try:
            row["receipt"] = extractor.extract_rules(p, case["text"])
        except Exception as exc:
            row["error"] = str(exc)
        row["score"] = score(case, row.get("receipt", {}).get("claims", []))
        row["seconds"] = round(time.perf_counter() - started, 3)
        row["calls"] = p.calls[before:]
        result["cases"].append(row)
        result["totals"] = {k: sum(r["score"][k] for r in result["cases"]) for k in row["score"]}
        result["totals"]["cases"] = len(result["cases"])
        out.parent.mkdir(parents=True, exist_ok=True)
        temporary = out.with_suffix(out.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(out)
        print(json.dumps({"id": case["id"], "score": row["score"], "seconds": row["seconds"]}), flush=True)


if __name__ == "__main__":
    main()
