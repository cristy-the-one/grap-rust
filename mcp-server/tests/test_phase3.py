"""Phase 3: canary auto-promotion (rollback on gap regressions) and
n-version extraction (agreement set auto-adopts, disputes queue)."""
import hashlib
import json

from grap_mcp.governance import agreement_merge
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.llm import StubProvider

V_TMPL = """\
version: kee-candidate
process_outcomes:
- id: req_ver
  name: Requirement Verification
  description: Requirement Verification (proposed).
  required_characteristics:
{chars}mapping_hints:
- outcome: req_ver
  indicators:
  - keywords: [verification, method]
concept_hashes:
  req_ver: {chash}
source_chunk_counts:
  req_ver: 2
"""


def _v(chars):
    lines = "".join(f"  - {c}\n" for c in chars)
    return V_TMPL.format(chars=lines, chash=hashlib.sha256(
        b"Requirement Verification").hexdigest())


def _server(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", role)
    from grap_mcp.application import GrapApplication
    s = GrapApplication()
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    return s


def _baseline(s, tmp_path):
    """Adopt v1, ingest covering evidence, run a production baseline."""
    v1 = s._get_workspace().publish_ontology(
        _v(["verification_method"]), metadata={"kind": "proposed"}).hash
    a1 = s.adopt_proposed_ontology(v1, rationale="baseline")
    assert a1["status"] == "ok"
    docs = tmp_path / "evidence"
    docs.mkdir()
    (docs / "req.md").write_text(
        "The verification method is specified for each requirement. "
        "Acceptance criteria are defined.", encoding="utf-8")
    assert s.ingest_documents(str(docs))["status"] == "ok"
    g = s.detect_gaps()
    assert g["gaps_found"] == 0
    return a1["new_version_hash"]


# ---- canary -----------------------------------------------------------------

def test_canary_rolls_back_gap_regression_then_clears(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    h1 = _baseline(s, tmp_path)
    assert s.set_policy("enabled", True, rationale="factory on")["status"] == "ok"
    assert s.set_policy("canary_enabled", True, rationale="canary on")["status"] == "ok"

    # candidate adds a characteristic with no evidence -> canary must revert
    _, auto = s._publish_and_gate_proposal(
        _v(["verification_method", "quantum_safety_audit"]), "test", "p", "e", 1)
    assert auto["pass"] and auto["adopted"]
    assert auto["canary"] == "failed"
    assert auto["new_gaps"] == 1
    assert auto["rolled_back_to"] == h1
    assert auto["rollback_status"] == "ok"
    assert s._state.ontology_version == h1              # really reverted
    q = s.list_pending_interventions()
    assert any(i["kind"] == "canary_failed" for i in q["pending"])
    assert s.get_factory_status()["healthy"] is False

    # candidate whose new characteristic IS covered -> passes, clears the alarm
    _, auto2 = s._publish_and_gate_proposal(
        _v(["verification_method", "acceptance_criteria"]), "test", "p", "e", 1)
    assert auto2["canary"] == "passed" and auto2["new_gaps"] == 0
    assert s._state.ontology_version != h1              # promotion kept
    assert not any(i["kind"] == "canary_failed"
                   for i in s.list_pending_interventions()["pending"])


def test_canary_skipped_on_bootstrap(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.set_policy("enabled", True, rationale="on")["status"] == "ok"
    assert s.set_policy("canary_enabled", True, rationale="on")["status"] == "ok"
    _, auto = s._publish_and_gate_proposal(_v(["verification_method"]),
                                           "test", "p", "e", 1)
    assert auto["adopted"] and auto["canary"] == "skipped"
    assert s._state.ontology is not None                # adoption kept


def test_set_policy_canary_bool_cast(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    out = s.set_policy("canary_enabled", 1, rationale="bool cast")
    assert out["adoption_policy"]["canary_enabled"] is True


# ---- n-version merge (pure) ---------------------------------------------------

def _prop(outcomes):
    return {"version": "kee-candidate",
            "process_outcomes": [
                {"id": oid, "name": name, "description": name,
                 "required_characteristics": chars}
                for oid, name, chars in outcomes],
            "mapping_hints": [],
            "provenance": {oid: ["c1", "c2"] for oid, _, _ in outcomes}}


def test_agreement_merge_intersects_and_reports():
    a = _prop([("rv", "Requirement Verification",
                ["verification_method", "acceptance_criteria"]),
               ("qb", "Quantum Bogus", ["x"])])
    b = _prop([("rv", "requirement  verification",     # normalized match
                ["verification_method", "test_plan"])])
    merged, dis = agreement_merge(a, b)
    assert [o["id"] for o in merged["process_outcomes"]] == ["rv"]
    assert merged["process_outcomes"][0]["required_characteristics"] == ["verification_method"]
    assert merged["source_chunk_counts"]["rv"] == 2
    assert merged["concept_hashes"]["rv"] == hashlib.sha256(
        b"Requirement Verification").hexdigest()
    assert dis["outcomes_only_in_a"] == ["qb"]
    assert dis["outcomes_only_in_b"] == []
    assert dis["disputed_characteristics"] == [
        {"outcome": "rv", "characteristics": ["acceptance_criteria", "test_plan"]}]


def test_agreement_merge_full_agreement_is_clean():
    a = _prop([("rv", "Requirement Verification", ["verification_method"])])
    merged, dis = agreement_merge(a, a)
    assert dis["agreed_outcomes"] == 1
    assert not dis["outcomes_only_in_a"] and not dis["disputed_characteristics"]
    assert merged["process_outcomes"][0]["required_characteristics"] == ["verification_method"]


# ---- n-version integration ----------------------------------------------------

def _handler(outcomes):
    def h(system, user):
        return json.dumps({"outcomes": outcomes, "relations": []})
    return h


def test_nversion_adopts_agreement_and_queues_disagreement(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.set_policy("enabled", True, rationale="on")["status"] == "ok"
    monkeypatch.setattr(s, "get_embedder", lambda: HashingEmbedder(dim=64))
    stubs = {
        "m1": StubProvider(handler=_handler([
            {"name": "Requirement Verification",
             "characteristics": ["verification method", "acceptance criteria"]},
            {"name": "Quantum Bogus", "characteristics": ["nonsense"]}])),
        "m2": StubProvider(handler=_handler([
            {"name": "Requirement Verification",
             "characteristics": ["verification method", "test plan"]}])),
    }
    monkeypatch.setattr(s, "_provider_from_spec", lambda spec: stubs[spec])

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "v.md").write_text("verification text", encoding="utf-8")

    res = s.extract_ontology_nversion(str(docs), models=["m1", "m2"])
    assert res["status"] == "ok"
    assert res["agreed_outcomes"] == 1
    assert res["auto_adoption"]["adopted"] is True       # agreement set adopted
    assert res["disputed_version_hash"]                  # residue published
    sources = res["source_proposals"]
    assert [p["model"] for p in sources] == ["m1", "m2"]
    saved = [s.get_proposed_ontology(p["version_hash"])["parsed"] for p in sources]
    assert all("v#0" in p["extraction_evidence"] for p in saved)
    assert len(saved[0]["process_outcomes"]) == 2
    assert len(saved[1]["process_outcomes"]) == 1
    agreement = s.get_proposed_ontology(res["version_hash"])["parsed"]
    assert agreement["source_proposals"] == sources
    q = s.list_pending_interventions()
    assert any(i["kind"] == "model_disagreement" for i in q["pending"])

    # models converge -> disagreement alarm clears (latest-state)
    res2 = s.extract_ontology_nversion(str(docs), models=["m2", "m2"])
    assert res2["status"] == "ok" and res2["disputed_version_hash"] is None
    assert not any(i["kind"] == "model_disagreement"
                   for i in s.list_pending_interventions()["pending"])


def test_nversion_requires_two_models(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.extract_ontology_nversion("x", models=["only-one"])["status"] == "error"


def test_partial_extraction_never_auto_replaces_the_active_ontology(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    baseline = _baseline(s, tmp_path)
    assert s.set_policy("enabled", True, rationale="on")["status"] == "ok"
    docs = tmp_path / "partial"
    docs.mkdir()
    (docs / "a.txt").write_text("Plan shall include coverage.", encoding="utf-8")
    (docs / "b.txt").write_text("Broken response for this source.", encoding="utf-8")
    def handler(system, user):
        if "Broken response" in user:
            return "not JSON"
        return json.dumps({"outcomes": [{"name": "Plan", "requirements": ["coverage"]}], "relations": []})
    monkeypatch.setattr(s, "get_provider", lambda **kwargs: StubProvider(handler=handler))
    monkeypatch.setattr(s, "get_embedder", lambda: HashingEmbedder(64))
    result = s.extract_ontology(str(docs))
    assert result["status"] == "ok"  # the partial proposal remains inspectable
    assert result["failures"] == [{"source": "b#0", "reason": "unparsed"}]
    assert result["auto_adoption"]["failed_checks"] == ["incomplete_extraction"]
    assert not result["auto_adoption"]["adopted"]
    assert s._state.ontology_version == baseline
    assert s.get_proposed_ontology(result["version_hash"])["status"] == "ok"
