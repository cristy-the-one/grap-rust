"""Exercise the production bounded recovery on saved primary replies."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from evaluate_chunking_accuracy import ROOT, save_report
from grap_mcp.kee import extract, qualified
from grap_mcp.kee.llm import OpenAICompatProvider

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--fixture", type=Path, required=True)
parser.add_argument("--results", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--resume", action="store_true")
args = parser.parse_args()
if args.output.exists() and not args.resume:
    parser.error("Choose a fresh repair receipt")
cases = {c["id"]: c for c in json.loads(args.fixture.read_bytes())["cases"]}
results = json.loads(args.results.read_bytes())
assert results["fixture_sha256"] == hashlib.sha256(args.fixture.read_bytes()).hexdigest()
p = OpenAICompatProvider(model="grap-legal-gemma4", timeout=180, temperature=0,
                         extra_body={"reasoning_effort": "none", "seed": 20260908})
p.retries, p.max_tokens = 0, 2048
report = {"results_sha256": hashlib.sha256(args.results.read_bytes()).hexdigest(), "rows": []}
if args.output.exists():
    saved = json.loads(args.output.read_bytes())
    assert saved["results_sha256"] == report["results_sha256"]
    report = saved
done = {(r["case"], r["arm"], r["chunk"]) for r in report["rows"]}
for row in results["rows"]:
    if (row["case"], row["arm"], row["chunk"]) in done:
        continue
    case = cases[row["case"]]
    file = ROOT / case["source_file"]
    assert hashlib.sha256(file.read_bytes()).hexdigest() == case["source_sha256"]
    text = json.loads(file.read_text(encoding="utf-8-sig"))["text_complet"]
    chunk = next(c for c in case["arms"][row["arm"]] if c["id"] == row["chunk"])
    context = qualified.slice_plan(qualified.plan(text, str(file.resolve())), chunk["start"], chunk["end"])
    started = time.perf_counter()
    frag, failure = extract.finalize_reply(row["raw"], row["chunk"], chunk["text"], p, context)
    item = {k: row[k] for k in ("case", "arm", "chunk")}
    item.update(repair=frag.get("classification_repair"), unresolved=frag["rule_coverage"]["unresolved"],
                seconds=round(time.perf_counter()-started, 3), failure=failure)
    report["rows"].append(item)
    save_report(args.output, report)
    print(json.dumps({k: v for k, v in item.items() if k != "repair"}), flush=True)
