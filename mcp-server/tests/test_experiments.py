import os
import tempfile
import pygrap

from grap_mcp.experiments import experiment_summary, quality_metrics


def _ws():
    return pygrap.Workspace.open(os.path.join(tempfile.mkdtemp(), "ws"))


def _run(ws, run_id, kind, gaps):
    ws.start_run(run_id, kind=kind, scope="all")
    ws.complete_run(run_id, status="completed", gaps_found=gaps,
                    explanation_hashes=[], summary={"scope": "all"})


def test_experiment_summary_shape():
    assert experiment_summary("all", "my hypothesis") == {
        "scope": "all", "experiment": "true", "hypothesis": "my hypothesis"}
    assert "hypothesis" not in experiment_summary("all", None)


def test_quality_metrics_excludes_experiments():
    ws = _ws()
    _run(ws, "run-p1", "production", 1)
    _run(ws, "run-p2", "production", 3)
    _run(ws, "run-e1", "experiment", 99)
    m = quality_metrics(ws)
    assert m["production_runs"] == 2
    assert m["production_completed"] == 2
    assert m["last_production_run"] == "run-p2"
    assert m["latest_gaps_found"] == 3
    assert m["experiments_excluded"] == 1


def test_quality_metrics_empty_workspace():
    m = quality_metrics(_ws())
    assert m["production_runs"] == 0
    assert m["last_production_run"] is None
    assert m["experiments_excluded"] == 0
