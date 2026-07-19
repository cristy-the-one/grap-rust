"""The KEE human-review loop: propose -> list/get -> verify -> adopt.

Integration-style: real workspace + real KG in a tmp dir, exercising the
concept_hashes tamper-evidence gate added in adopt_proposed_ontology.
"""
import hashlib
import importlib

PROPOSAL_TMPL = """\
version: kee-candidate
process_outcomes:
- id: requirement_verification
  name: {name}
  description: Requirement Verification (proposed by extraction).
  required_characteristics:
  - verification_method
mapping_hints:
- outcome: requirement_verification
  indicators:
  - keywords: [verification, method]
concept_hashes:
  requirement_verification: {chash}
"""


def _server(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", "operator")
    import grap_mcp.server as s
    importlib.reload(s)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    return s


def _publish(s, yaml_text):
    return s._get_workspace().publish_ontology(
        yaml_text, metadata={"kind": "proposed", "source": "test"}).hash


def test_adopt_verifies_and_accepts_untampered_proposal(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    name = "Requirement Verification"
    good = PROPOSAL_TMPL.format(
        name=name, chash=hashlib.sha256(name.encode("utf-8")).hexdigest())
    vh = _publish(s, good)

    out = s.adopt_proposed_ontology(vh, rationale="reviewed; hashes verified")
    assert out["status"] == "ok"
    assert out["concept_hashes_checked"] == 1
    assert out["projection_verified"] is True
    assert out["outcomes_projected"] == 1


def test_adopt_rejects_tampered_proposal(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    # Hash recorded for the original name, but the yaml's name was edited
    # after extraction — adoption must refuse.
    original = "Requirement Verification"
    tampered = PROPOSAL_TMPL.format(
        name="Weakened Verification",
        chash=hashlib.sha256(original.encode("utf-8")).hexdigest())
    vh = _publish(s, tampered)

    out = s.adopt_proposed_ontology(vh, rationale="attempting adoption")
    assert out["status"] == "error"
    assert "verification failed" in out["message"]
    assert out["mismatches"] and out["mismatches"][0]["outcome"] == "requirement_verification"
    # the rejection is audited
    events = s._get_workspace().query_events("ontology_adoption_rejected")
    assert any(e.subject == vh for e in events)


def test_adopt_rejects_hash_for_unknown_outcome(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    name = "Requirement Verification"
    good = PROPOSAL_TMPL.format(
        name=name, chash=hashlib.sha256(name.encode("utf-8")).hexdigest())
    # extra hash entry pointing at a non-existent outcome id
    bad = good + "  ghost_outcome: " + "0" * 64 + "\n"
    vh = _publish(s, bad)

    out = s.adopt_proposed_ontology(vh, rationale="attempting adoption")
    assert out["status"] == "error"
    assert any(m.get("outcome") == "ghost_outcome" for m in out["mismatches"])


def test_adopt_without_hashes_still_works(tmp_path, monkeypatch):
    # Legacy stateless extraction has no concept_hashes: nothing to verify.
    s = _server(tmp_path, monkeypatch)
    yaml_text = (
        "version: kee-candidate\n"
        "process_outcomes:\n"
        "- id: quality_assurance\n"
        "  name: Quality Assurance\n"
        "  description: QA (proposed).\n"
        "  required_characteristics: [quality_plan]\n"
        "mapping_hints: []\n")
    vh = _publish(s, yaml_text)
    out = s.adopt_proposed_ontology(vh, rationale="no hashes in legacy proposal")
    assert out["status"] == "ok"
    assert out["concept_hashes_checked"] == 0
