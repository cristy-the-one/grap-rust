import json
from pathlib import Path

from grap_mcp.kee import extract, qualified
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.graph_extract import extend_graph_ontology
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.pipeline import read_chunks, to_yaml
from grap_mcp.kee.working_graph import WorkingGraph

RULE = "Executorii bancari au obligaţia sa păstreze secretul profesional, chiar şi după încetarea funcţiei, cu excepţia cazurilor în care legea îi eliberează de aceasta obligaţie."


def test_abbreviated_article_heading_enables_rule_protection():
    assert qualified.plan("ART. 12 Instituţia funcţionează autonom.")["units"]


def test_full_document_rule_survives_legacy_cuts_and_article_filter(tmp_path):
    file = tmp_path / "law.txt"
    text = "+ Articolul 3 " + RULE
    file.write_text(text, encoding="utf-8")
    contexts = {}
    chunks = read_chunks(str(file), chunk_chars=80, rule_contexts=contexts)
    assert len(chunks) > 1
    records = {}
    for source, piece in chunks:
        context = contexts[source]
        assert context["units"][0]["statement"] == RULE
        raw = json.dumps({"outcomes": [{"name": "Articolul 3", "requirements": [RULE], "evidence": piece}],
                          "relations": [{"from": "Executorii bancari", "type": "au obligaţia sa păstreze", "to": "secretul profesional", "evidence": piece}],
                          "units": {"u0": {"kind": "obligation", "subject": "Executorii bancari", "ignored_extra": "not trusted"}}})
        frag, error = extract.parse_reply(raw, source, piece, StubProvider(), context)
        assert error is None and frag["relations"] == []
        rule = frag["qualified_rules"][0]
        assert rule["statement"] == RULE and rule["exceptions"]
        span = rule["evidence"][0]
        assert text[span["start"]:span["end"]] == RULE
        assert rule["subject"] == "Executorii bancari"
        assert frag["deferred_proposals"]
        records[source] = extract.extraction_record(frag)
    assert len(qualified.report(records)["rules"]) == 1


def test_metadata_only_output_cannot_silently_lose_operative_rule():
    frag, error = extract.parse_reply('{"outcomes":[],"relations":[]}', "law#0", RULE, StubProvider())
    assert error is None
    assert frag["qualified_rules"][0]["statement"] == RULE
    assert frag["rule_coverage"]["unresolved"] == 1
    assert frag["qualified_rules"][0]["kind"] == "obligation"


def test_rule_receipt_reloads_exports_and_retracts_without_entity_nodes(tmp_path):
    raw = json.dumps({"outcomes": [], "relations": [], "units": {"u0": {"kind": "obligation", "subject": "Executorii bancari"}}})
    frag, _ = extract.parse_reply(raw, "law#0", RULE, StubProvider())
    path = tmp_path / "graph"
    with WorkingGraph(HashingEmbedder(64), workdir=str(path)) as wg:
        wg.upsert_fragment(frag, "law#0")
        wg.save()
    with WorkingGraph.open(str(path), HashingEmbedder(64)) as wg:
        ontology = wg.to_candidate_ontology()
        assert ontology["process_outcomes"] == []
        assert ontology["qualified_legal"]["rules"][0]["statement"] == RULE
        assert "qualified_legal" in to_yaml(ontology)
        wg.retract_chunk("law#0")
        assert wg.to_candidate_ontology()["qualified_legal"]["rules"] == []


def test_edit_outside_entity_chunk_invalidates_full_rule(tmp_path):
    docs, graph = tmp_path / "docs", tmp_path / "graph"
    docs.mkdir()
    file = docs / "law.txt"
    file.write_text("+ Articolul 3 " + RULE, encoding="utf-8")
    provider = StubProvider(default='{"outcomes":[],"relations":[]}')
    first = extend_graph_ontology(str(graph), str(docs), provider, HashingEmbedder(), chunk_chars=80)
    calls = len(provider.calls)
    extend_graph_ontology(str(graph), str(docs), provider, HashingEmbedder(), chunk_chars=80)
    assert len(provider.calls) == calls
    file.write_text("+ Articolul 3 " + RULE.replace("legea", "părţile"), encoding="utf-8")
    second = extend_graph_ontology(str(graph), str(docs), provider, HashingEmbedder(), chunk_chars=80)
    assert second["changed"] == first["added"]
    assert all("părţile" in r["statement"] for r in second["ontology"]["qualified_legal"]["rules"])


def test_overlong_rule_is_retained_and_explicitly_unresolved():
    text = "+ Articolul 1 " + ("cuvânt " * 2500)
    context = qualified.plan(text)
    assert qualified.prompt_units(context) == {}
    frag, _ = extract.parse_reply('{"outcomes":[],"relations":[]}', "long", text, StubProvider(), context)
    assert frag["qualified_rules"][0]["statement"] == text[len("+ Articolul 1 "):].strip()
    assert frag["rule_coverage"]["unresolved"] == 1


def test_one_focused_retry_repairs_unsupported_right_label():
    text = "+ Articolul 1 Dosarele pot fi preluate pe baza solicitării scrise."
    class Provider:
        calls = 0
        def complete(self, system, user):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"outcomes": [], "relations": [], "units": {"u0": {"kind": "right", "subject": "Dosarele"}}})
            assert "Dosarele pot fi preluate" in user
            return json.dumps({"units": {"u0": {"kind": "permission", "subject": "Dosarele"}}})
    p = Provider()
    frag, error = extract.extract_chunk(p, "law", text)
    assert error is None and p.calls == 2
    assert frag["qualified_rules"][0]["kind"] == "permission"
    assert frag["qualified_rules"][0]["statement"].endswith("pe baza solicitării scrise.")
    assert frag["classification_repair"]["unit_ids"] == ["u0"]


def test_repair_failure_keeps_rule_and_stops_after_one_attempt():
    class Failing:
        calls = 0
        def complete(self, system, user):
            self.calls += 1
            if self.calls == 1:
                return '{"outcomes":[],"relations":[]}'
            raise RuntimeError("offline")
    p = Failing()
    frag, error = extract.extract_chunk(p, "law", RULE)
    assert error is None and p.calls == 2
    assert frag["qualified_rules"][0]["statement"] == RULE
    assert frag["classification_repair"]["error"] == "offline"
    assert frag["rule_coverage"]["unresolved"] == 1
