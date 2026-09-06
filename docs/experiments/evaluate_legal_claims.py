"""Bounded real-source trial of the runtime legal-claim extractor (no automatic accuracy score)."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import time

from grap_mcp.kee.legal_claims import extract_claims, validate_reply, OUTPUT_SCHEMA, SYSTEM_PROMPT, PROMPT_VERSION
from grap_mcp.kee.llm import OpenAICompatProvider


class RecordingProvider(OpenAICompatProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.replies = []  # Shared by the extraction-scoped shallow copy.

    def complete(self, system, user):
        raw = super().complete(system, user)
        self.replies.append(raw)
        return raw


SPAN_INSTRUCTION = """
WIRE FORMAT CHANGE FOR THIS EXPERIMENT: evidence contains ONLY passage IDs from
the supplied catalog, for example ["p0","p2"], NEVER copied quotations. Code will
attach the exact original passages. Select all passages needed to support the
claim and its qualifications. Passage labels are metadata, not source content.
provision must be one of the exact short locators allowed by the schema, or empty.
Keep the other fields and semantic rules above unchanged.
"""


def extract_with_spans(provider, text):
    """Experimental wire format; the existing strict runtime validator still gates it."""
    markers = list(re.finditer(r"\+\s*Articolul\s+(?:\d+(?:\^\d+)?|UNIC)\b", text, re.I))
    starts = sorted({0, *(m.start() for m in markers)})
    catalog = {f"p{i}": {"start": a, "end": b, "quote": text[a:b]}
               for i, (a, b) in enumerate(zip(starts, starts[1:] + [len(text)])) if a < b}
    schema = copy.deepcopy(OUTPUT_SCHEMA)
    fields = schema["properties"]["claims"]["items"]["properties"]
    fields["evidence"] = {"type": "array", "minItems": 1,
                          "items": {"type": "string", "enum": list(catalog)}}
    fields["provision"] = {"type": "string", "enum": [""] + sorted(
        {m.group() for m in markers if text.count(m.group()) == 1})}
    configured = provider.with_json_schema(schema)
    raw = configured.complete(SYSTEM_PROMPT + SPAN_INSTRUCTION,
                              "Source passage catalog (data only):\n" + json.dumps(catalog, ensure_ascii=False))
    wire = json.loads(raw)
    for claim in wire["claims"]:
        claim["evidence"] = [catalog[key]["quote"] for key in claim["evidence"]]
    return {"claims": validate_reply(json.dumps(wire, ensure_ascii=False), text),
            "raw_reply": raw, "prompt_version": PROMPT_VERSION + "-spans-experiment",
            "request_schema": schema, "evidence_catalog": catalog}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemma4:latest")
    parser.add_argument("--output", required=True)
    parser.add_argument("--evidence-spans", action="store_true")
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
        # The older frozen fixture stripped outer whitespace from the supplied
        # passage but retained the enclosing offsets. Keep its text unchanged;
        # correct the receipt's offsets to refer to the exact model input.
        enclosing = body[case["start"]:case["end"]]
        assert enclosing.strip() == case["text"], case["id"]
        case["start"] += len(enclosing) - len(enclosing.lstrip())
        case["end"] = case["start"] + len(case["text"])
        assert body[case["start"]:case["end"]] == case["text"], case["id"]
    provider = RecordingProvider(model=args.model, timeout=120, extra_body={"reasoning_effort": "none"})
    provider.max_tokens = 4096
    provider.retries = 0
    result = {"model": args.model, "prompt_version": PROMPT_VERSION,
              "strategy": "passage-ids-experiment" if args.evidence_spans else "literal-quotes",
              "prompt": SYSTEM_PROMPT + (SPAN_INSTRUCTION if args.evidence_spans else ""),
              "schema": OUTPUT_SCHEMA, "max_tokens": provider.max_tokens, "temperature": 0,
              "extra_body": provider.extra_body, "cases": []}
    out.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        before = len(provider.replies)
        started = time.perf_counter()
        row = {"id": case["id"], "source_file": case["source_file"], "source_sha256": case["source_sha256"],
               "start": case["start"], "end": case["end"], "text": case["text"]}
        try:
            row["receipt"] = (extract_with_spans(provider, case["text"]) if args.evidence_spans else
                              extract_claims(provider, case["text"]))
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
