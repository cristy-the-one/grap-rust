"""Checkpoint behavior, with deterministic replies and no model server."""
import json
import sys

import pytest
import compare_local_models as compare


def test_resume_retains_results_and_retries_only_failed_request(tmp_path, monkeypatch):
    cases = tmp_path / "cases.json"
    config = tmp_path / "models.json"
    output = tmp_path / "results.json"
    cases.write_text(json.dumps([{"id": key, "text": key} for key in ("a", "b")]))
    config.write_text(json.dumps([{"model": "test"}]))
    args = ["compare", "--cases", str(cases), "--models-config", str(config), "--output", str(output)]
    calls = []

    def reply(provider, system, user):
        calls.append(user)
        if user.endswith("\n\nb"):
            raise OSError("interrupted server")
        return {"raw": '{"outcomes":[],"relations":[]}', "seconds": 0.1}

    monkeypatch.setattr(compare, "complete", reply)
    monkeypatch.setattr(sys, "argv", args)
    compare.main()
    saved = output.read_bytes()
    with pytest.raises(SystemExit):
        compare.main()  # accidental overwrite is rejected
    assert output.read_bytes() == saved

    calls.clear()

    def resumed(provider, system, user):
        calls.append(user)
        return {"raw": '{"outcomes":[],"relations":[]}', "seconds": 0.2}

    monkeypatch.setattr(compare, "complete", resumed)
    monkeypatch.setattr(sys, "argv", args + ["--resume"])
    compare.main()
    run = json.loads(output.read_text())["runs"][0]
    assert len(calls) == 2  # warmup + b, never a
    assert calls[-1].endswith("\n\nb")
    assert run["rows"][0]["seconds"] == 0.1
    assert len(run["rows"]) == 2 and len(run["failed_attempts"]) == 1
    assert run["summary"]["empty_correct"] is None
    calls.clear()
    compare.main()
    assert not calls  # fully complete resume does not even warm a model
    config.write_text(json.dumps([{"model": "different"}]))
    with pytest.raises(SystemExit):
        compare.main()
    assert not calls
