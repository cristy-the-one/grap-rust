import hashlib
import io
import json

import pytest
import yaml

from grap_mcp.application import GrapApplication
from grap_mcp import ontology, reasoner
from grap_mcp.kee import extract
from grap_mcp.kee import pipeline
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.graph_extract import extend_graph_ontology
from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.llm import OpenAICompatProvider, StubProvider
from grap_mcp.kee.working_graph import WorkingGraph


@pytest.mark.parametrize("graph_augmented", [True, False])
def test_single_use_export_keeps_relations_and_source_quotes(tmp_path, graph_augmented):
    source = "The Council issues Order 17."
    (tmp_path / "act.txt").write_text(source, encoding="utf-8")
    provider = StubProvider(default=json.dumps({"outcomes": [], "relations": [
        {"from": "Council", "type": "issues", "to": "Order 17", "evidence": source}]}))
    result = pipeline.extract_ontology(str(tmp_path), provider, HashingEmbedder(),
                                       sim=1.1, graph_augmented=graph_augmented)
    saved = yaml.safe_load(pipeline.to_yaml(result["ontology"]))
    assert saved["relations"][0]["from"] == "Council"
    assert saved["relations"][0]["to"] == "Order 17"
    receipt = saved["extraction_evidence"]["act#0"]
    assert receipt["content_hash"] == hashlib.sha256(source.encode()).hexdigest()
    assert "source_text" not in receipt  # compact exports retain quoted excerpts
    assert receipt["relations"][0]["evidence"]["quote"] == source
    assert receipt["prompt_version"] == extract.PROMPT_VERSION
    if graph_augmented:
        assert result["fragments"] == 1
        assert result["quality"]["quoted_claims"] == 3
        ids = {o["id"] for o in saved["process_outcomes"]}
        assert saved["relations"][0]["from_id"] in ids
        assert saved["relations"][0]["to_id"] in ids


def test_duplicate_source_labels_fail_before_model_calls(tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "plan.txt").write_text("Plan defines coverage.", encoding="utf-8")
    provider = StubProvider(default=response())
    with pytest.raises(ValueError, match="Duplicate extraction source label"):
        pipeline.extract_ontology(str(tmp_path), provider, HashingEmbedder())
    assert not provider.calls


def test_named_entities_do_not_become_assessment_requirements(tmp_path):
    source = "The Test Plan shall record coverage. The Council issues Order 17."
    (tmp_path / "doc.txt").write_text(source, encoding="utf-8")
    provider = StubProvider(default=json.dumps({"outcomes": [
        {"name": "Test Plan", "requirements": ["coverage"], "evidence": source},
        {"name": "Council", "requirements": [], "characteristics": ["issues Order 17"], "evidence": source}],
        "relations": [{"from": "Council", "type": "issues", "to": "Order 17", "evidence": source}]}))
    result = pipeline.extract_ontology(str(tmp_path), provider, HashingEmbedder(), sim=1.1)
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(pipeline.to_yaml(result["ontology"]), encoding="utf-8")
    loaded = ontology.load_ontology_from_file(str(candidate))
    assert {o.name for o in loaded.process_outcomes} == {"Test Plan", "Council", "Order 17"}
    with WorkingGraph(HashingEmbedder()) as empty_evidence:
        gaps = reasoner.detect_gaps(empty_evidence.kg, loaded)
    assert [(g["outcome_name"], g["missing_characteristic"]) for g in gaps] == [("Test Plan", "coverage")]


def response(char="coverage", evidence=None):
    item = {"name": "Test Plan", "characteristics": [char]}
    if evidence is not None:
        item["evidence"] = evidence
    return json.dumps({"outcomes": [item], "relations": []})


def run(tmp_path, provider, **kwargs):
    return extend_graph_ontology(str(tmp_path / "graph"), str(tmp_path / "docs"),
                                 provider, HashingEmbedder(), relook_ratio=0, **kwargs)


def document(tmp_path, text="The Test Plan records coverage."):
    (tmp_path / "docs").mkdir(exist_ok=True)
    path = tmp_path / "docs" / "plan.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_failed_refresh_preserves_previous_claims_and_source_receipt(tmp_path):
    path = document(tmp_path)
    run(tmp_path, StubProvider(default=response(evidence=path.read_text())))
    old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text("The Test Plan now records ownership.", encoding="utf-8")

    def down(*args):
        raise RuntimeError("provider unavailable")

    out = run(tmp_path, StubProvider(handler=down))
    assert out["stale_chunks"] == ["plan#0"]
    assert out["pending_chunks"] == ["plan#0"]
    assert out["ontology"]["process_outcomes"][0]["required_characteristics"] == ["coverage"]
    app = GrapApplication(tmp_path / "workspace", role="operator")
    receipt = app.get_extraction_evidence(str(tmp_path / "graph"), "plan#0")
    assert receipt["receipt"]["content_hash"] == old_hash
    assert receipt["latest_failure"]["reason"] == "provider"
    assert not receipt["latest_failure"]["quarantined"]
    run(tmp_path, StubProvider(default=response("ownership", path.read_text())))
    with GraphStore(str(tmp_path / "graph" / "graph_store.db")) as store:
        state = store.load()
    assert state.concepts == {0: "Test Plan"}  # refresh preserves identity
    assert state.chars == {0: ["ownership"]}
    assert state.extractions["plan#0"]["outcomes"][0]["evidence"]["status"] == "quoted"
    assert not state.chunk_failures


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_incomplete_http_reply_cannot_retract_previous_claims(tmp_path, monkeypatch, finish_reason):
    path = document(tmp_path)
    run(tmp_path, StubProvider(default=response(evidence=path.read_text())))
    old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text("The Test Plan now records ownership.", encoding="utf-8")
    calls = []

    def incomplete(req, timeout=None):
        calls.append(req)
        # Even syntactically valid empty JSON must not commit an incomplete refresh.
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish_reason,
            "message": {"content": '{"outcomes":[],"relations":[]}'}}]}).encode())

    monkeypatch.setattr("grap_mcp.kee.llm.urllib.request.urlopen", incomplete)
    out = run(tmp_path, OpenAICompatProvider(base_url="http://unused/v1", model="m"))
    assert out["pending_chunks"] == ["plan#0"]
    assert out["ontology"]["process_outcomes"][0]["required_characteristics"] == ["coverage"]
    with GraphStore(str(tmp_path / "graph" / "graph_store.db")) as store:
        assert store.load().extractions["plan#0"]["content_hash"] == old_hash
    assert len(calls) == 1  # repeating the same token cap is not a transport retry


def test_empty_success_retracts_old_claims_and_is_not_retried(tmp_path):
    path = document(tmp_path)
    run(tmp_path, StubProvider(default=response()))
    path.write_text("Page intentionally left blank.", encoding="utf-8")
    p = StubProvider(default='{"outcomes": [], "relations": []}')
    out = run(tmp_path, p)
    assert out["concepts"] == 0 and not out["quarantined"]
    assert out["quality"]["empty_extractions"] == 1
    run(tmp_path, p)
    assert len(p.calls) == 1


def test_malformed_schema_is_not_treated_as_empty_success(tmp_path):
    path = document(tmp_path)
    run(tmp_path, StubProvider(default=response()))
    path.write_text("Changed source", encoding="utf-8")
    for _ in range(4):
        p = StubProvider(default='{"unrelated": true}')
        out = run(tmp_path, p)
        assert out["concepts"] == 1
    assert not p.calls  # quarantined attempt never impersonates a successful refresh
    assert out["stale_chunks"] == ["plan#0"]


def test_quote_checks_distinguish_missing_from_fabricated_support():
    text = "The Test Plan records coverage."
    p = StubProvider(default=response(evidence=text))
    frag, reason = extract.extract_chunk(p, "p", text)
    assert reason is None
    quote = frag["outcomes"][0]["evidence"]
    assert text[quote["start"]:quote["end"]] == quote["quote"] == text
    bad, reason = extract.extract_chunk(StubProvider(default=response(evidence="made up")), "p", text)
    assert bad is None and reason == "filtered"
    legacy, reason = extract.extract_chunk(StubProvider(default=response()), "p", text)
    assert legacy["outcomes"][0]["evidence"]["status"] == "missing"


def test_relook_replaces_its_own_claims_but_retains_other_sources(tmp_path):
    with WorkingGraph(HashingEmbedder(), workdir=str(tmp_path), merge_threshold=1.1) as wg:
        first = {"outcomes": [{"name": "A", "characteristics": ["incorrect"]}, {"name": "B"}],
                 "relations": [{"from": "A", "type": "uses", "to": "B"}]}
        wg.upsert_fragment(first, "first")
        wg.upsert_fragment({"outcomes": [{"name": "A", "characteristics": ["shared"]}]}, "other")
        wg.replace_fragment({"outcomes": [{"name": "A", "characteristics": ["correct"]}]}, "first")
        assert wg.canonical == {0: "A"}
        assert set(wg.chars[0]) == {"shared", "correct"}
        assert wg.node_sources[0] == {"first", "other"}
        assert wg.kg.edge_count() == 0


def test_explicit_refresh_reprocesses_unchanged_sources(tmp_path):
    document(tmp_path)
    p = StubProvider(default=response())
    run(tmp_path, p)
    run(tmp_path, p)
    assert len(p.calls) == 1
    run(tmp_path, p, refresh=True)
    assert len(p.calls) == 2


def test_malformed_requirements_cannot_erase_previous_criteria(tmp_path):
    path = document(tmp_path)
    run(tmp_path, StubProvider(default=response("coverage")))
    path.write_text("The Test Plan shall record ownership.", encoding="utf-8")
    provider = StubProvider(default=json.dumps({
        "outcomes": [{"name": "Test Plan", "requirements": "ownership"}], "relations": []}))
    result = run(tmp_path, provider)
    assert result["stale_chunks"] == ["plan#0"]
    assert result["quality"]["schema_failures"] == 1
    assert result["ontology"]["process_outcomes"][0]["required_characteristics"] == ["coverage"]


def test_deleted_failed_source_clears_failure_record(tmp_path):
    path = document(tmp_path)
    run(tmp_path, StubProvider(default="no json"))
    path.unlink()
    out = run(tmp_path, StubProvider(default="no json"))
    assert not out["pending_chunks"] and out["quality"]["quarantined_total"] == 0
    with GraphStore(str(tmp_path / "graph" / "graph_store.db")) as store:
        assert not store.load().chunk_failures


def test_quoted_relation_can_supply_an_omitted_endpoint_without_another_call():
    text = "Consiliul emite Ordinul 17."
    p = StubProvider(default=json.dumps({"outcomes": [{"name": "Ordinul 17", "evidence": text}],
          "relations": [{"from": "Consiliul", "type": "emite", "to": "Ordinul 17", "evidence": text}]}))
    frag, reason = extract.extract_chunk(p, "law", text)
    assert reason is None and len(p.calls) == 1
    assert {o["name"] for o in frag["outcomes"]} == {"Ordinul 17", "Consiliul"}
    assert next(o for o in frag["outcomes"] if o["name"] == "Consiliul")["characteristics"] == []


def test_endpoint_must_be_a_whole_quoted_mention():
    text = "The Planet is visible."
    frag = extract.normalize_fragment({"relations": [{"from": "Plan", "type": "sees", "to": "Alien",
                                                       "evidence": text}]}, "doc", source_text=text)
    assert frag["outcomes"] == []  # neither substring Plan nor invented Alien gets promoted
