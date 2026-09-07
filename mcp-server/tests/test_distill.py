import copy
import hashlib
import json

import pytest

from grap_mcp.kee.distill import distill_ontology
from grap_mcp.kee.llm import StubProvider


def source_record(text, names, relations):
    ev = {"status": "quoted", "quote": text, "start": 0, "end": len(text)}
    return {"source_text": text, "content_hash": hashlib.sha256(text.encode()).hexdigest(),
            "outcomes": [{"name": name, "evidence": ev} for name in names],
            "relations": [{**r, "evidence": ev} for r in relations]}


def corpus():
    return {"extraction_evidence": {s: source_record(text, ["Guvernul", "prezenta lege"], [
        {"from": "prezenta lege", "type": "adoptată de", "to": "Guvernul"}])
        for s, text in [("a#0", "prezenta lege adoptată de Guvernul."),
                        ("a#1", "prezenta lege adoptată de Guvernul."),
                        ("b#0", "prezenta lege adoptată de Guvernul.")]}}


def answer(system, user):
    items = json.loads(user)["items"]
    rows = []
    for item in items:
        row = dict(id=item["id"], status="entity", label="Act normativ", definition="Un act normativ.",
                   reverse=False, modality="uncertain", scope_quote="", reason="Source")
        if "proposal" in item:
            row = {"id": item["id"], "status": "verified", "reason": "Checked source"}
        elif "type" in item:
            row.update(status="supported", label="adoptă", definition="Autorul adoptă actul.",
                       reverse=True, modality="asserted")
        elif item["name"] == "Guvernul":
            row.update(label="Autoritate", definition="O autoritate publică.")
        if "name" in item:
            for key in ("reverse", "modality", "scope_quote"):
                del row[key]
        rows.append(row)
    return json.dumps({"items": rows})


def test_source_first_normalization_reversal_and_document_support(tmp_path):
    ontology = corpus()
    original = copy.deepcopy(ontology)
    out = distill_ontology(ontology, StubProvider(handler=answer), cache_dir=tmp_path)
    assert ontology == original
    assert out["status"] == "complete"
    assert len(out["entities"]) == 4  # no cross-document 'prezenta lege' identity merge
    assert out["patterns"][0]["document_support"] == 2  # 3 repeated chunks != 3 documents
    pattern = out["patterns"][0]
    assert (pattern["from_class"], pattern["predicate"], pattern["to_class"]) == ("Autoritate", "adoptă", "Act normativ")
    assert pattern["status"] == "recurring_candidate"
    entities = {e["id"]: e for e in out["entities"]}
    for rel in out["relations"]:
        assert entities[rel["normalized"]["from"]]["names"] == ["Guvernul"]
        assert rel["quote"] == ontology["extraction_evidence"][rel["source"]]["source_text"]


def test_resume_does_not_repeat_calls_and_zero_budget_does_not_call(tmp_path):
    provider = StubProvider(handler=answer)
    first = distill_ontology(corpus(), provider, cache_dir=tmp_path, max_calls=1)
    assert first["status"] == "partial" and first["calls"] == 1
    second = distill_ontology(corpus(), provider, cache_dir=tmp_path)
    assert second["status"] == "complete" and second["cache_hits"] == 1
    third = distill_ontology(corpus(), provider, cache_dir=tmp_path, max_calls=0)
    assert third["status"] == "complete" and third["calls"] == 0
    assert third["patterns"] == second["patterns"]
    assert third["relations"] == second["relations"]


def test_verifier_rejects_semantic_overreach():
    def reject(system, user):
        obj = json.loads(answer(system, user))
        if "proposal" in json.loads(user)["items"][0]:
            for r in obj["items"]:
                r["status"] = "rejected"
                r["reason"] = "The source approves; proposal wrongly says issues."
        return json.dumps(obj)
    result = distill_ontology(corpus(), StubProvider(handler=reject))
    assert not result["patterns"]
    assert all(r["status"] == "rejected" for r in result["relations"])


@pytest.mark.parametrize("fault", ["invented_scope", "duplicate", "missing", "repaired_verification"])
def test_malformed_model_batches_stay_pending(fault):
    def broken(system, user):
        obj = json.loads(answer(system, user))
        rows = obj["items"]
        if fault == "invented_scope":
            rows[0]["scope_quote"] = "Made up restriction"
        elif fault == "duplicate":
            rows[-1]["id"] = rows[0]["id"]
        elif fault == "missing":
            rows.pop()
        elif "proposal" in json.loads(user)["items"][0]:
            rows[0]["reverse"] = False
        return json.dumps(obj)
    result = distill_ontology(corpus(), StubProvider(handler=broken))
    if fault == "duplicate":
        assert result["status"] == "complete" and result["failures"]  # singleton fallback repaired protocol
        return
    assert result["status"] == "partial" and result["failures"]
    assert not result["patterns"]


def test_source_tampering_fails_before_model_and_bad_quotes_are_excluded():
    ontology = corpus()
    ontology["extraction_evidence"]["a#0"]["source_text"] += "tampered"
    provider = StubProvider(handler=answer)
    with pytest.raises(ValueError, match="fingerprint"):
        distill_ontology(ontology, provider)
    assert not provider.calls
    ontology = corpus()
    ontology["extraction_evidence"]["a#0"]["outcomes"][0]["evidence"] = {"status": "unquoted"}
    result = distill_ontology(ontology, provider)
    assert len(result["excluded"]) == 2  # bad mention and dangling edge


def test_missing_receipts_and_receipt_only_are_explicit():
    with pytest.raises(ValueError, match="receipts"):
        distill_ontology({"process_outcomes": []}, StubProvider())
    ontology = corpus()
    for r in ontology["extraction_evidence"].values():
        del r["source_text"]
    out = distill_ontology(ontology, StubProvider(handler=answer))
    assert {i["verification"] for i in out["mentions"]} == {"receipt_only"}


def test_definition_collision_abstains_instead_of_changing_class_meaning():
    def conflict(system, user):
        obj = json.loads(answer(system, user))
        if "name" in json.loads(user)["items"][0]:
            obj["items"][-1]["definition"] = "A different class."
        return json.dumps(obj)
    out = distill_ontology(corpus(), StubProvider(handler=conflict))
    assert out["mentions"][-1]["decision"]["status"] == "uncertain"
    assert out["patterns"][0]["document_support"] == 1
    assert out["patterns"][0]["status"] == "sparse_candidate"


def test_public_operation_is_workspace_owned_and_operator_only(tmp_path):
    from grap_mcp.application import GrapApplication
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(corpus()), encoding="utf-8")
    provider = StubProvider(handler=answer)
    app = GrapApplication(tmp_path / "workspace", role="operator", provider_factory=lambda **_: provider)
    result = app.distill_ontology(str(path), max_calls=30)
    assert result["status"] == "ok" and result["report"]["status"] == "complete"
    assert "distill_ontology" in app.list_capabilities()["domains"]["knowledge"]
    assert app.distill_ontology(str(path), max_calls=0)["report"]["calls"] == 0
    other = GrapApplication(tmp_path / "other", role="operator", provider_factory=lambda **_: provider)
    assert other.distill_ontology(str(path), max_calls=0)["report"]["status"] == "partial"
    viewer = GrapApplication(tmp_path / "viewer", role="viewer", provider_factory=lambda **_: provider)
    assert viewer.distill_ontology(str(path))["status"] == "error"
    assert not (tmp_path / "viewer" / "vocabulary-cache").exists()


def test_loader_reads_durable_receipts_without_embedding_or_graph(tmp_path):
    from grap_mcp.kee.distill import load_input
    from grap_mcp.kee.graph_store import GraphStore
    with GraphStore(str(tmp_path / "graph_store.db")) as store:
        for source, record in corpus()["extraction_evidence"].items():
            store.conn.execute("INSERT INTO extractions VALUES (?,?)", (source, json.dumps(record)))
        store.conn.commit()
    ontology, sources = load_input(tmp_path)
    assert ontology == corpus() and sources == {}


def test_modality_guard_overrides_even_two_agreeing_models():
    ontology = {"extraction_evidence": {"a#0": source_record(
        "Se autorizează Agenția să preia activele Băncii.", ["Agenția", "Banca"],
        [{"from": "Agenția", "type": "preia activele", "to": "Banca"}])}}
    out = distill_ontology(ontology, StubProvider(handler=answer))
    assert out["relations"][0]["status"] == "uncertain"
    assert out["relations"][0]["guard"] == "explicit_modality_not_preserved"
    assert not out["patterns"]


def test_reflexive_artifact_is_retained_as_exclusion_not_a_schema_pattern():
    ontology = {"extraction_evidence": {"a#0": source_record(
        "Comisia se numește și își desfășoară", ["Comisia"],
        [{"from": "Comisia", "type": "se numește și își desfășoară", "to": "Comisia"}])}}
    out = distill_ontology(ontology, StubProvider(handler=answer))
    assert not out["relations"] and not out["patterns"]
    assert out["excluded"][0]["reason"] == "self_relation_without_reflexive_evidence"


def test_malformed_batch_recovery_is_cached(tmp_path):
    def singleton_only(system, user):
        if len(json.loads(user)["items"]) > 1:
            return '{"items": []}'
        return answer(system, user)
    provider = StubProvider(handler=singleton_only)
    report = distill_ontology(corpus(), provider, cache_dir=tmp_path, max_calls=40)
    assert report["status"] == "complete" and report["failures"]
    resumed = distill_ontology(corpus(), provider, cache_dir=tmp_path, max_calls=0)
    assert resumed["status"] == "complete" and resumed["calls"] == 0
    assert resumed["patterns"] == report["patterns"]


def test_reading_missing_extraction_graph_never_creates_a_dummy_graph(tmp_path):
    from grap_mcp.application import GrapApplication
    def forbidden():
        raise AssertionError("A read of a missing store must not load embeddings")
    app = GrapApplication(tmp_path / "ws", role="operator", embedder_factory=forbidden)
    missing = tmp_path / "missing-graph"
    assert app.export_ontology(str(missing))["status"] == "error"
    assert app.open_extraction_graph(str(missing))["status"] == "error"
    assert not missing.exists()
