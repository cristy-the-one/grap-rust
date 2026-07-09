#!/usr/bin/env python3
"""Knowledge Extraction Engine — end-to-end demo (offline by default).

See `mcp-server/KEE_DEMO_CLIENT_TUTORIAL.md` for complete client-machine setup instructions.

Runs the full assisted-extraction loop on a small bundled corpus:

    docs -> chunk -> LLM extract fragments -> embedding reconcile (dedup)
         -> candidate ontology -> validate -> load -> detect gaps

By default it uses a deterministic StubProvider (canned per-chunk responses) so
the demo runs offline with no model. To use a real local model, start Ollama
(or any OpenAI-compatible server) and run with:

    GRAP_LLM_BASE_URL=http://localhost:11434/v1 GRAP_LLM_MODEL=llama3.1:8b \
        python mcp-server/demo_kee.py --real

For fully-local embeddings via Ollama too (instead of sentence-transformers):

    GRAP_EMBED_BACKEND=ollama GRAP_EMBED_MODEL=nomic-embed-text \
        GRAP_LLM_MODEL=llama3.1:8b python mcp-server/demo_kee.py --real

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

    # Wire the demo-controlled provider/embedder into the server module's factories.
    # This ensures extract_ontology (and other KEE tools) use our stub (with corpus-specific
    # responses) + hashing embedder instead of defaulting to a network LLM.
    import grap_mcp.kee.llm as llm_mod
    import grap_mcp.kee.embeddings as emb_mod
    llm_mod.get_provider = lambda model=None: provider
    emb_mod.get_embedder = lambda: embedder
    os.environ["GRAP_LLM_STUB"] = "1"

    banner("1. Extract + reconcile  (3 docs, 2 describe the same concept) - using server path for versioning")
    os.environ["GRAP_WORKSPACE"] = str(workdir / "ws")
    os.environ["GRAP_ROLE"] = "operator"
    import grap_mcp.server as s
    s._state.reset()  # ensure clean portable state
    # Use server extract which versions the proposal with hashes etc.
    ext_res = s.extract_ontology(str(docs), out_path=str(workdir / "candidate-ontology.yaml"), sim=0.82)
    print("extract_ontology status:", ext_res.get("status"))
    print("version_hash:", ext_res.get("version_hash"))
    print("concept_hashes sample:", list(ext_res.get("concept_hashes", {}).items())[:2] or "(none)")
    print("process_outcomes:", len(ext_res.get("process_outcomes", [])))

    banner("2. Validate (via proposal)")
    # Validation is in the extract result
    print("validation:", ext_res.get("validation"))

    banner("3. Human review via tools (list, get) - non-banal governance")
    print("list_proposed:", s.list_proposed_ontologies())
    prop = s.get_proposed_ontology(ext_res.get("version_hash"))
    print("get_proposed keys:", list(prop.keys()) if isinstance(prop, dict) else type(prop))

    banner("4. Prepare target KG then Adopt with rationale (full governance loop)")
    kgp = str(workdir / "kg")
    print("create_knowledge_graph:", s.create_knowledge_graph(kgp)["status"])
    adopt = s.adopt_proposed_ontology(ext_res.get("version_hash"), rationale="Demo: reviewed proposal with core hashes and graph-aug extraction; adopting for use.")
    print("adopt status:", adopt.get("status") if isinstance(adopt, dict) else adopt)

    banner("5. Ingest evidence + use adopted ontology for gap detection")
    ev = workdir / "evidence"
    ev.mkdir()
    (ev / "verification.md").write_text(CORPUS["verification.md"], encoding="utf-8")
    ing = s.ingest_documents(str(ev))
    print(f"ingest evidence: {ing['nodes_added']} nodes")
    gaps = s.detect_gaps(wire_format="json")
    print(f"\ngaps_found={gaps.get('gaps_found')} (demo shows unmet characteristics):")
    for g in gaps.get("gaps", [])[:3]:
        print(f"  GAP {g.get('outcome_id')}:{g.get('missing_characteristic')}")

    print("\nDone (portable non-banal KEE demo). "
          "Shows: graph-augmented extract (working graph context during proposal), "
          "version_hash + concept_hashes for provenance/dedup, "
          "full governance: extract (propose) → list/get proposed → adopt with required rationale (audited), "
          "then adopt drives gap detection on fresh evidence ingest. "
          "Stub+temp dirs by default (no LLM, fully offline/portable). "
          "Pass --real + GRAP_LLM_BASE_URL for live model (still portable with local Ollama).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
