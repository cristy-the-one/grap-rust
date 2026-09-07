import json
import re

import pytest

from grap_mcp.application import GrapApplication
from grap_mcp.kee.legal_claims import LegalClaimStore
from grap_mcp.kee.llm import StubProvider


def write_source(file, suffix="", count=26):
    body = " ".join(f"+ Articolul {i} Instituția {i} trebuie să păstreze documentele{suffix}."
                    for i in range(count))
    file.write_text(json.dumps({"id": "act", "titlu": "Norme", "text_complet": body}), encoding="utf-8")
    return body


def reply(system, user):
    source = json.loads(user.split("\n", 1)[1])
    return json.dumps({"units": {key: {"kind": "obligation", "subject": " ".join(value.split()[:2])}
                       for key, value in source.items()}})


def provider(handler=reply, model="local"):
    p = StubProvider(handler=handler)
    p.model = model
    return p


def test_resume_after_reopening_publishes_only_complete_source(tmp_path):
    file = tmp_path / "act.json"
    body = write_source(file)
    p = provider()
    workspace = tmp_path / "workspace"
    app = GrapApplication(workspace, role="operator", provider_factory=lambda **_: p)
    first = app.extract_legal_claims(str(file), max_batches=1)
    assert first["counts"]["partial"] == 1 and first["batches"]["completed"] == 1
    assert app.get_legal_claims()["total"] == 0
    assert app.export_legal_claim_graph()["sources"] == 0
    source = app.get_legal_sources()["sources"][0]
    history = app.get_legal_sources(source["source_id"])
    assert history["work"]["completed_batches"] == 1 and history["work"]["total_batches"] == 3
    assert not history["revisions"]
    app = GrapApplication(workspace, role="operator", provider_factory=lambda **_: p)
    scan = app.extract_legal_claims(str(file), max_batches=0)
    assert scan["batches"]["attempted"] == 0 and len(p.calls) == 1
    assert app.get_legal_sources()["sources"][0]["status"] == "partial"
    final = app.extract_legal_claims(str(file), max_batches=2)
    assert final["counts"]["ready"] == 1
    assert final["batches"] == {"limit": 2, "attempted": 2, "completed": 2, "reused": 1}
    assert len(p.calls) == 3 and app.get_legal_claims()["total"] == 26
    claim = app.get_legal_claims()["claims"][0]
    receipt = app.get_legal_claims(revision_id=claim["revision_id"])
    assert receipt["completion"]["completed_batches"] == receipt["completion"]["total_batches"] == 3
    assert receipt["source_text"] == body and len(receipt["units"]) == 26
    for c in receipt["claims"]:
        for span in c["evidence"] + c["context_evidence"] + [c["provision_evidence"], c["subject_evidence"]]:
            assert body[span["start"]:span["end"]] == span["quote"]
    assert app.extract_legal_claims(str(file), max_batches=1)["counts"]["unchanged"] == 1
    assert len(p.calls) == 3


@pytest.mark.parametrize("interrupted", [False, True])
def test_failure_or_interruption_keeps_successful_batches(tmp_path, interrupted):
    file = tmp_path / "act.json"
    write_source(file)
    db = tmp_path / "ledger.sqlite"
    calls = 0
    def fail_second(system, user):
        nonlocal calls
        calls += 1
        if calls == 2:
            if interrupted:
                raise KeyboardInterrupt()
            return "malformed"
        return reply(system, user)
    with LegalClaimStore(db) as store:
        if interrupted:
            with pytest.raises(KeyboardInterrupt):
                store.ingest_folder(file, provider(fail_second), max_batches=3)
        else:
            assert store.ingest_folder(file, provider(fail_second), max_batches=3)["counts"]["failed"] == 1
        source_id = store.sources()["sources"][0]["source_id"]
        assert store.work_progress(source_id)["completed_batches"] == 1
    good = provider()
    with LegalClaimStore(db) as store:
        result = store.ingest_folder(file, good, max_batches=2)
        assert result["counts"]["ready"] == 1 and result["batches"]["reused"] == 1
        assert len(good.calls) == 2 and store.read()["total"] == 26


@pytest.mark.parametrize("change", ["source", "model", "config", "version", "refresh"])
def test_incompatible_partial_work_is_not_reused(tmp_path, change):
    file = tmp_path / "act.json"
    write_source(file)
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        store.ingest_folder(file, provider(), max_batches=1)
        old_work = store.sources()["sources"][0]["work_id"]
        p = provider(model="different" if change == "model" else "local")
        if change == "source":
            write_source(file, " noi")
        if change == "config":
            p.temperature = 0.5
        if change == "version":
            store.prompt_version = "future-version"
        result = store.ingest_folder(file, p, max_batches=1, refresh=change == "refresh")
        assert result["counts"]["partial"] == 1 and result["batches"]["reused"] == 0
        assert store.sources()["sources"][0]["work_id"] != old_work


def test_partial_refresh_keeps_old_receipt_out_of_current_reads(tmp_path):
    file = tmp_path / "act.json"
    write_source(file)
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        store.ingest_folder(file, provider())
        old = store.read()["claims"][0]["revision_id"]
        store.ingest_folder(file, provider(), max_batches=1, refresh=True)
        assert store.read()["total"] == 0 and store.read(include_stale=True)["total"] == 26
        assert len(store.receipt(old)["claims"]) == 26
        assert store.ingest_folder(file, provider(), max_batches=2)["counts"]["ready"] == 1
        assert store.read()["claims"][0]["revision_id"] != old


def test_global_batch_budget_does_not_cancel_first_partial_source(tmp_path):
    for name in ("a", "b"):
        write_source(tmp_path / f"{name}.json")
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        p = provider()
        result = store.ingest_folder(tmp_path, p, max_batches=1)
        assert result["counts"]["partial"] == 1 and result["counts"]["deferred"] == 1
        assert len(p.calls) == 1
        store.ingest_folder(tmp_path, p, max_files=0, max_batches=1)
        assert store.sources()["sources"][0]["status"] == "partial"


def test_folder_continues_past_failed_source_and_explicit_file_can_retry(tmp_path):
    for name in ("a", "b"):
        write_source(tmp_path / f"{name}.json", count=1)
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        failed = store.ingest_folder(tmp_path, provider(lambda *_: "bad"), max_files=1, max_batches=1)
        assert failed["counts"]["failed"] == 1
        good = provider()
        continued = store.ingest_folder(tmp_path, good, max_files=1, max_batches=1)
        assert continued["counts"]["failed"] == 1 and continued["counts"]["ready"] == 1
        assert len(good.calls) == 1
        assert store.ingest_folder(tmp_path / "a.json", good, max_batches=1)["counts"]["ready"] == 1


def test_source_change_during_batch_cannot_checkpoint_or_publish(tmp_path):
    file = tmp_path / "act.json"
    write_source(file)
    def changed(system, user):
        write_source(file, " noi")
        return reply(system, user)
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        result = store.ingest_folder(file, provider(changed), max_batches=1)
        assert result["counts"]["failed"] == 1 and result["batches"]["completed"] == 0
        source = store.sources()["sources"][0]
        assert not store.work_progress(source["source_id"])["matches_observed_source"]
        assert store.read()["total"] == 0


def test_superseded_same_source_worker_cannot_save_late_batch(tmp_path):
    file = tmp_path / "act.json"
    write_source(file)
    db = tmp_path / "ledger.sqlite"
    def overtaken(system, user):
        with LegalClaimStore(db) as newer:
            assert newer.ingest_folder(file, provider(), max_batches=1)["counts"]["partial"] == 1
        return reply(system, user)
    with LegalClaimStore(db) as store:
        result = store.ingest_folder(file, provider(overtaken), max_batches=1)
        assert result["counts"]["superseded"] == 1
        source = store.sources()["sources"][0]
        assert source["status"] == "partial"
        assert store.work_progress(source["source_id"])["completed_batches"] == 1


def test_definition_only_document_needs_no_labeling_calls(tmp_path):
    file = tmp_path / "act.json"
    file.write_text(json.dumps({"id": "act", "titlu": "Norme", "text_complet":
        "Termenii au următoarele semnificații: OSP - Oficiul piețelor;"}), encoding="utf-8")
    p = provider()
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        assert store.ingest_folder(file, p, max_batches=1)["counts"]["ready"] == 1
        assert not p.calls and store.read()["total"] == 1


def test_unsplittable_statement_fails_before_any_generation_with_location(tmp_path):
    file = tmp_path / "act.json"
    body = "+ Articolul 1 " + "cuvânt " * 1201
    file.write_text(json.dumps({"id": "act", "titlu": "Norme", "text_complet": body}), encoding="utf-8")
    p = provider()
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        result = store.ingest_folder(file, p, max_batches=3, max_chars=100000)
        assert result["counts"]["failed"] == 1 and not p.calls
        assert "characters 14:" in store.sources()["sources"][0]["error"]
        assert store.read()["total"] == 0


def test_long_unbroken_text_also_has_a_request_size_bound(tmp_path):
    file = tmp_path / "act.json"
    file.write_text(json.dumps({"id": "act", "titlu": "Norme", "text_complet": "x" * 13000}), encoding="utf-8")
    p = provider()
    with LegalClaimStore(tmp_path / "ledger.sqlite") as store:
        result = store.ingest_folder(file, p, max_batches=3, max_chars=100000)
        assert result["counts"]["failed"] == 1 and not p.calls
        assert "rendered bytes" in store.sources()["sources"][0]["error"]
