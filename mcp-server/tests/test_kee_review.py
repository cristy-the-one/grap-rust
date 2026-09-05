"""The KEE human-review loop: propose -> list/get -> verify -> adopt.

Integration-style: real workspace + real KG in a tmp dir, exercising the
concept_hashes tamper-evidence gate added in adopt_proposed_ontology.
"""
import hashlib

PROPOSAL_TMPL = """\
version: kee-candidate
process_outcomes:
- id: requirement_verification
  name: {name}
  description: Requirement Verification (proposed by extraction).
  required_characteristics:
  - verification_method
mapping_hints:
- outcome: requirement_verification
  indicators:
  - keywords: [verification, method]
concept_hashes:
  requirement_verification: {chash}
"""


def _server(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("GRAP_ROLE", "operator")
    from grap_mcp.application import GrapApplication
    s = GrapApplication()
    assert s.create_knowledge_graph(str(tmp_path / "kg"))["status"] == "ok"
    return s


def _publish(s, yaml_text):
    return s._get_workspace().publish_ontology(
        yaml_text, metadata={"kind": "proposed", "source": "test"}).hash


def test_adopt_verifies_and_accepts_untampered_proposal(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    name = "Requirement Verification"
    good = PROPOSAL_TMPL.format(
        name=name, chash=hashlib.sha256(name.encode("utf-8")).hexdigest())
    vh = _publish(s, good)

    out = s.adopt_proposed_ontology(vh, rationale="reviewed; hashes verified")
    assert out["status"] == "ok"
    assert out["concept_hashes_checked"] == 1
    assert out["projection_verified"] is True
    assert out["outcomes_projected"] == 1


def test_adopt_rejects_tampered_proposal(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    # Hash recorded for the original name, but the yaml's name was edited
    # after extraction — adoption must refuse.
    original = "Requirement Verification"
    tampered = PROPOSAL_TMPL.format(
        name="Weakened Verification",
        chash=hashlib.sha256(original.encode("utf-8")).hexdigest())
    vh = _publish(s, tampered)

    out = s.adopt_proposed_ontology(vh, rationale="attempting adoption")
    assert out["status"] == "error"
    assert "verification failed" in out["message"]
    assert out["mismatches"] and out["mismatches"][0]["outcome"] == "requirement_verification"
    # the rejection is audited
    events = s._get_workspace().query_events("ontology_adoption_rejected")
    assert any(e.subject == vh for e in events)


def test_adopt_rejects_hash_for_unknown_outcome(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    name = "Requirement Verification"
    good = PROPOSAL_TMPL.format(
        name=name, chash=hashlib.sha256(name.encode("utf-8")).hexdigest())
    # extra hash entry pointing at a non-existent outcome id
    bad = good + "  ghost_outcome: " + "0" * 64 + "\n"
    vh = _publish(s, bad)

    out = s.adopt_proposed_ontology(vh, rationale="attempting adoption")
    assert out["status"] == "error"
    assert any(m.get("outcome") == "ghost_outcome" for m in out["mismatches"])


def test_export_hashes_survive_shorter_alias(tmp_path):
    # Law-ro pilot regression: _canonical_label prefers the SHORTEST alias, so
    # a concept that gains a shorter alias exported a name whose sha256 no
    # longer matched the stored node-creation hash — the integrity gate then
    # (correctly) refused adoption. Export must hash the exported name.
    from grap_mcp.governance import verify_concept_hashes
    from grap_mcp.kee.embeddings import HashingEmbedder
    from grap_mcp.kee.working_graph import WorkingGraph

    wg = WorkingGraph.open(str(tmp_path / "wd"), HashingEmbedder(dim=64))
    try:
        sig = wg.upsert_fragment(
            {"outcomes": [{"name": "Legea Asigurarilor Sociale de Sanatate",
                           "characteristics": ["contributii sociale"]}],
             "relations": []}, "doc#0")
        assert sig is not None
        node = next(iter(wg.canonical))
        wg._register_alias("Legea 145", node)          # shorter alias wins display
        onto = wg.to_candidate_ontology()
        assert onto["process_outcomes"][0]["name"] == "Legea 145"
        checked, mismatches = verify_concept_hashes(onto)
        assert checked == 1 and mismatches == []
    finally:
        wg.close()


def test_tokenize_folds_diacritics():
    # Law-ro pilot finding 10: the [^a-z0-9] tokenizer truncated words at
    # every Romanian diacritic ('referinţă' -> 'referin'), corrupting outcome
    # ids and lexical matching.
    from grap_mcp.kee.reconcile import fold, keywords, tokenize
    assert fold("referinţă") == "referinta"
    assert tokenize("Termenii de referinţă") == "termenii_de_referinta"
    assert tokenize("Legea asigurărilor sociale de sănătate") == \
        "legea_asigurarilor_sociale_de_sanatate"
    assert tokenize("înţelegere şi hotărâre") == "intelegere_si_hotarare"
    assert "sanatate" in keywords("asigurările de sănătate")


def test_covers_matches_across_diacritics():
    # Both sides of the coverage match fold: a transliterated characteristic
    # token must find its diacritic spelling in the evidence, and vice versa.
    from grap_mcp.reasoner import _covers
    evidence_ro = "Contribuţiile pentru asigurările sociale de sănătate sunt colectate."
    assert _covers(evidence_ro, "asigurarilor_de_sanatate") > 0.9
    assert _covers("plain ascii evidence about sanatate", "sănătate") == 1.0
    assert _covers(evidence_ro, "quantum_safety_audit") == 0.0


def test_covers_matches_numeric_tokens():
    # Law-ro pilot finding 11: evidence words were extracted with [a-z]+,
    # dropping digits, so date/percentage characteristics could never be
    # satisfied no matter how literal the evidence.
    from grap_mcp.reasoner import _covers
    ev = "Contractul-cadru va fi elaborat până la 15 februarie 1999."
    assert _covers(ev, "elaborat_pana_la_15_februarie_1999") == 1.0
    assert _covers("cota de 75% pentru medicamente", "cota_de_75") == 1.0
    # absent numbers still read as gaps
    assert _covers(ev, "elaborat_pana_la_30_iunie_2001") < 0.6


def test_duplicate_parked_relations_dedupe_and_save(tmp_path):
    # Law-ro pilot cycle 5 regression: a model emitting the same ungroundable
    # relation twice in one chunk produced duplicate parked rows, the store's
    # PRIMARY KEY aborted the save, and the whole extraction was lost.
    from grap_mcp.kee.embeddings import HashingEmbedder
    from grap_mcp.kee.working_graph import WorkingGraph

    wg = WorkingGraph.open(str(tmp_path / "wd"), HashingEmbedder(dim=64))
    try:
        rel = {"from": "Ghost A", "type": "reglementeaza", "to": "Ghost B"}
        wg.upsert_fragment({"outcomes": [{"name": "Concept Real",
                                          "characteristics": ["ceva"]}],
                            "relations": [rel, dict(rel)]}, "doc#0")
        assert len(wg.parked) == 1                    # deduped at park time
        wg.save()                                     # and the save survives
    finally:
        wg.close()
    wg2 = WorkingGraph.open(str(tmp_path / "wd"), HashingEmbedder(dim=64))
    try:
        assert len(wg2.parked) == 1
    finally:
        wg2.close()


def test_adopt_refuses_zero_outcome_document(tmp_path, monkeypatch):
    # An empty "ontology" (disputed report, malformed record) would pass hash
    # verification trivially, project nothing, and silently deactivate gap
    # detection. Both adoption and rollback must refuse it.
    s = _server(tmp_path, monkeypatch)
    vh = _publish(s, "agreed_outcomes: 0\noutcomes_only_in_a: [qb]\n")
    out = s.adopt_proposed_ontology(vh, rationale="oops, adopting the report")
    assert out["status"] == "error" and "zero process outcomes" in out["message"]
    rb = s.rollback_ontology(vh, reason="oops, rolling back to the report")
    assert rb["status"] == "error" and "zero process outcomes" in rb["message"]


def test_adopt_without_hashes_still_works(tmp_path, monkeypatch):
    # Legacy stateless extraction has no concept_hashes: nothing to verify.
    s = _server(tmp_path, monkeypatch)
    yaml_text = (
        "version: kee-candidate\n"
        "process_outcomes:\n"
        "- id: quality_assurance\n"
        "  name: Quality Assurance\n"
        "  description: QA (proposed).\n"
        "  required_characteristics: [quality_plan]\n"
        "mapping_hints: []\n")
    vh = _publish(s, yaml_text)
    out = s.adopt_proposed_ontology(vh, rationale="no hashes in legacy proposal")
    assert out["status"] == "ok"
    assert out["concept_hashes_checked"] == 0
