"""Print observed architecture contracts in isolated temporary state.

Run with a current pygrap binding installed:
    python docs/experiments/architecture-contract-probe.py

No LLM or network calls. These are observations of known gaps, not tests that
require those gaps to persist: the printed findings should change as they are
repaired. No production workspace or policy is opened.
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mcp-server"))

with tempfile.TemporaryDirectory(prefix="grap-architecture-") as tmp:
    root = Path(tmp)
    os.environ["GRAP_WORKSPACE"] = str(root / "workspace")
    os.environ["GRAP_ROLE"] = "admin"
    from grap_mcp.application import GrapApplication
    from grap_mcp import reasoner
    s = GrapApplication(root / "workspace", role="admin")
    from grap_mcp import governance
    assert s.create_knowledge_graph(str(root / "evidence"))["status"] == "ok"
    assert s.load_ontology(str(REPO_ROOT / "docs/examples/minimal-aspice-ontology.yaml"))["status"] == "ok"
    pg = root / "probe.pg.yaml"
    pg.write_text("name: architecture-probe\nversion: '1'\nsteps:\n"
                  "  - id: impossible\n    operation: operation_that_does_not_exist\n"
                  "    guards: ['false']\n", encoding="utf-8")
    assert s.load_procedure_graph(str(pg))["status"] == "ok"
    s.set_weight("lexical_threshold", 0.99, "isolated architecture probe")
    original = reasoner.detect_gaps
    weights_seen = []
    def spy(*args, **kwargs):
        weights_seen.append(kwargs.get("weights"))
        return original(*args, **kwargs)
    reasoner.detect_gaps = spy
    procedure = s.run_procedure()
    first = s.detect_gaps()
    s._state.kg.add_node("new evidence changes the corpus after the recorded run")
    second = s.detect_gaps()
    ws = s._get_workspace()
    r1, r2 = ws.get_run(first["run_id"]), ws.get_run(second["run_id"])
    proposal = {"process_outcomes": [{"id": "x", "name": "fixed name",
        "required_characteristics": ["original"]}],
        "concept_hashes": {"x": hashlib.sha256(b"fixed name").hexdigest()}}
    proposal["process_outcomes"][0]["required_characteristics"] = ["completely changed"]
    print(json.dumps({
        "unknown_operation_false_guard": {k: procedure.get(k) for k in ["status", "message", "pg_step_count", "gaps_found"]},
        "weights_seen_procedure_then_detect": weights_seen[:2],
        "corpus_changed_but_same_kg_version": r1.kg_version == r2.kg_version,
        "kg_version_is_ontology_version": r1.kg_version == s._state.ontology_version,
        "reconstruction_after_corpus_change": s.reconstruct_run(first["run_id"]),
        "changed_characteristics_name_hash_check": governance.verify_concept_hashes(proposal),
        "structural_signal_available_on_binding": hasattr(s._state.kg, "has_characteristic"),
    }, indent=2))
