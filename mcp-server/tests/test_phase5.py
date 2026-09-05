"""Phase 5: trivial PG drafts self-promote; the pipeline carries the PG; and
the lights-out soak — every policy on, two full pipeline runs, zero humans."""
import hashlib
import importlib
import json

from grap_mcp.governance import pg_draft_is_trivial
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.llm import StubProvider

# ontology ids come from KEE's tokenizer: "Requirement Verification" ->
# requirement_verification, "Quality Assurance" -> quality_assurance
PG_V1 = """\
name: P
version: 0.1.0
steps:
  - id: s1
    operation: verify
    references: ["kg:aspice.process_outcome.requirement_verification"]
"""

DRAFT_ADDITIVE = """\
name: P
version: 0.1.1
steps:
  - id: s1
    operation: verify
    references: ["kg:aspice.process_outcome.requirement_verification"]
  - id: s2
    operation: assure
    references: ["kg:aspice.process_outcome.quality_assurance"]
"""

DRAFT_INTRUSIVE = """\
name: P
version: 0.1.1
steps:
  - id: s1
    operation: verify_differently
    references: ["kg:aspice.process_outcome.requirement_verification"]
  - id: s2
    operation: assure
    references: ["kg:aspice.process_outcome.quality_assurance"]
"""


def _sha(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


ONTO_BOTH = f"""\
version: kee-candidate
process_outcomes:
- id: requirement_verification
  name: Requirement Verification
  description: RV.
  required_characteristics: [verification_method]
- id: quality_assurance
  name: Quality Assurance
  description: QA.
  required_characteristics: [quality_plan]
mapping_hints: []
concept_hashes:
  requirement_verification: {_sha("Requirement Verification")}
  quality_assurance: {_sha("Quality Assurance")}
source_chunk_counts:
  requirement_verification: 2
  quality_assurance: 2
"""


def _server(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", role)
    from grap_mcp.application import GrapApplication
    s = GrapApplication()
    return s


# ---- trivial-draft rule (pure) -------------------------------------------------

def test_pg_draft_is_trivial_rule():
    ok = {"steps_added": ["s2"], "steps_removed": [], "steps_changed": [],
          "metadata_changed": {"version": ["0.1.0", "0.1.1"]}}
    assert pg_draft_is_trivial(ok) is True
    assert pg_draft_is_trivial({**ok, "steps_removed": ["s1"]}) is False
    assert pg_draft_is_trivial({**ok, "steps_changed": [{"step_id": "s1"}]}) is False
    assert pg_draft_is_trivial({**ok, "metadata_changed": {"name": ["P", "Q"]}}) is False
    assert pg_draft_is_trivial({**ok, "steps_added": []}) is False


# ---- auto-promotion --------------------------------------------------------------

def _setup(tmp_path, monkeypatch, draft_yaml):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    vh = s._get_workspace().publish_ontology(ONTO_BOTH, metadata={}).hash
    assert s.adopt_proposed_ontology(vh, rationale="test")["status"] == "ok"
    p = tmp_path / "p.pg.yaml"
    p.write_text(PG_V1, encoding="utf-8")
    assert s.load_procedure_graph(str(p))["status"] == "ok"
    monkeypatch.setattr(s, "get_provider",
                        lambda model=None: StubProvider(default=draft_yaml))
    assert s.set_policy("pg_autopromote_enabled", True, rationale="close the loop")["status"] == "ok"
    return s


def test_trivial_draft_auto_promotes_and_queue_stays_empty(tmp_path, monkeypatch):
    s = _setup(tmp_path, monkeypatch, DRAFT_ADDITIVE)
    out = s.draft_pg_patch()
    assert out["status"] == "ok"
    assert out["auto_promoted"] is True
    # promoted -> never pending; nothing else pending either
    kinds = [i["kind"] for i in s.list_pending_interventions()["pending"]]
    assert "pg_draft_pending" not in kinds


def test_intrusive_draft_stays_queued_for_a_human(tmp_path, monkeypatch):
    s = _setup(tmp_path, monkeypatch, DRAFT_INTRUSIVE)
    # Removing an obsolete reference changes an existing step and needs review.
    s._state.pg.steps[0].references.append("kg:aspice.process_outcome.obsolete")
    out = s.draft_pg_patch()
    assert out["status"] == "ok"
    assert out["auto_promoted"] is False           # changes existing step s1
    assert any(i["kind"] == "pg_draft_pending"
               for i in s.list_pending_interventions()["pending"])


# ---- lights-out soak ----------------------------------------------------------------

def _factory_stub(system, user):
    if "Procedure Graph" in system:                # PG drafting call
        return DRAFT_ADDITIVE
    u = user.lower()                               # extraction call
    if "verification" in u:
        return json.dumps({"outcomes": [{"name": "Requirement Verification",
                                         "characteristics": ["verification method"]}],
                           "relations": []})
    return json.dumps({"outcomes": [{"name": "Quality Assurance",
                                     "characteristics": ["quality plan"]}],
                       "relations": []})


def test_lights_out_two_runs_zero_humans(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    for key in ("enabled", "canary_enabled", "pg_draft_enabled",
                "pg_autopromote_enabled"):
        assert s.set_policy(key, True, rationale="lights out")["status"] == "ok"
    monkeypatch.setattr(s, "get_provider",
                        lambda model=None: StubProvider(handler=_factory_stub))
    monkeypatch.setattr(s, "get_embedder", lambda: HashingEmbedder(dim=64))

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "verification.md").write_text(
        "The verification method is defined for each requirement.", encoding="utf-8")
    (docs / "quality.md").write_text(
        "Quality assurance maintains a quality plan.", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "req.md").write_text(
        "The verification method is specified. The quality plan is maintained.",
        encoding="utf-8")
    pgf = tmp_path / "p.pg.yaml"
    pgf.write_text(PG_V1, encoding="utf-8")
    cfg = tmp_path / "pipeline.json"
    cfg.write_text(json.dumps({
        "kg_path": str(tmp_path / "kg"),
        "pg_path": str(pgf),
        "evidence_dirs": [str(evidence)],
        "extract": {"docs_path": str(docs)},
        "detect_gaps": True,
        "maintenance": True}), encoding="utf-8")

    r1 = s.run_pipeline(str(cfg))
    assert r1["status"] == "ok"
    assert r1["steps"]["pg"]["status"] == "ok"
    auto = r1["steps"]["extract"]["auto_adoption"]
    assert auto["adopted"] is True
    assert auto["canary"] == "skipped"             # bootstrap: no baseline yet
    assert auto["pg_draft"]["status"] == "ok"      # PG patch drafted...
    assert r1["steps"]["maintenance"]["ok"] is True

    r2 = s.run_pipeline(str(cfg))                  # unchanged corpus
    assert r2["status"] == "ok"
    assert r2["steps"]["extract"]["auto_adoption"] == {
        "attempted": False, "reason": "already_active"}
    assert r2["steps"]["gaps"]["delta"]["new"] == 0

    st = s.get_factory_status()
    assert st["healthy"] is True                   # the factory needs nobody
    assert s.list_pending_interventions()["count"] == 0
