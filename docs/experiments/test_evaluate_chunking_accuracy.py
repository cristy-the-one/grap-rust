import json
from pathlib import Path

import pytest

from evaluate_chunking_accuracy import audit, prepare, run, source_chunks


def test_frozen_pairs_cover_same_target_with_full_document_chunks(tmp_path):
    path = tmp_path / "fixture.json"
    prepare(path)
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert len(fixture["cases"]) == 7
    for case in fixture["cases"]:
        for arm, chunks in case["arms"].items():
            assert chunks[0]["start"] <= case["start"]
            assert chunks[-1]["end"] >= case["end"]
            assert all(c["start"] < case["end"] and c["end"] > case["start"] for c in chunks)
            assert all(c["text"] for c in chunks)
    conditional = fixture["cases"][0]
    assert len(conditional["arms"]["legacy"]) == 2
    assert len(conditional["arms"]["statements"]) == 1
    assert conditional["text"] in conditional["arms"]["statements"][0]["text"]


def test_repeated_text_offsets_are_monotonic():
    text = ("O propoziţie identică. " * 100).strip()
    for arm in ("legacy", "statements"):
        spans = source_chunks(text, arm)
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
        assert spans[0][0] == 0 and spans[-1][1] == len(text)


def test_resume_rejects_changed_prompt_before_model_call(tmp_path):
    fixture, output = tmp_path / "fixture.json", tmp_path / "output.json"
    prepare(fixture)
    output.write_text(json.dumps({"fixture_sha256": "wrong"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot resume"):
        run(fixture, output)


def test_saved_results_have_complete_pairs_and_literal_evidence():
    import hashlib
    root = Path(__file__).parent
    fixture_bytes = (root / "chunking-accuracy-cases-2026-09.json").read_bytes()
    result = json.loads((root / "chunking-accuracy-results-2026-09.json").read_text(encoding="utf-8"))
    fixture = json.loads(fixture_bytes)
    assert result["fixture_sha256"] == hashlib.sha256(fixture_bytes).hexdigest()
    chunks = {(c["id"], arm, z["id"]): z["text"] for c in fixture["cases"] for arm, zs in c["arms"].items() for z in zs}
    keys = [(r["case"], r["arm"], r["chunk"]) for r in result["rows"]]
    assert len(keys) == len(set(keys)) and set(keys) == set(chunks)
    for row, key in zip(result["rows"], keys):
        assert row["finish_reason"] == "stop" and row["parse_failure"] is None
        frag = row["fragment"]
        for claim in frag["outcomes"] + frag["relations"]:
            evidence = claim["evidence"]
            assert chunks[key][evidence["start"]:evidence["end"]] == evidence["quote"]


def test_audit_cannot_reuse_judgments_on_new_responses(tmp_path):
    root = Path(__file__).parent
    changed = tmp_path / "changed.json"
    data = json.loads((root / "chunking-accuracy-results-2026-09.json").read_text(encoding="utf-8"))
    data["rows"][0]["raw"] = "different reply"
    changed.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="different model results"):
        audit(root / "chunking-accuracy-cases-2026-09.json", changed,
              root / "chunking-accuracy-judgments-2026-09.json", tmp_path / "audit.json")
