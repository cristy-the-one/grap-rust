#!/usr/bin/env python3
"""Knowledge Extraction Engine — end-to-end demo (offline by default).

Runs the full assisted-extraction loop on a small bundled corpus:

    docs -> chunk -> LLM extract fragments -> embedding reconcile (dedup)
         -> candidate ontology -> validate -> load -> detect gaps

By default it uses a deterministic StubProvider (canned per-chunk responses) so
the demo runs offline with no model. To use a real local model, start Ollama
(or any OpenAI-compatible server) and run with:

    GRAP_LLM_BASE_URL=http://localhost:11434/v1 GRAP_LLM_MODEL=llama3.1 \
        python mcp-server/demo_kee.py --real

The point: the LLM only ever *proposes*; grap dedups/links/versions and a human
loads the result. Governance stays deterministic even when the extractor isn't.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grap_mcp.kee import pipeline, validate          # noqa: E402
from grap_mcp.kee.embeddings import get_embedder, HashingEmbedder  # noqa: E402
from grap_mcp.kee.llm import StubProvider, OpenAICompatProvider    # noqa: E402

# A tiny, messy "domain" corpus (intentionally unstructured prose).
CORPUS = {
    "verification.md":
        "Our verification approach defines a verification method for every "
        "requirement and records acceptance criteria. A test plan and a test "
        "specification are produced as evidence.",
    "verification_again.md":
        "The verification process must specify how each requirement is verified "
        "and capture the acceptance criteria. Evidence is the test plan document.",
    "quality.md":
        "Quality assurance establishes a quality management approach with a "
        "documented quality plan and assigned responsibilities.",
}

# Canned extractor responses keyed by content (used by the offline StubProvider).
def _stub_handler(system: str, user: str) -> str:
    u = user.lower()
    if "verification" in u:
        return json.dumps({"outcomes": [{
            "name": "Requirement Verification",
            "characteristics": ["verification method", "acceptance criteria",
                                "test plan", "test specification"]}], "relations": []})
    if "quality" in u:
        return json.dumps({"outcomes": [{
            "name": "Quality Assurance",
            "characteristics": ["quality plan", "responsibility assignment"]}],
            "relations": []})
    return "{}"


def banner(title: str) -> None:
    print("\n" + "=" * 68 + f"\n{title}\n" + "=" * 68)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true",
                    help="use a real OpenAI-compatible LLM (GRAP_LLM_BASE_URL)")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="kee-demo-"))
    docs = workdir / "docs"
    docs.mkdir()
    for name, text in CORPUS.items():
        (docs / name).write_text(text, encoding="utf-8")

    if args.real:
        provider = OpenAICompatProvider()
        embedder = get_embedder()
        print(f"Using real LLM at {provider.base_url} (model {provider.model}), "
              f"embedder={embedder.name}")
    else:
        provider = StubProvider(handler=_stub_handler)
        embedder = HashingEmbedder(dim=256)
        print("Using offline StubProvider + HashingEmbedder "
              "(pass --real + GRAP_LLM_BASE_URL for a real model)")

    banner("1. Extract + reconcile  (3 docs, 2 describe the same concept)")
    result = pipeline.extract_ontology(str(docs), provider, embedder, sim=0.82)
    onto = result["ontology"]
    print(f"chunks={result['chunks']}  fragments={result['fragments']}  "
          f"-> {len(onto['process_outcomes'])} canonical outcomes after dedup")
    for o in onto["process_outcomes"]:
        srcs = ", ".join(onto["provenance"].get(o["id"], []))
        print(f"  - {o['name']}  [{', '.join(o['required_characteristics'])}]")
        print(f"      from: {srcs}")

    banner("2. Validate the candidate")
    rep = validate.validate_ontology(onto)
    print("ok:", rep["ok"], "| errors:", rep["errors"], "| warnings:", rep["warnings"])

    banner("3. Write candidate ontology (a *proposal*, gated by human review)")
    out = workdir / "candidate-ontology.yaml"
    out.write_text(pipeline.to_yaml(onto), encoding="utf-8")
    print("wrote", out)

    banner("4. A human adopts it: load + run gap detection against evidence")
    os.environ["GRAP_WORKSPACE"] = str(workdir / "ws")
    os.environ["GRAP_ROLE"] = "operator"
    import grap_mcp.server as s
    kgp = str(workdir / "kg")
    print("create_knowledge_graph:", s.create_knowledge_graph(kgp)["status"])
    # ingest only ONE of the source docs as 'project evidence' so some
    # characteristics are covered and others remain gaps
    ev = workdir / "evidence"
    ev.mkdir()
    (ev / "verification.md").write_text(CORPUS["verification.md"], encoding="utf-8")
    ing = s.ingest_documents(str(ev))
    print(f"ingest evidence: {ing['nodes_added']} nodes")
    print("load proposed ontology:", s.load_ontology(str(out))["status"])
    gaps = s.detect_gaps()
    print(f"\ngaps_found={gaps['gaps_found']} "
          f"(quality-management characteristics are unmet by the evidence):")
    for g in gaps["gaps"]:
        print(f"  GAP {g['outcome_id']}:{g['missing_characteristic']} "
              f"(confidence {g['confidence']})")

    print("\nDone. The LLM proposed; grap dedup/versioned; a human loaded it; "
          "the deterministic reasoner found the gaps.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
