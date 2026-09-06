import json
from pathlib import Path

import pytest

from grap_mcp.application import GrapApplication
from grap_mcp.kee.legal_claims import LegalClaimStore
from grap_mcp.kee.legal_rules import extract_rules, source_units
from grap_mcp.kee.llm import StubProvider


def labels(**rows):
    return StubProvider(default=json.dumps({"units": {
        name: {"kind": kind, "subject_start": first, "subject_end": last}
        for name, (kind, first, last) in rows.items()}}))


def assert_anchored(text, claims):
    for claim in claims:
        assert claim["statement"] in text
        for span in [*claim["evidence"], *claim["context_evidence"],
                     *([claim["subject_evidence"]] if "subject_evidence" in claim else [])]:
            assert text[span["start"]:span["end"]] == span["quote"]


def test_preserves_qualifications_when_model_only_classifies():
    text = "+ Articolul 4 Ordinele nu vor fi acceptate decât în cazuri excepţionale şi numai cu acordul expres al directorului. Cazurile excepţionale includ boala."
    out = extract_rules(labels(u0=("prohibition", 0, 0), u1=("fact", 0, 0)), text)
    rule = out["claims"][0]
    assert rule["subject"] == "Ordinele"
    assert "acordul expres" in rule["statement"]
    assert "acordul expres" in rule["exceptions"][0]
    assert "boala" in rule["context_evidence"][0]["quote"]
    assert_anchored(text, out["claims"])


def test_explicit_definitions_preserve_abbreviations_and_need_no_model():
    text = "+ Articolul 2 În înţelesul prezentului regulament, au următoarele semnificatii: . C.N.V.M. - Comisia Naţionala a Valorilor Mobiliare; . OSP - Oficiul de supraveghere a pieţelor;"
    p = StubProvider(default="must not be called")
    out = extract_rules(p, text)
    assert not p.calls and out["model_calls"] == 0
    assert [(r["subject"], r["object"]) for r in out["claims"]] == [
        ("C.N.V.M.", "Comisia Naţionala a Valorilor Mobiliare"), ("OSP", "Oficiul de supraveghere a pieţelor")]
    assert all("prezentului regulament" in r["scope"][0] for r in out["claims"])
    assert_anchored(text, out["claims"])


def test_inline_definition_retains_calculation_context():
    text = "+ Articolul 1 (1) Posturile vor asigura cel puţin 40% din producţie. (2) Prin opere locale se înţelege operele realizate în România."
    out = extract_rules(labels(u0=("obligation", 1, 1)), text)
    definition = next(r for r in out["claims"] if r["kind"] == "definition")
    assert definition["subject"] == "opere locale"
    assert "40%" in definition["context_evidence"][0]["quote"]
    assert_anchored(text, out["claims"])


def test_mixed_nested_definitions_are_disjoint_and_last_entry_is_preserved():
    text = ("+ Articolul 2 Termenii au următoarele semnificații: . OSP - Oficiul piețelor; "
            ". administrarea riscului - Controlul riscurilor. Riscurile sunt: "
            ". riscul creditului - Neplata obligațiilor; . riscul de lichiditate - Lipsa fondurilor. "
            "+ Articolul 3 Instituția funcționează.")
    units, definitions = source_units(text)
    assert [(d["subject"], d["object"]) for d in definitions] == [
        ("OSP", "Oficiul piețelor"), ("administrarea riscului", "Controlul riscurilor."),
        ("riscul creditului", "Neplata obligațiilor"), ("riscul de lichiditate", "Lipsa fondurilor.")]
    for definition in definitions:
        span = definition["evidence"][0]
        assert all(u["evidence"][0]["end"] <= span["start"] or
                   u["evidence"][0]["start"] >= span["end"] for u in units)
    assert any("Instituția funcționează" in u["statement"] for u in units)
    assert_anchored(text, definitions)


def test_uppercase_article_marker_and_flattened_paragraph_keep_their_scope():
    text = "+ Articolul 6 Firma există. ART.7 (1) Instituția - Bugetul de stat (2) Literele D și F se modifică."
    units, _ = source_units(text)
    assert len(units) == 3
    assert units[-1]["statement"] == "(2) Literele D și F se modifică."
    assert units[-1]["provision"] == "ART.7"
    assert_anchored(text, units)


def test_does_not_split_statutory_citation_parentheses_or_decimal_numbers():
    text = "Potrivit art. 26 alin. (2), Banca aplică limita de 3,562. Firma trebuie să plătească."
    units, _ = source_units(text)
    assert len(units) == 2
    assert "alin. (2)" in units[0]["statement"] and "3,562" in units[0]["statement"]


def test_repeated_replacement_heading_has_local_offsets_and_parent_context():
    text = "+ Articolul 1 Articolul 1 din Legea nr. 2/1992 se modifica astfel: + Articolul 1 Membrii Academiei primesc lunar o indemnizație."
    out = extract_rules(labels(u0=("amendment", -1, -1), u1=("fact", 0, 1)), text)
    right = next(r for r in out["claims"] if r["kind"] == "right")
    assert right["subject"] == "Membrii Academiei"
    assert right["provision_evidence"]["start"] == text.rindex("+ Articolul 1")
    assert any("Legea nr. 2/1992" in e["quote"] for e in right["context_evidence"])
    assert right["scope_resolution"] == "unresolved"
    assert_anchored(text, out["claims"])


def test_bad_subject_annotation_cannot_remove_or_rewrite_the_rule():
    text = "În împrejurări speciale, beneficiarul poate solicita sprijin."
    out = extract_rules(labels(u0=("permission", 1000, 1001)), text)
    assert out["claims"][0]["subject"] == ""
    assert out["claims"][0]["statement"] == text
    assert "subject unresolved" in out["claims"][0]["review_issues"]


def test_obvious_circumstance_is_not_returned_as_an_actor():
    text = "La înființare, instituția poate depune cererea."
    out = extract_rules(labels(u0=("permission", 0, 1)), text)
    assert out["claims"][0]["subject"] == ""


def test_all_units_must_be_accounted_for():
    with pytest.raises(ValueError, match="every supplied"):
        extract_rules(labels(u0=("fact", 0, 0)), "Banca funcționează. Firma există.")


def test_does_not_turn_absence_of_duty_into_positive_duty():
    out = extract_rules(labels(u0=("fact", 0, 0)), "Firma nu este obligată să depună cererea.")
    assert out["claims"][0]["kind"] == "fact"
    assert out["claims"][0]["subject"] == "Firma"


def test_negative_future_description_does_not_force_a_prohibition():
    claim = extract_rules(labels(u0=("fact", 0, 0)), "Datele nu vor exista în sistem după migrare.")["claims"][0]
    assert claim["kind"] == "fact"


@pytest.mark.parametrize("text,first,last", [
    ("(1) Se interzice amplasarea magazinelor.", 0, 1),
    ("Din producția europeană cel puțin 40% va fi producție locală.", 0, 2),
    ("Din producția europeană cel puțin 40% va fi producție locală.", 6, 9),
    ("În exercitarea atribuțiilor personalul are dreptul să solicite documentele.", 5, 5),
])
def test_model_fragments_stay_unresolved(text, first, last):
    claim = extract_rules(labels(u0=("fact", first, last)), text)["claims"][0]
    assert claim["subject"] == ""
    assert claim["statement"] == text
    assert "subject unresolved" in claim["review_issues"]


def test_mixed_right_and_duty_keep_shared_sentence_context():
    text = "Autoritatea are dreptul să solicite, iar firmele au obligația să prezinte documentele."
    out = extract_rules(labels(u0=("right", 0, 0), u1=("obligation", 1, 1)), text)
    assert [(r["kind"], r["subject"]) for r in out["claims"]] == [("right", "Autoritatea"), ("obligation", "firmele")]
    assert all(text in r["context_evidence"][0]["quote"] for r in out["claims"])


def test_paragraph_number_is_not_part_of_subject():
    text = "(3^1) Contribuabilii, persoane juridice, sunt obligaţi să transmită datele."
    out = extract_rules(labels(u0=("obligation", 0, 0)), text)
    assert out["claims"][0]["subject"] == "Contribuabilii, persoane juridice"


def test_public_application_uses_statement_extractor_and_preserves_native_graph(tmp_path):
    file = tmp_path / "act.json"
    body = "+ Articolul 1 Membrii Academiei primesc lunar o indemnizație."
    file.write_text(json.dumps({"id": "act", "titlu": "LEGE nr. 2/1992", "text_complet": body}), encoding="utf-8")
    p = labels(u0=("fact", 0, 1))
    app = GrapApplication(tmp_path / "workspace", role="operator", provider_factory=lambda **_: p)
    assert app.extract_legal_claims(str(file))["counts"]["ready"] == 1
    right = app.get_legal_claims(kind="right")["claims"][0]
    assert right["statement"] == body.removeprefix("+ Articolul 1 ")
    receipt = app.get_legal_claims(revision_id=right["revision_id"])
    assert receipt["source_text"] == body
    snapshot = app.export_legal_claim_graph()
    assert snapshot["status"] == "ok" and snapshot["claims"] == 1
    app = GrapApplication(tmp_path / "workspace", role="operator", provider_factory=lambda **_: p)
    assert app.extract_legal_claims(str(file))["counts"]["unchanged"] == 1
    assert len(p.calls) == 1


def test_failed_old_pipeline_is_retried_after_upgrade(tmp_path):
    file = tmp_path / "act.json"
    file.write_text(json.dumps({"id": "act", "titlu": "Test", "text_complet": "Banca trebuie să plătească."}), encoding="utf-8")
    def old(*_):
        raise ValueError("old extractor failed")
    old.prompt_version = "old"
    with LegalClaimStore(tmp_path / "ledger.sqlite", extractor=old) as store:
        assert store.ingest_folder(file, StubProvider())["counts"]["failed"] == 1
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        assert store.ingest_folder(file, labels(u0=("obligation", 0, 0)))["counts"]["ready"] == 1


def test_rust_search_preserves_source_text_even_when_model_extracts_no_claims(tmp_path):
    import pygrap

    file = tmp_path / "act.json"
    body = "Documentația pentru recapitalizare include certificatul."
    file.write_text(json.dumps({"id": "act", "titlu": "Norme", "text_complet": body}), encoding="utf-8")
    app = GrapApplication(tmp_path / "workspace", role="operator",
                          provider_factory=lambda **_: labels(u0=("other", -1, -1)))
    assert app.extract_legal_claims(str(file))["counts"]["ready"] == 1
    assert app.get_legal_claims()["total"] == 0
    snapshot = app.export_legal_claim_graph()
    assert snapshot["sources"] == 1 and snapshot["claims"] == 0
    graph = pygrap.IndexedKnowledgeGraph.with_merge_policy(snapshot["graph_path"], 2)
    assert graph.node_count() == 1 and graph.edge_count() == 0
    hits = graph.search_and_traverse("recapitalizare", max_seeds=5, required_properties=[],
        min_strength=0., max_depth=0, max_results=5, text_weight=1., graph_weight=0.)
    assert hits
    manifest = json.loads((Path(snapshot["graph_path"]).parent / "legal-claims.json").read_text(encoding="utf-8"))
    source = next(iter(manifest["revisions"].values()))
    assert source["source_text"] == body and "source_node_id" in source


def test_definition_resolution_is_scoped_and_conflicts_are_not_guessed(tmp_path):
    folder = tmp_path / "sources"
    folder.mkdir()
    for identity, tail in (("one", "OSP - Oficiul piețelor;"), ("two", "OSP - Oficiul serviciilor publice;"),
                           ("ambiguous", "OSP - Primul oficiu; OSP - Al doilea oficiu;"),
                           ("dotted", "C.N.V.M. - Comisia Valorilor Mobiliare;")):
        (folder / f"{identity}.json").write_text(json.dumps({"id": identity, "titlu": "Regulament",
            "text_complet": "În acest regulament, termenii au următoarele semnificații: " + tail}), encoding="utf-8")
    p = StubProvider(default="no calls expected")
    app = GrapApplication(tmp_path / "workspace", role="operator", provider_factory=lambda **_: p)
    assert app.extract_legal_claims(str(folder), max_files=4)["counts"]["ready"] == 4
    assert not p.calls
    by_name = {Path(s['source_file']).stem: s['source_id'] for s in app.get_legal_sources()['sources']}
    one = app.resolve_legal_term("OSP", by_name["one"])
    two = app.resolve_legal_term("OSP", by_name["two"])
    assert one["matches"][0]["object"] != two["matches"][0]["object"]
    assert one["resolution"] == two["resolution"] == "unique_definition"
    assert app.resolve_legal_term("OSP", by_name["ambiguous"])["resolution"] == "ambiguous"
    assert app.resolve_legal_term("cnvm", by_name["dotted"])["resolution"] == "unique_definition"
