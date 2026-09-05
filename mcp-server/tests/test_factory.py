"""Phase-2 dark-factory automation: verify_graph, run_maintenance,
run_pipeline (idempotent), get_factory_status, and the watcher fingerprint."""
import importlib
import json
import time

from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.llm import StubProvider


def _server(tmp_path, monkeypatch, role="admin"):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", role)
    from grap_mcp.application import GrapApplication
    s = GrapApplication()
    return s


def _stub_extraction(s, monkeypatch):
    def handler(system, user):
        return json.dumps({"outcomes": [{"name": "Requirement Verification",
                                         "characteristics": ["verification method"]}],
                           "relations": []})
    monkeypatch.setattr(s, "get_provider", lambda model=None: StubProvider(handler=handler))
    monkeypatch.setattr(s, "get_embedder", lambda: HashingEmbedder(dim=64))


def _pipeline_cfg(tmp_path, detect_gaps=True, maintenance=False):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "verification.md").write_text(
        "The verification method is defined for each requirement.", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "req.md").write_text(
        "The verification method is specified for each requirement.", encoding="utf-8")
    cfg = {"kg_path": str(tmp_path / "kg"),
           "evidence_dirs": [str(evidence)],
           "extract": {"docs_path": str(docs)},
           "detect_gaps": detect_gaps,
           "maintenance": maintenance}
    p = tmp_path / "pipeline.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def test_verify_graph_clean_kg(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    docs = tmp_path / "d"
    docs.mkdir()
    (docs / "a.md").write_text("some evidence text", encoding="utf-8")
    assert s.ingest_documents(str(docs))["status"] == "ok"
    out = s.verify_graph(sample=50)
    assert out["status"] == "ok"
    assert out["checked"] >= 1
    assert out["integrity_ok"] is True
    assert out["mismatch"] == 0


def test_run_maintenance_reports_steps(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    out = s.run_maintenance()
    assert out["status"] == "ok" and out["ok"] is True
    assert set(out["steps"]) == {"compact_graph", "reindex_entities",
                                 "enforce_retention", "verify_graph"}
    # audited with a machine-readable subject
    ev = s._latest_event(s._get_workspace(), "maintenance_run")
    assert ev is not None and ev.subject == "ok"
    # a clean run keeps the queue clean
    assert s.list_pending_interventions()["count"] == 0


def test_run_pipeline_end_to_end_and_idempotent(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    assert s.set_policy("enabled", True, rationale="factory on")["status"] == "ok"
    _stub_extraction(s, monkeypatch)
    cfg_path = _pipeline_cfg(tmp_path)

    # first run: creates the KG, ingests, extracts, auto-adopts, runs gaps
    r1 = s.run_pipeline(cfg_path)
    assert r1["status"] == "ok"
    assert r1["steps"]["kg"]["status"] == "ok"
    assert r1["steps"]["evidence"][0]["status"] == "ok"
    auto1 = r1["steps"]["extract"]["auto_adoption"]
    assert auto1["pass"] and auto1["adopted"]
    assert r1["steps"]["gaps"]["run_id"]

    # second run on an unchanged corpus: no re-adoption, no new gaps, no queue
    r2 = s.run_pipeline(cfg_path)
    assert r2["status"] == "ok"
    auto2 = r2["steps"]["extract"]["auto_adoption"]
    assert auto2 == {"attempted": False, "reason": "already_active"}
    assert r2["steps"]["evidence"][0]["nodes_added"] == 0     # manifest skipped all
    assert r2["steps"]["gaps"]["delta"]["new"] == 0
    assert s.list_pending_interventions()["count"] == 0

    st = s.get_factory_status()
    assert st["healthy"] is True
    assert st["policy_enabled"] is True
    assert (tmp_path / "ws" / "factory_pulse.json").exists()


def test_empty_evidence_surfaces_and_clears(tmp_path, monkeypatch):
    # Law-ro pilot finding 13: evidence_dirs that match zero files must not
    # pass silently — gap detection against an empty KG reports every
    # characteristic as a gap while the factory claims HEALTHY.
    s = _server(tmp_path, monkeypatch)
    assert s.set_policy("enabled", True, rationale="factory on")["status"] == "ok"
    _stub_extraction(s, monkeypatch)
    cfg_path = _pipeline_cfg(tmp_path)
    evidence = tmp_path / "evidence"
    (evidence / "req.md").unlink()               # nothing ingestible left

    r1 = s.run_pipeline(cfg_path)
    assert r1["steps"]["evidence"][0]["files"] == 0
    q = s.list_pending_interventions()
    assert any(i["kind"] == "evidence_empty" for i in q["pending"])
    assert s.get_factory_status()["healthy"] is False

    # evidence appears -> the next run clears the alarm (latest-state semantics)
    (evidence / "req.md").write_text(
        "The verification method is specified for each requirement.",
        encoding="utf-8")
    r2 = s.run_pipeline(cfg_path)
    assert r2["steps"]["evidence"][0]["files"] == 1
    assert not any(i["kind"] == "evidence_empty"
                   for i in s.list_pending_interventions()["pending"])


def test_gap_regression_surfaces_and_clears(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    ws = s._get_workspace()
    ws.record_event("gap_delta", "new=2;resolved=0;run=run-x")
    q = s.list_pending_interventions()
    assert any(i["kind"] == "gap_regression" for i in q["pending"])
    assert s.get_factory_status()["healthy"] is False
    # the next healthy delta clears the alarm (latest-state semantics)
    ws.record_event("gap_delta", "new=0;resolved=2;run=run-y")
    assert not any(i["kind"] == "gap_regression"
                   for i in s.list_pending_interventions()["pending"])


def test_maintenance_failure_surfaces_until_next_clean_run(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    ws = s._get_workspace()
    ws.record_event("maintenance_run", "failed:verify_graph")
    assert any(i["kind"] == "maintenance_failed"
               for i in s.list_pending_interventions()["pending"])
    ws.record_event("maintenance_run", "ok")
    assert not any(i["kind"] == "maintenance_failed"
                   for i in s.list_pending_interventions()["pending"])


def test_kv_subject_parser():
    from grap_mcp.application import GrapApplication
    s = GrapApplication()
    assert s._kv_subject("checked=10;mismatch=0;unknown=2") == {
        "checked": "10", "mismatch": "0", "unknown": "2"}
    assert s._kv_subject("ok") == {}
    assert s._kv_subject(None) == {}


def test_watcher_fingerprint_changes_with_corpus(tmp_path):
    import run_factory
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "a.md").write_text("one", encoding="utf-8")
    fp1 = run_factory.corpus_fingerprint([str(d)])
    assert fp1 == run_factory.corpus_fingerprint([str(d)])   # stable
    time.sleep(0.01)
    (d / "b.md").write_text("two", encoding="utf-8")
    assert run_factory.corpus_fingerprint([str(d)]) != fp1   # sees changes
    assert run_factory.watched_dirs(
        {"evidence_dirs": ["e"], "extract": {"docs_path": "docs"}}) == ["e", "docs"]
