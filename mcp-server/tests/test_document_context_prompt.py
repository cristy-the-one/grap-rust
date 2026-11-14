"""X-01: the extraction prompt carries the source title and heading path.

Quotes are checked against the chunk alone. A quote that exists only in that
prefix is rejected. The prompt version moves, and the extraction cache key
moves with it.
"""
import json

import pygrap  # noqa: F401

from grap_mcp.incremental import chunk_hash
from grap_mcp.kee import extract, pipeline
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.graph_extract import extend_graph_ontology
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.working_graph import WorkingGraph


SPEC = """# Vehicle Brake Spec

## Pedal

The pedal shall report position.

## Pressure

The sensor shall report pressure.
"""
SENSOR = "The sensor shall report pressure."
PEDAL = "The pedal shall report position."
TITLE = "Vehicle Brake Spec"
HEADING = "Pressure"
PREVIOUS_PROMPT_VERSION = "document-claims-v6-legal-grounded"
PROMPT_VERSION = "document-claims-v7-document-context"


def _write_spec(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "brake.md").write_text(SPEC, encoding="utf-8")
    return docs


def _user_for(provider, chunk):
    return next(user for _system, user in provider.calls if chunk in user)


def test_prompt_carries_title_and_heading_path_before_the_chunk(tmp_path):
    docs = _write_spec(tmp_path)
    provider = StubProvider(default='{"outcomes":[],"relations":[]}')
    pipeline.extract_ontology(str(docs), provider, HashingEmbedder(),
                              sim=1.1, graph_augmented=False)
    user = _user_for(provider, SENSOR)
    chunk_at = user.index(SENSOR)
    assert TITLE in user and user.index(TITLE) < chunk_at
    assert HEADING in user and user.index(HEADING) < chunk_at
    # Outermost section first: the document heading, then the section.
    assert user.index(TITLE) < user.index(HEADING) < chunk_at
    assert "SOURCE TITLE:" in user and "HEADING PATH:" in user
    assert user.index("SOURCE TITLE:") < user.index("HEADING PATH:") < chunk_at
    assert "never from that prefix" in provider.calls[0][0]

    law = tmp_path / "act.json"
    body = "Capitolul I\n\nDispozitii generale.\n\nArticolul 1\n\nMembrii primesc indemnizatia."
    law.write_text(json.dumps({"id": "act", "titlu": "LEGE nr. 2/1992", "text_complet": body}),
                   encoding="utf-8")
    legal = StubProvider(default='{"outcomes":[],"relations":[]}')
    pipeline.extract_ontology(str(law), legal, HashingEmbedder(),
                              sim=1.1, graph_augmented=False)
    sentence = "Membrii primesc indemnizatia."
    legal_user = _user_for(legal, sentence)
    sentence_at = legal_user.index(sentence)
    assert legal_user.index("LEGE nr. 2/1992") < sentence_at
    assert legal_user.index("Capitolul I") < legal_user.index("Articolul 1") < sentence_at


def test_quote_that_appears_only_in_the_prefix_is_rejected(tmp_path):
    docs = _write_spec(tmp_path)

    def handler(_system, user):
        if SENSOR in user:
            return json.dumps({"outcomes": [
                {"name": "Sensor", "requirements": [], "evidence": TITLE}],
                "relations": [{"from": "Sensor", "type": "reports", "to": "pressure",
                               "evidence": HEADING}]})
        if PEDAL in user:
            return json.dumps({"outcomes": [
                {"name": "Pedal", "requirements": [], "evidence": PEDAL}], "relations": []})
        return '{"outcomes":[],"relations":[]}'

    provider = StubProvider(handler=handler)
    result = pipeline.extract_ontology(str(docs), provider, HashingEmbedder(),
                                       sim=1.1, graph_augmented=False)
    user = _user_for(provider, SENSOR)
    chunk_at = user.index(SENSOR)
    assert TITLE not in SENSOR and HEADING not in SENSOR
    assert user.index(TITLE) < chunk_at and user.index(HEADING) < chunk_at
    names = {item["name"] for item in result["ontology"]["process_outcomes"]}
    assert names == {"Pedal"}
    evidence = result["ontology"]["extraction_evidence"]
    quotes = [item.get("evidence", {}).get("quote")
              for record in evidence.values()
              for item in [*record.get("outcomes", []), *record.get("relations", [])]]
    assert TITLE not in quotes and HEADING not in quotes
    assert PEDAL in quotes


def test_prompt_version_is_bumped_and_recorded_on_the_receipt():
    text = "The Test Plan records coverage."
    provider = StubProvider(default=json.dumps({"outcomes": [
        {"name": "Test Plan", "requirements": [], "evidence": text}], "relations": []}))
    frag, reason = extract.extract_chunk(provider, "plan#0", text)
    assert reason is None
    assert frag["extraction"]["prompt_version"] == PROMPT_VERSION
    assert frag["extraction"]["prompt_version"] != PREVIOUS_PROMPT_VERSION


def test_extraction_cache_follows_the_prompt_version(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    text = "The Test Plan records coverage."
    (docs / "plan.txt").write_text(text, encoding="utf-8")
    graph = str(tmp_path / "graph")
    provider = StubProvider(default=json.dumps({"outcomes": [
        {"name": "Test Plan", "requirements": [], "evidence": text}], "relations": []}))
    extend_graph_ontology(graph, str(docs), provider, HashingEmbedder(), relook_ratio=0)
    current_key = chunk_hash(text + "\0" + extract.PROMPT_VERSION)
    previous_key = chunk_hash(text + "\0" + PREVIOUS_PROMPT_VERSION)
    with WorkingGraph.open(graph, HashingEmbedder()) as wg:
        assert wg.manifest["plan#0"] == current_key
        user = provider.calls[0][1]
        assert "SOURCE TITLE: plan\n" in user and user.index("SOURCE TITLE: plan") < user.index(text)
        assert wg.extractions["plan#0"]["prompt_version"] == PROMPT_VERSION
        wg.manifest["plan#0"] = previous_key
        wg.save()
    again = StubProvider(default=json.dumps({"outcomes": [
        {"name": "Test Plan", "requirements": [], "evidence": text}], "relations": []}))
    out = extend_graph_ontology(graph, str(docs), again, HashingEmbedder(), relook_ratio=0)
    assert current_key != previous_key
    assert out["changed"] == 1 and len(again.calls) == 1
