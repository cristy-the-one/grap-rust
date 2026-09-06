import json

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
