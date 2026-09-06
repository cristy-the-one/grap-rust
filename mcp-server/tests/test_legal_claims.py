"""Legal proposal contracts through the extraction/store/application interfaces."""
import json

import pytest

from grap_mcp.application import GrapApplication
from grap_mcp.kee.legal_claims import LegalClaimStore, extract_claims
from grap_mcp.kee.llm import StubProvider, OpenAICompatProvider


TEXT = "Membrii Academiei primesc lunar o indemnizație. Plata se face până la 30 aprilie 2000."


def claim(**changes):
    return {"kind": "right", "subject": "Membrii Academiei", "predicate": "primesc",
            "object": "o indemnizație", "provision": "", "scope": [], "conditions": [],
            "exceptions": [], "dates": ["lunar"], "evidence": [TEXT], **changes}


def provider(*claims):
    return StubProvider(default=json.dumps({"claims": list(claims)}, ensure_ascii=False))


def source(path, text=TEXT, identity="same-id"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": identity, "titlu": "LEGE nr. 2 din 1 ianuarie 1992",
                               "text_complet": text, "in_vigoare": True}, ensure_ascii=False), encoding="utf-8")
    return path


def test_qualified_right_and_definition_survive_reopen_with_literal_receipts(tmp_path):
    file = source(tmp_path / "sources" / "a.json")
    db = tmp_path / "claims.sqlite"
    with LegalClaimStore(db) as store:
        assert store.ingest_folder(file, provider(claim()))["counts"]["ready"] == 1
    with LegalClaimStore(db) as store:
        row = store.read(kind="right")["claims"][0]
        assert row["dates"] == ["lunar"] and row["review_status"] == "proposed"
        assert store.read(kind="obligation")["total"] == 0
        receipt = store.receipt(row["revision_id"])
        assert receipt["source_text"] == TEXT and receipt["metadata"]["in_force"] is True
        for span in row["evidence"]:
            assert receipt["source_text"][span["start"]:span["end"]] == span["quote"]
        assert row["scope_resolution"] == "unresolved"
        # Resume performs zero model calls, including after reopening the database.
        resumed = provider()
        assert store.ingest_folder(file, resumed)["counts"]["unchanged"] == 1
        assert not resumed.calls


@pytest.mark.parametrize("bad", [
    {"kind": "requirement"}, {"conditions": "approval"}, {"subject": "fabricated"},
    {"evidence": ["not present"]}, {"evidence": []}, {"predicate": ""},
    {"provision": "Articolul 999"}, {"dates": ["today"]},
])
def test_invalid_item_rejects_whole_reply_including_valid_sibling(bad):
    with pytest.raises(ValueError):
        extract_claims(provider(claim(), claim(**bad)), TEXT)


def test_ambiguous_evidence_rejected_instead_of_guessing_article():
    text = "Articolul 1: Banca plătește. Articolul 1: Banca plătește."
    with pytest.raises(ValueError, match="ambiguous"):
        extract_claims(provider(claim(subject="Banca", predicate="plătește", object="",
                                      dates=[], evidence=["Banca plătește."])), text)


def test_conditions_exceptions_and_date_remain_attached_to_same_claim():
    text = "S.V.M. nu va primi ordine decât după verificarea dovezilor, cu excepția cazurilor aprobate, până la 30 aprilie 2000."
    out = extract_claims(provider(claim(kind="prohibition", subject="S.V.M.", predicate="nu va primi",
        object="ordine", conditions=["după verificarea dovezilor"], exceptions=["cu excepția cazurilor aprobate"],
        dates=["până la 30 aprilie 2000"], evidence=[text])), text)["claims"]
    assert len(out) == 1 and out[0]["conditions"] and out[0]["exceptions"] and out[0]["dates"]


def test_unique_provision_locator_has_separate_source_anchor():
    text = "Articolul 2. " + TEXT
    out = extract_claims(provider(claim(provision="Articolul 2")), text)["claims"][0]
    anchor = out["provision_evidence"]
    assert text[anchor["start"]:anchor["end"]] == "Articolul 2"
    assert out["scope_resolution"] == "unresolved"
    with pytest.raises(ValueError, match="provision locator"):
        extract_claims(provider(claim(provision="Articolul 2")), text + " Articolul 2")


def test_structured_output_scoped_to_legal_call(monkeypatch):
    p = OpenAICompatProvider(extra_body={"reasoning_effort": "none"})
    seen = []
    def complete(self, system, user):
        seen.append(self.extra_body)
        return '{"claims":[]}'
    monkeypatch.setattr(OpenAICompatProvider, "complete", complete)
    extract_claims(p, TEXT)
    assert seen[0]["response_format"]["json_schema"]["schema"]["required"] == ["claims"]
    assert p.extra_body == {"reasoning_effort": "none"}


def test_failed_refresh_retains_old_receipt_and_successful_empty_retracts(tmp_path):
    file = source(tmp_path / "a.json")
    with LegalClaimStore(tmp_path / "claims.sqlite") as store:
        store.ingest_folder(file, provider(claim()))
        original = store.read()["claims"][0]
        source(file, "Replaced body.")
        failure = store.ingest_folder(file, StubProvider(default='{"claims":['))
        assert failure["counts"]["failed"] == 1
        assert store.read()["claims"] == []
        retained = store.read(include_stale=True)["claims"][0]
        assert retained["claim_id"] == original["claim_id"] and retained["stale"]
        assert store.receipt(original["revision_id"])["source_text"] == TEXT
        assert store.ingest_folder(file, provider(), refresh=True)["counts"]["ready"] == 1
        assert store.read(include_stale=True)["total"] == 0
        history = store.source_history(original["source_id"])
        assert len(history["revisions"]) == 2
        assert store.receipt(original["revision_id"])["claims"][0]["kind"] == "right"
        store.ingest_folder(file, StubProvider(default="bad"), refresh=True)
        assert store.coverage()["sources_with_retained_claims"] == 0


def test_incomplete_provider_response_never_replaces_current_claims(tmp_path):
    file = source(tmp_path / "a.json")
    def incomplete(*_):
        raise RuntimeError("LLM completion is incomplete (finish_reason='length')")
    with LegalClaimStore(tmp_path / "claims.sqlite") as store:
        store.ingest_folder(file, provider(claim()))
        failed = store.ingest_folder(file, StubProvider(handler=incomplete), refresh=True)
        assert failed["counts"]["failed"] == 1
        assert store.read(include_stale=True)["total"] == 1
        assert store.sources()["sources"][0]["status"] == "failed"


def test_budget_and_resume_count_calls_not_unchanged_files_and_no_identity_merging(tmp_path):
    folder = tmp_path / "sources"
    source(folder / "a.json")
    source(folder / "b.json")  # Same downloaded ID/content still represents a separate source.
    source(folder / "c.json", "Act în curs de procesare.")
    source(folder / "d.json", "long body " * 500)
    (folder / "e.json").write_text("{invalid", encoding="utf-8")
    p = provider(claim())
    with LegalClaimStore(tmp_path / "claims.sqlite") as store:
        first = store.ingest_folder(folder, p, max_files=1, max_chars=500)
        assert first["counts"] == {"scanned": 5, "attempted": 1, "ready": 1,
                                    "unchanged": 0, "deferred": 3, "failed": 1, "missing": 0}
        second = store.ingest_folder(folder, p, max_files=1, max_chars=500)
        assert second["counts"]["attempted"] == 1 and second["counts"]["unchanged"] == 1
        rows = store.read()["claims"]
        assert len({r["source_id"] for r in rows}) == 2
        assert len({r["claim_id"] for r in rows}) == 2
        assert len(p.calls) == 2
        assert len(store.sources()["sources"]) == 5


def test_interruption_marks_pending_before_call_and_preserves_receipt(tmp_path):
    file = source(tmp_path / "a.json")
    def interrupt(*_):
        raise KeyboardInterrupt()
    with LegalClaimStore(tmp_path / "claims.sqlite") as store:
        store.ingest_folder(file, provider(claim()))
        with pytest.raises(KeyboardInterrupt):
            store.ingest_folder(file, StubProvider(handler=interrupt), refresh=True)
        assert store.sources()["sources"][0]["status"] == "pending"
        assert store.read(include_stale=True)["total"] == 1


def test_resume_does_not_spend_budget_repeating_unchanged_failure(tmp_path):
    folder = tmp_path / "sources"
    source(folder / "a.json")
    source(folder / "b.json")
    with LegalClaimStore(tmp_path / "claims.sqlite") as store:
        assert store.ingest_folder(folder, StubProvider(default="bad"), max_files=1)["counts"]["attempted"] == 1
        resumed = provider(claim())
        result = store.ingest_folder(folder, resumed, max_files=1)
        assert result["counts"]["ready"] == 1 and result["counts"]["failed"] == 1
        assert len(resumed.calls) == 1


def test_folder_absence_marks_stale_without_affecting_other_folders(tmp_path):
    folder = tmp_path / "sources"
    file = source(folder / "a.json")
    other = source(tmp_path / "unrelated" / "b.json")
    with LegalClaimStore(tmp_path / "claims.sqlite") as store:
        store.ingest_folder(file, provider(claim()))
        store.ingest_folder(other, provider(claim()))
        file.unlink()
        result = store.ingest_folder(folder, provider(), max_files=0)
        assert result["counts"]["missing"] == 1
        assert store.read()["total"] == 1
        assert store.read(include_stale=True)["total"] == 2


def test_application_interface_is_workspace_owned_and_registered(tmp_path):
    file = source(tmp_path / "a.json")
    app = GrapApplication(tmp_path / "workspace", role="operator", provider_factory=lambda **_: provider(claim()))
    assert app.extract_legal_claims(str(file))["counts"]["ready"] == 1
    row = app.get_legal_claims(kind="right")["claims"][0]
    assert app.get_legal_claims(revision_id=row["revision_id"])["source_text"] == TEXT
    assert app.get_legal_sources(source_id=row["source_id"])["revisions"]
    assert {"extract_legal_claims", "get_legal_claims", "get_legal_sources"} <= set(app.operation_names())
    other = GrapApplication(tmp_path / "other", role="operator")
    assert other.get_legal_claims()["total"] == 0
    reader = GrapApplication(tmp_path / "workspace", role="reader")
    assert reader.extract_legal_claims(str(file))["status"] == "error"
    assert app.get_legal_claims(kind="imagined")["status"] == "error"


def test_rust_snapshot_preserves_qualified_claim_nodes_and_reopens_search(tmp_path):
    import pygrap
    file = source(tmp_path / "a.json")
    with LegalClaimStore(tmp_path / "claims.sqlite") as store:
        store.ingest_folder(file, provider(claim()))
        target = tmp_path / "snapshot"
        result = store.export_graph(target)
        assert result["claims"] == 1 and result["sources"] == 1
        with pytest.raises(FileExistsError):
            store.export_graph(target)
        # A failed replacement excludes old claims from NEW snapshots, without
        # invalidating this explicitly dated, self-contained historical export.
        store.ingest_folder(file, StubProvider(default="bad"), refresh=True)
        assert store.export_graph(tmp_path / "later")["claims"] == 0
    graph = pygrap.IndexedKnowledgeGraph.with_merge_policy(result["graph_path"], 2)
    hits = graph.search_and_traverse("indemnizație", max_seeds=5, required_properties=[],
        min_strength=0., max_depth=0, max_results=5, text_weight=1., graph_weight=0.)
    assert hits and graph.node_count() == 2 and graph.edge_count() == 1
    data = json.loads((target / "legal-claims.json").read_text(encoding="utf-8"))
    saved = data["claims"][0]
    assert json.loads(graph.get_node_text(saved["node_id"]))["dates"] == ["lunar"]
    assert data["revisions"][saved["revision_id"]]["source_text"] == TEXT
