"""Phase-1 dark-factory loop: policy-gated auto-adoption, rollback, diffs,
and the exception queue — integration-style against a real tmp workspace."""
import hashlib
import importlib
import json
import os

from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.llm import StubProvider

PROPOSAL_TMPL = """\
version: kee-candidate
process_outcomes:
- id: {oid}
  name: {name}
  description: {name} (proposed).
  required_characteristics:
  - {char}
mapping_hints:
- outcome: {oid}
  indicators:
  - keywords: [{char}]
concept_hashes:
  {oid}: {chash}
source_chunk_counts:
  {oid}: 2
"""


def _proposal_yaml(oid, name, char):
    return PROPOSAL_TMPL.format(
        oid=oid, name=name, char=char,
        chash=hashlib.sha256(name.encode("utf-8")).hexdigest())


def _server(tmp_path, monkeypatch, role="operator"):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", role)
    import grap_mcp.server as s
    importlib.reload(s)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    return s


def _publish(s, yaml_text):
    return s._get_workspace().publish_ontology(
        yaml_text, metadata={"kind": "proposed", "source": "test"}).hash


def _stub_extraction(s, monkeypatch):
    """Route extraction through a deterministic stub + hashing embedder."""
    def handler(system, user):
        u = user.lower()
        if "verification" in u:
            return json.dumps({"outcomes": [{"name": "Requirement Verification",
                                             "characteristics": ["verification method"]}],
                               "relations": []})
        return json.dumps({"outcomes": [{"name": "Quality Assurance",
                                         "characteristics": ["quality plan"]}],
                           "relations": []})
    monkeypatch.setattr(s, "get_provider", lambda model=None: StubProvider(handler=handler))
    monkeypatch.setattr(s, "get_embedder", lambda: HashingEmbedder(dim=64))


def _docs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "verification.md").write_text(
        "The verification method is defined for each requirement.", encoding="utf-8")
    (docs / "quality.md").write_text(
        "Quality assurance maintains a quality plan.", encoding="utf-8")
    return str(docs)


# ---- rollback -------------------------------------------------------------

def test_rollback_restores_previous_version(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    v1 = _publish(s, _proposal_yaml("req_ver", "Requirement Verification", "verification_method"))
    v2 = _publish(s, _proposal_yaml("qa", "Quality Assurance", "quality_plan"))

    a1 = s.adopt_proposed_ontology(v1, rationale="first")
    h1 = a1["new_version_hash"]
    a2 = s.adopt_proposed_ontology(v2, rationale="second")
    assert s._state.ontology_version == a2["new_version_hash"]

    rb = s.rollback_ontology(h1, reason="second version regressed gap detection")
    assert rb["status"] == "ok"
    assert rb["rolled_back_from"] == a2["new_version_hash"]
    assert s._state.ontology_version == h1
    events = s._get_workspace().query_events("ontology_rolled_back")
    assert any(e.subject == h1 for e in events)


def test_rollback_requires_reason_and_known_version(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.rollback_ontology("deadbeef", reason=None)["status"] == "error"
    out = s.rollback_ontology("0" * 64, reason="nope")
    assert out["status"] == "error" and "Unknown version" in out["message"]


# ---- policy gate + auto-adoption -------------------------------------------

def test_auto_adoption_when_policy_passes(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch, role="admin")
    assert s.set_policy("enabled", True, rationale="enable the factory")["status"] == "ok"
    _stub_extraction(s, monkeypatch)

    res = s.extract_ontology(_docs(tmp_path))
    assert res["status"] == "ok"
    auto = res["auto_adoption"]
    assert auto["attempted"] and auto["pass"] and auto["adopted"]
    # the factory adopted: ontology active, audited as policy
    assert s._state.ontology is not None
    adopted = s._get_workspace().query_events("ontology_adopted")
    assert any(e.subject == res["version_hash"] for e in adopted)
    # nothing pending
    q = s.list_pending_interventions()
    assert q["status"] == "ok" and q["count"] == 0


def test_policy_rejection_lands_in_exception_queue(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch, role="admin")
    assert s.set_policy("enabled", True, rationale="enable")["status"] == "ok"
    assert s.set_policy("min_source_chunks", 99, rationale="force failure")["status"] == "ok"
    _stub_extraction(s, monkeypatch)

    res = s.extract_ontology(_docs(tmp_path))
    auto = res["auto_adoption"]
    assert auto["attempted"] and auto["pass"] is False
    assert "provenance" in auto["failed_checks"]

    q = s.list_pending_interventions()
    kinds = {i["subject"]: i["kind"] for i in q["pending"]}
    assert kinds.get(res["version_hash"]) == "policy_rejected"

    # a human clears the exception the governed way
    out = s.adopt_proposed_ontology(res["version_hash"],
                                    rationale="reviewed; thin provenance acceptable here")
    assert out["status"] == "ok"
    assert s.list_pending_interventions()["count"] == 0


def test_superseded_proposals_clear_from_queue(tmp_path, monkeypatch):
    # A proposal rejected (or awaiting review) before a NEWER proposal was
    # adopted is dead work, not pending work (law-ro pilot finding).
    s = _server(tmp_path, monkeypatch, role="admin")
    assert s.set_policy("enabled", True, rationale="on")["status"] == "ok"
    assert s.set_policy("min_source_chunks", 99, rationale="force reject")["status"] == "ok"
    _stub_extraction(s, monkeypatch)
    res = s.extract_ontology(_docs(tmp_path))
    assert res["auto_adoption"]["pass"] is False
    assert s.list_pending_interventions()["count"] == 1      # rejected, pending

    # a different, newer proposal gets adopted -> the old rejection is superseded
    assert s.set_policy("min_source_chunks", 1, rationale="restore")["status"] == "ok"
    v2 = _publish(s, _proposal_yaml("req_ver", "Requirement Verification",
                                    "verification_method"))
    assert s.adopt_proposed_ontology(v2, rationale="newer candidate")["status"] == "ok"
    assert s.list_pending_interventions()["count"] == 0


def test_proposals_await_review_when_policy_disabled(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    _stub_extraction(s, monkeypatch)
    res = s.extract_ontology(_docs(tmp_path))
    assert res["auto_adoption"] is None          # factory off -> human loop
    q = s.list_pending_interventions()
    assert any(i["subject"] == res["version_hash"]
               and i["kind"] == "proposal_awaiting_review" for i in q["pending"])


def test_set_policy_is_governed(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)          # operator, not admin
    assert "requires 'admin'" in s.set_policy("enabled", True, rationale="x")["message"]
    s2 = _server(tmp_path, monkeypatch, role="admin")
    assert s2.set_policy("nope", 1, rationale="x")["status"] == "error"
    assert s2.set_policy("max_outcomes_added", -1, rationale="x")["status"] == "error"
    ok = s2.set_policy("max_outcomes_added", 3, rationale="tighter budget")
    assert ok["adoption_policy"]["max_outcomes_added"] == 3
    assert s2.get_config()  # config loads fine afterwards


# ---- evaluate + diff tools --------------------------------------------------

def test_evaluate_and_diff_against_active(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    v1 = _publish(s, _proposal_yaml("req_ver", "Requirement Verification", "verification_method"))
    assert s.adopt_proposed_ontology(v1, rationale="baseline")["status"] == "ok"

    v2 = _publish(s, _proposal_yaml("qa", "Quality Assurance", "quality_plan"))
    verdict = s.evaluate_proposal(v2)
    assert verdict["status"] == "ok"
    churn = next(c for c in verdict["checks"] if c["name"] == "churn")
    assert churn["ok"] is False                  # removes req_ver (budget 0)
    assert verdict["pass"] is False

    d = s.diff_ontologies(v2)                    # against active by default
    assert d["status"] == "ok"
    assert d["outcomes_added"] == ["qa"]
    assert d["outcomes_removed"] == ["req_ver"]


# ---- run deltas --------------------------------------------------------------

def test_diff_runs_shows_resolved_gaps(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    onto = os.path.join(os.path.dirname(__file__), "..", "..",
                        "docs", "examples", "minimal-aspice-ontology.yaml")
    assert s.load_ontology(onto)["status"] == "ok"

    r1 = s.detect_gaps()
    assert r1["gaps_found"] >= 1

    docs = tmp_path / "evidence"
    docs.mkdir()
    (docs / "req.md").write_text(
        "The verification method is specified for each requirement. "
        "Acceptance criteria are defined. The test environment is identified.",
        encoding="utf-8")
    assert s.ingest_documents(str(docs))["status"] == "ok"
    r2 = s.detect_gaps()

    d = s.diff_runs(r1["run_id"], r2["run_id"])
    assert d["status"] == "ok"
    assert d["resolved_gaps"]                     # evidence closed gaps
    assert not d["new_gaps"]
    assert d["counts"]["a"] == r1["gaps_found"]

    assert "not found" in s.diff_runs("run-nope", r2["run_id"])["message"]


# ---- the ontology must not pollute the evidence index ---------------------
#
# Adoption used to project the ontology INTO the evidence KG. At 950 documents
# that took the index from 4,692 nodes to 23,170 — 80% of what BM25 ranked over
# was scaffolding — which was the whole of the canary's ~830 "regression" floor
# and a standing tax on retrieval for every gap run. Excluding those nodes from
# results does not stop them being ranked.

def test_adoption_does_not_grow_the_evidence_index(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch, role="admin")
    before = s._state.kg.node_count()

    v = _publish(s, _proposal_yaml("req_ver", "Requirement Verification", "verification_method"))
    assert s.adopt_proposed_ontology(v, rationale="first")["status"] == "ok"

    assert s._state.kg.node_count() == before, "ontology leaked into the evidence KG"
    # ...and it really was projected, just somewhere else
    assert s._state.ontology_kg is not None
    assert s._state.ontology_kg.node_count() > 0
    assert len(s._state.ontology_nodes) > 0
    # the evidence-side exclusion set stays empty: nothing to exclude any more
    assert s._state.projected_nodes == set()


def test_ontology_graph_describes_only_the_current_ontology(tmp_path, monkeypatch):
    """Rebuilt per projection, so it cannot accumulate every version ever."""
    s = _server(tmp_path, monkeypatch, role="admin")
    v1 = _publish(s, _proposal_yaml("req_ver", "Requirement Verification", "verification_method"))
    assert s.adopt_proposed_ontology(v1, rationale="first")["status"] == "ok"
    n1 = s._state.ontology_kg.node_count()

    v2 = _publish(s, _proposal_yaml("qa", "Quality Assurance", "quality_plan"))
    assert s.adopt_proposed_ontology(v2, rationale="second")["status"] == "ok"
    n2 = s._state.ontology_kg.node_count()

    # second ontology is the same shape as the first: the graph must not have
    # grown by accumulating the first one alongside it
    assert n2 == n1, f"ontology graph accumulated across adoptions ({n1} -> {n2})"
    assert s._state.kg.node_count() == 0
