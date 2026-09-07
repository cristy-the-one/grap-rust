import json
from pathlib import Path

import pytest

from grap_mcp.application import GrapApplication
from grap_mcp.kee.llm import StubProvider


def setup(tmp_path, count=3, text="Normele nu se aplică băncilor."):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for i in range(count):
        (corpus / f"{i}.json").write_text(json.dumps({"id": str(i+1), "titlu": f"Act {i}", "text_complet": text}), encoding="utf-8")
    def reply(_, user):
        return json.dumps({"units": {key: {"kind": "fact", "subject": "Normele"}
                                    for key in json.loads(user.split("\n", 1)[1])}})
    provider = StubProvider(handler=reply)
    app = GrapApplication(tmp_path / "workspace", role="operator", provider_factory=lambda **_: provider)
    campaign = app.create_legal_review(str(corpus), sample_size=count, seed="fixed-test")
    assert campaign["status"] == "ok", campaign
    return app, provider, campaign


def process_first(app, campaign):
    cid, iid = campaign["campaign"]["campaign_id"], campaign["items"][0]["item_id"]
    detail = app.process_legal_review(cid, iid)
    assert detail["status"] == "ok" and detail["revision_id"], detail
    return cid, iid, detail


def judgment(detail, value="correct"):
    return [{"claim_id": c["claim_id"], "kind": value, "subject": value, "qualifications": value, "note": "Checked against source"}
            for c in detail["receipt"]["claims"]]


def test_sampling_is_reproducible_and_includes_failed_documents(tmp_path):
    app, _, first = setup(tmp_path)
    second = app.create_legal_review(str(tmp_path / "corpus"), sample_size=3, seed="fixed-test")
    assert [i["file"] for i in first["items"]] == [i["file"] for i in second["items"]]
    assert first["campaign"]["manifest_sha256"] == second["campaign"]["manifest_sha256"]
    (tmp_path / "corpus/broken.json").write_text("broken", encoding="utf-8")
    third = app.create_legal_review(str(tmp_path / "corpus"), sample_size=4)
    assert third["summary"]["population_documents"] == 4
    assert len(third["items"]) == 4 and sum(i["extraction_status"] == "blocked" for i in third["items"]) == 1


def test_frozen_source_and_receipt_survive_original_edits_and_restart(tmp_path):
    app, provider, campaign = setup(tmp_path)
    Path(campaign["items"][0]["original_path"]).write_text("changed", encoding="utf-8")
    cid, iid, detail = process_first(app, campaign)
    assert detail["source_text"] == "Normele nu se aplică băncilor."
    before = len(provider.calls)
    assert app.process_legal_review(cid, iid)["revision_id"] == detail["revision_id"]
    assert len(provider.calls) == before
    reopened = GrapApplication(tmp_path / "workspace", role="operator")
    assert reopened.get_legal_review(cid, iid)["receipt"] == detail["receipt"]


def test_review_is_versioned_and_does_not_rewrite_claims(tmp_path):
    app, _, campaign = setup(tmp_path)
    cid, iid, detail = process_first(app, campaign)
    rows = judgment(detail, "incorrect")
    rows[0].update(corrected_kind="permission", corrected_subject="Normele")
    saved = app.save_legal_review(cid, iid, detail["revision_id"], 0, "Reviewer", rows, [], True)
    assert saved["status"] == "ok" and saved["version"] == 1
    assert saved["receipt"] == detail["receipt"]
    stale = app.save_legal_review(cid, iid, detail["revision_id"], 0, "Another reviewer", [], [])
    assert stale["status"] == "error" and "another window" in stale["message"]
    assert app.save_legal_review(cid, iid, detail["revision_id"], 1, "Reviewer", judgment(detail), [], True)["status"] == "ok"
    export = app.get_legal_review(cid, export=True)
    assert len(export["items"][0]["history"]) == 2
    assert len(export["campaign"]["manifest"]) == 3
    assert export["summary"]["claim_judgments"]["joint"]["correct"] == 1
    assert export["summary"]["unprocessed_documents"] == 2


@pytest.mark.parametrize("problem", ["wrong_receipt", "foreign_claim", "invented_subject", "bad_span", "incomplete"])
def test_invalid_reviews_cannot_be_saved(tmp_path, problem):
    app, _, campaign = setup(tmp_path)
    cid, iid, detail = process_first(app, campaign)
    rows, omissions, rid = judgment(detail), [], detail["revision_id"]
    if problem == "wrong_receipt": rid = "wrong"
    if problem == "foreign_claim": rows[0]["claim_id"] = "wrong"
    if problem == "invented_subject": rows[0]["corrected_subject"] = "Invented"
    if problem == "bad_span": omissions = [{"quote": "Invented", "start": 0, "end": 8}]
    if problem == "incomplete": rows = []
    result = app.save_legal_review(cid, iid, rid, 0, "Reviewer", rows, omissions, True)
    assert result["status"] == "error", result
    assert app.get_legal_review(cid, iid)["version"] == 0


def test_uncertain_unreviewed_and_omissions_are_not_successes(tmp_path):
    app, _, campaign = setup(tmp_path)
    cid, iid, detail = process_first(app, campaign)
    assert app.get_legal_review(cid)["summary"]["claim_judgments"]["joint"]["unreviewed"] == 1
    omission = {"start": 0, "end": len(detail["source_text"]), "quote": detail["source_text"], "note": "Additional rule to discuss"}
    saved = app.save_legal_review(cid, iid, detail["revision_id"], 0, "Reviewer", judgment(detail, "uncertain"), [omission], True)
    assert saved["status"] == "ok"
    summary = app.get_legal_review(cid)["summary"]
    assert summary["claim_judgments"]["joint"]["correct"] == 0
    assert summary["claim_judgments"]["joint"]["uncertain"] == 1
    assert summary["reported_omissions"] == 1 and summary["completed_document_reviews"] == 1


def test_partial_document_cannot_be_reviewed_as_complete(tmp_path):
    app, _, campaign = setup(tmp_path, text=" ".join(f"+ Articolul {i} Normele nu se aplică băncilor." for i in range(14)))
    cid, iid = campaign["campaign"]["campaign_id"], campaign["items"][0]["item_id"]
    first = app.process_legal_review(cid, iid)
    assert first["status"] == "ok" and first["extraction_status"] == "partial"
    assert first["receipt"] is None
    assert app.get_legal_review(cid)["summary"]["extracted_documents"] == 0
    second = app.process_legal_review(cid, iid)
    assert second["revision_id"] and len(second["receipt"]["claims"]) == 14


def test_modified_snapshot_is_rejected_and_viewer_cannot_write(tmp_path):
    app, provider, campaign = setup(tmp_path)
    cid, iid = campaign["campaign"]["campaign_id"], campaign["items"][0]["item_id"]
    Path(campaign["items"][0]["snapshot"]).write_text("changed", encoding="utf-8")
    assert app.process_legal_review(cid, iid)["status"] == "error" and not provider.calls
    viewer = GrapApplication(tmp_path / "workspace", role="viewer")
    assert viewer.get_legal_review(cid)["status"] == "ok"
    assert viewer.process_legal_review(cid, iid)["status"] == "error"
    assert viewer.create_legal_review(str(tmp_path / "corpus"))["status"] == "error"


def test_campaign_prevents_mixed_extractor_versions_and_does_not_mutate_provider(tmp_path, monkeypatch):
    from grap_mcp.kee import legal_rules
    app, provider, campaign = setup(tmp_path)
    provider.retries, provider.timeout = 4, 120
    cid, iid, _ = process_first(app, campaign)
    assert provider.retries == 4 and provider.timeout == 120
    monkeypatch.setattr(legal_rules, "VERSION", "future-version")
    assert app.process_legal_review(cid, iid)["status"] == "ok"  # Pinned history remains readable.
    blocked = app.process_legal_review(cid, campaign["items"][1]["item_id"])
    assert blocked["status"] == "error" and "version changed" in blocked["message"]
