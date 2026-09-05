from grap_mcp import reasoner
from tests.test_phase4 import _server, _adopt, _load_pg, ONTO_RV, PG_V1


def test_both_entry_points_use_frozen_scope_weights_and_save_explanations(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    _adopt(s, ONTO_RV)
    before = s._state.kg.node_count()
    _load_pg(s, tmp_path)
    assert s._state.kg.node_count() == before
    ws = s._get_workspace()
    v = ws.publish_pg(PG_V1, level="draft")
    ws.promote(v.hash, "test")
    ws.activate("a", "P", v.hash, weights={"lexical_threshold": 0.99})
    ws.set_production("a")
    seen = []
    detect = reasoner.detect_gaps

    def capture(kg, onto, pg, **kwargs):
        seen.append((pg.to_dict(), kwargs["weights"]))
        return detect(kg, onto, pg, **kwargs)

    monkeypatch.setattr(reasoner, "detect_gaps", capture)
    s._state.pg.steps[0].references = []  # unsaved edit must not replace activation
    a = s.run_procedure()
    b = s.detect_gaps()
    assert a["status"] == b["status"] == "ok"
    assert a["gaps"] == b["gaps"]
    assert seen[0] == seen[1]
    assert seen[0][1] == {"lexical_threshold": 0.99}
    assert seen[0][0]["steps"][0]["references"]
    for result in (a, b):
        run = ws.get_run(result["run_id"])
        assert len(run.explanation_hashes) == result["gaps_found"] > 0
        assert result["procedure_executed"] is False
        report = s.reconstruct_run(result["run_id"])
        assert report["artifacts_available"] is True
        assert report["replayable"] is False


def test_reference_patch_does_not_call_provider(tmp_path, monkeypatch):
    from tests.test_phase4 import ONTO_RV_QA
    s = _server(tmp_path, monkeypatch)
    _adopt(s, ONTO_RV_QA)
    _load_pg(s, tmp_path)

    def forbidden(**kwargs):
        raise AssertionError("Reference maintenance must not call an LLM")

    monkeypatch.setattr(s, "get_provider", forbidden)
    assert s.draft_pg_patch(model="legacy-argument")["status"] == "ok"
