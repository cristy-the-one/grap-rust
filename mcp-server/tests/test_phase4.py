"""Phase 4: LLM-drafted PG patches — deterministic checks, draft publication,
one-click promotion, and the pg_draft queue kinds."""
import hashlib
import importlib

from grap_mcp import pg_patch
from grap_mcp import procedure_graph as pg_loader
from grap_mcp.kee.llm import StubProvider


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
    import grap_mcp.server as s
    importlib.reload(s)
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


def _stub_llm(s, monkeypatch, response):
    monkeypatch.setattr(s, "get_provider",
                        lambda model=None: StubProvider(default=response))


# ---- pure helpers -------------------------------------------------------------

def test_strip_fences():
    assert pg_patch.strip_fences("```yaml\nname: P\n```") == "name: P"
    assert pg_patch.strip_fences("name: P") == "name: P"
    assert pg_patch.strip_fences("") == ""


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
    _stub_llm(s, monkeypatch, "```yaml\n" + DRAFT_OK + "```")   # fenced on purpose

    out = s.draft_pg_patch()
    assert out["status"] == "ok"
    assert out["uncovered"] == ["qa"] and out["orphaned"] == []
    assert out["validation"]["ok"] is True
    assert "s2" in str(out["diff"])                            # diff shows the new step
    h = out["draft_version_hash"]
    assert h

    q = s.list_pending_interventions()
    assert any(i["kind"] == "pg_draft_pending" for i in q["pending"])

    # the human's one click
    assert s.promote_pg(h, rationale="reviewed LLM draft; covers qa")["status"] == "ok"
    assert not any(i["kind"] == "pg_draft_pending"
                   for i in s.list_pending_interventions()["pending"])


def test_bad_draft_rejected_and_alarm_clears_on_success(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    _adopt(s, ONTO_RV_QA)
    _load_pg(s, tmp_path)

    _stub_llm(s, monkeypatch, "definitely: [not a pg")
    out = s.draft_pg_patch()
    assert out["status"] == "error"
    assert any(i["kind"] == "pg_draft_failed"
               for i in s.list_pending_interventions()["pending"])

    # a draft that ignores the coverage instruction is also rejected
    _stub_llm(s, monkeypatch, PG_V1)                            # still misses qa
    out2 = s.draft_pg_patch()
    assert out2["status"] == "error" and "does not cover" in out2["message"]

    _stub_llm(s, monkeypatch, DRAFT_OK)
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
    _stub_llm(s, monkeypatch, DRAFT_OK)

    _, auto = s._publish_and_gate_proposal(ONTO_RV_QA, "test", "p", "e", 2)
    assert auto["adopted"] is True
    assert auto["pg_draft"]["status"] == "ok"
    assert auto["pg_draft"]["uncovered"] == ["qa"]
    assert auto["pg_draft"]["draft_version_hash"]


def test_set_policy_pg_draft_bool_cast(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    out = s.set_policy("pg_draft_enabled", 1, rationale="cast")
    assert out["adoption_policy"]["pg_draft_enabled"] is True
