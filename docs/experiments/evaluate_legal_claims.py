"""Bounded real-source trial of the runtime legal-claim extractor (no automatic accuracy score)."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from grap_mcp.kee.legal_claims import extract_claims, OUTPUT_SCHEMA, SYSTEM_PROMPT, PROMPT_VERSION
from grap_mcp.kee.llm import OpenAICompatProvider


class RecordingProvider(OpenAICompatProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.replies = []  # Shared by the extraction-scoped shallow copy.

    def complete(self, system, user):
        raw = super().complete(system, user)
        self.replies.append(raw)
        return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemma4:latest")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise SystemExit("Choose a new output path; preserved results are not overwritten.")
    root = Path(__file__).resolve().parents[2]
    cases = json.loads((root / "docs/experiments/real-law-extraction-cases.json").read_text(encoding="utf-8"))
    # Check frozen passages against original bytes BEFORE making any model calls.
    for case in cases:
        raw = (root / case["source_file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == case["source_sha256"], case["id"]
        body = json.loads(raw.decode("utf-8-sig"))[case["field"]]
        assert body[case["start"]:case["end"]] == case["text"], case["id"]
    provider = RecordingProvider(model=args.model, timeout=120, extra_body={"reasoning_effort": "none"})
    provider.max_tokens = 4096
    provider.retries = 0
    result = {"model": args.model, "prompt_version": PROMPT_VERSION, "prompt": SYSTEM_PROMPT,
              "schema": OUTPUT_SCHEMA, "max_tokens": provider.max_tokens, "temperature": 0,
              "extra_body": provider.extra_body, "cases": []}
    out.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        before = len(provider.replies)
        started = time.perf_counter()
        row = {"id": case["id"], "source_file": case["source_file"], "source_sha256": case["source_sha256"],
               "start": case["start"], "end": case["end"], "text": case["text"]}
        try:
            row["receipt"] = extract_claims(provider, case["text"])
        except Exception as exc:
            row["error"] = str(exc)
        if len(provider.replies) > before:
            row["raw_reply"] = provider.replies[-1]
        row["seconds"] = round(time.perf_counter() - started, 3)
        result["cases"].append(row)
        temporary = out.with_suffix(out.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(out)
        print(json.dumps({"id": row["id"], "claims": len(row.get("receipt", {}).get("claims", [])),
                          "seconds": row["seconds"], "error": row.get("error")}), flush=True)


if __name__ == "__main__":
    main()
