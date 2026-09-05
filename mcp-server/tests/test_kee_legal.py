"""Tests for the legal citation network + build_legal_graph merge."""

import json

from grap_mcp.kee import legal


def test_citation_pairs_resolves_within_set():
    acts = [
        legal.parse_act({"id": "1", "tip_act": "LEGE", "numar": "166",
                         "data_vigoare": "1999-11-09", "titlu": "Lege A",
                         "text_complet": "continut"}),
        legal.parse_act({"id": "2", "tip_act": "HOTARARE", "numar": "671",
                         "data_vigoare": "2000-08-17", "titlu": "HG B",
                         "text_complet": "In temeiul LEGEA nr. 166 din 9 noiembrie 1999"}),
    ]
    assert legal.citation_pairs(acts) == [("2", "1")]


def test_build_legal_graph_merges_citations(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", "operator")
    from grap_mcp.application import GrapApplication
    s = GrapApplication()

    corpus = tmp_path / "acts"
    corpus.mkdir()
    (corpus / "1.json").write_text(json.dumps(
        {"id": "1", "tip_act": "LEGE", "numar": "166", "data_vigoare": "1999-11-09",
         "titlu": "Lege A", "text_complet": "continut"}), encoding="utf-8")
    (corpus / "2.json").write_text(json.dumps(
        {"id": "2", "tip_act": "HOTARARE", "numar": "671", "data_vigoare": "2000-08-17",
         "titlu": "HG B", "text_complet": "In temeiul LEGEA nr. 166 din 9 noiembrie 1999"}),
        encoding="utf-8")

    r = s.build_legal_graph(str(corpus), max_files=10, with_relations=False)
    assert r["status"] == "ok"
    assert r["counts"]["acts"] == 2
    assert r["counts"]["cites"] == 1
    assert r["counts"]["shown_nodes"] == 2          # both acts connected by the cite
    assert {e["type"] for e in r["edges"]} == {"cites"}
    assert {n["kind"] for n in r["nodes"]} == {"act"}
