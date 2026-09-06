"""Tests for the legal citation network + build_legal_graph merge."""

import json

from grap_mcp.kee import legal


def test_act_year_comes_from_heading_not_effective_date():
    act = legal.parse_act({"id":"1", "tip_act":"HOTĂRÂRE", "numar":"997",
                           "titlu":"HOTĂRÂRE nr. 997 din 31 decembrie 1998", "data_vigoare":"1999-01-13"})
    assert act["year"] == "1998"


def test_short_references_and_ambiguous_keys_are_not_guessed():
    acts = [legal.parse_act({"id":key,"tip_act":"LEGE","numar":"58",
                            "titlu":"LEGE nr. 58 din 5 martie 1998"}) for key in ("a","b")]
    source = legal.parse_act({"id":"source","text_complet":"Potrivit Legii bancare nr. 58/1998."})
    assert legal.citation_pairs([acts[0],source]) == [("source","a")]
    assert legal.citation_pairs([*acts,source]) == []


def test_heading_preserves_special_number_and_missing_year_stays_unknown():
    a = legal.parse_act({"id":"a","tip_act":"HOTĂRÂRE", "numar":"11",
                         "titlu":"HOTĂRÂRE nr. 1.1/X din 13 iulie 1999"})
    assert a["nr"] == "1.1/X"
    assert legal.parse_act({"data_vigoare":"1999-01-01"})["year"] == ""


def test_colliding_own_heading_is_not_an_unresolved_external_citation():
    acts = [legal.parse_act({"id":key,"tip_act":"LEGE","numar":"58",
                            "titlu":"LEGE nr. 58 din 5 martie 1998",
                            "text_complet":"LEGE nr. 58 din 5 martie 1998 text"}) for key in ("a","b")]
    assert legal.citation_report(acts)["references"] == []


def test_citation_pairs_resolves_within_set():
    acts = [
        legal.parse_act({"id": "1", "tip_act": "LEGE", "numar": "166",
                         "data_vigoare": "1999-11-09", "titlu": "LEGE nr. 166 din 9 noiembrie 1999",
                         "text_complet": "continut"}),
        legal.parse_act({"id": "2", "tip_act": "HOTARARE", "numar": "671",
                         "data_vigoare": "2000-08-17", "titlu": "HOTARARE nr. 671 din 17 august 2000",
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
         "titlu": "LEGE nr. 166 din 9 noiembrie 1999", "text_complet": "continut"}), encoding="utf-8")
    (corpus / "2.json").write_text(json.dumps(
        {"id": "2", "tip_act": "HOTARARE", "numar": "671", "data_vigoare": "2000-08-17",
         "titlu": "HOTARARE nr. 671 din 17 august 2000", "text_complet": "In temeiul LEGEA nr. 166 din 9 noiembrie 1999"}),
        encoding="utf-8")

    r = s.build_legal_graph(str(corpus), max_files=10, with_relations=False)
    assert r["status"] == "ok"
    assert r["counts"]["acts"] == 2
    assert r["counts"]["cites"] == 1
    assert r["counts"]["shown_nodes"] == 2          # both acts connected by the cite
    assert {e["type"] for e in r["edges"]} == {"cites"}
    assert {n["kind"] for n in r["nodes"]} == {"act"}
    assert "continut" in s._state.kg.get_node_text(0)
    from pathlib import Path
    saved = json.loads(Path(r["citation_report"]).read_text(encoding="utf-8"))
    assert saved["references"][0]["quote"] == "LEGEA nr. 166 din 9 noiembrie 1999"
