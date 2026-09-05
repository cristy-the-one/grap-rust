"""PG reference patches — deterministic checks, draft publication,
one-click promotion, and the pg_draft queue kinds."""
import hashlib

from grap_mcp import pg_patch
from grap_mcp import procedure_graph as pg_loader


def _sha(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


ONTO_RV = f"""\
version: kee-candidate
process_outcomes:
- id: rv
  name: Requirement Verification
  description: RV.
  required_characteristics: [verification_method]
mapping_hints: []
concept_hashes:
  rv: {_sha("Requirement Verification")}
source_chunk_counts:
  rv: 2
"""

ONTO_RV_QA = f"""\
version: kee-candidate
process_outcomes:
- id: rv
  name: Requirement Verification
  description: RV.
  required_characteristics: [verification_method]
- id: qa
  name: Quality Assurance
  description: QA.
  required_characteristics: [quality_plan]
mapping_hints: []
concept_hashes:
  rv: {_sha("Requirement Verification")}
  qa: {_sha("Quality Assurance")}
source_chunk_counts:
  rv: 2
  qa: 2
"""

PG_V1 = """\
name: P
version: 0.1.0
steps:
  - id: s1
    operation: verify
    references: ["kg:aspice.process_outcome.rv"]
"""

DRAFT_OK = """\
name: P
version: 0.1.1
steps:
  - id: s1
    operation: verify
    references: ["kg:aspice.process_outcome.rv"]
  - id: s2
    operation: assure
    references: ["kg:aspice.process_outcome.qa"]
"""


def _server(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", role)
    from grap_mcp.application import GrapApplication
    s = GrapApplication()
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    return s


def _adopt(s, yaml_text):
    vh = s._get_workspace().publish_ontology(
        yaml_text, metadata={"kind": "proposed"}).hash
    out = s.adopt_proposed_ontology(vh, rationale="test adopt")
    assert out["status"] == "ok"
    return out["new_version_hash"]


def _load_pg(s, tmp_path):
    p = tmp_path / "p.pg.yaml"
    p.write_text(PG_V1, encoding="utf-8")
    assert s.load_procedure_graph(str(p))["status"] == "ok"


# ---- pure helpers -------------------------------------------------------------

def test_reference_patch_preserves_steps_and_handles_collisions():
    pg = pg_loader.load_from_string(PG_V1)
    pg.steps[0].id = "cover_qa"
    pg.steps[0].guards = ["keep this guard"]
    pg.steps[0].references.append("kg:aspice.process_outcome.old")
    raw = pg_patch.patch_references(pg, [{"id": "qa"}], ["old"])
    draft = pg_loader.load_from_string(raw)
    assert draft.steps[0].operation == "verify"
    assert draft.steps[0].guards == ["keep this guard"]
    assert draft.steps[1].id == "cover_qa_2"
    assert pg_patch.referenced_outcome_ids(draft) == {"rv", "qa"}
    assert raw == pg_patch.patch_references(pg, [{"id": "qa"}], ["old"])
    assert "old" in pg.steps[0].references[-1]  # original untouched


def test_coverage_violations():
    prev = {"rv", "old"}
    draft = pg_loader.load_from_string(DRAFT_OK)
    # must cover qa (it does), drop old (it does), keep rv (it does)
    assert pg_patch.coverage_violations(draft, prev, {"qa"}, {"old"}) == []
    # demand something the draft lacks
    v = pg_patch.coverage_violations(draft, prev, {"qa", "zz"}, {"old"})
    assert any("zz" in x for x in v)
    # regression: draft silently dropping a still-valid reference
    v2 = pg_patch.coverage_violations(draft, {"rv", "keep_me"}, {"qa"}, set())
    assert any("keep_me" in x for x in v2)


# ---- draft -> review -> promote loop --------------------------------------------

def test_draft_publish_and_promote_clears_queue(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    _adopt(s, ONTO_RV_QA)
    _load_pg(s, tmp_path)

    out = s.draft_pg_patch()
    assert out["status"] == "ok"
    assert out["uncovered"] == ["qa"] and out["orphaned"] == []
    assert out["validation"]["ok"] is True
    assert "cover_qa" in str(out["diff"])                            # diff shows the new step
    h = out["draft_version_hash"]
    assert h

    q = s.list_pending_interventions()
    assert any(i["kind"] == "pg_draft_pending" for i in q["pending"])

    # the human's one click
    assert s.promote_pg(h, rationale="reviewed reference patch; covers qa")["status"] == "ok"
    assert not any(i["kind"] == "pg_draft_pending"
                   for i in s.list_pending_interventions()["pending"])


def test_bad_draft_rejected_and_alarm_clears_on_success(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    _adopt(s, ONTO_RV_QA)
    _load_pg(s, tmp_path)

    original = pg_patch.patch_references
    monkeypatch.setattr(pg_patch, "patch_references", lambda *args: "definitely: [not a pg")
    out = s.draft_pg_patch()
    assert out["status"] == "error"
    assert any(i["kind"] == "pg_draft_failed"
               for i in s.list_pending_interventions()["pending"])

    # a draft that ignores the coverage instruction is also rejected
    monkeypatch.setattr(pg_patch, "patch_references", lambda *args: PG_V1)
    out2 = s.draft_pg_patch()
    assert out2["status"] == "error" and "does not cover" in out2["message"]

    monkeypatch.setattr(pg_patch, "patch_references", original)
    assert s.draft_pg_patch()["status"] == "ok"
    pending = s.list_pending_interventions()["pending"]
    assert not any(i["kind"] == "pg_draft_failed" for i in pending)
    assert any(i["kind"] == "pg_draft_pending" for i in pending)


def test_draft_noop_when_pg_already_covers(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    _adopt(s, ONTO_RV)
    _load_pg(s, tmp_path)
    out = s.draft_pg_patch()
    assert out["status"] == "ok"
    assert out["draft_version_hash"] is None
    assert "already covers" in out["note"]


# ---- auto-trigger after adoption -------------------------------------------------

def test_pg_draft_auto_triggers_after_kept_adoption(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    _adopt(s, ONTO_RV)
    _load_pg(s, tmp_path)
    assert s.set_policy("enabled", True, rationale="on")["status"] == "ok"
    assert s.set_policy("pg_draft_enabled", True, rationale="draft PGs")["status"] == "ok"

    _, auto = s._publish_and_gate_proposal(ONTO_RV_QA, "test", "p", "e", 2)
    assert auto["adopted"] is True
    assert auto["pg_draft"]["status"] == "ok"
    assert auto["pg_draft"]["uncovered"] == ["qa"]
    assert auto["pg_draft"]["draft_version_hash"]


def test_set_policy_pg_draft_bool_cast(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    out = s.set_policy("pg_draft_enabled", 1, rationale="cast")
    assert out["adoption_policy"]["pg_draft_enabled"] is True
