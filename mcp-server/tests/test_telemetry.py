import grap_mcp.telemetry as tel
import grap_mcp.server as s


def test_records_and_aggregates(monkeypatch):
    monkeypatch.setenv("GRAP_TELEMETRY", "1")
    tel.reset()
    tel.record("graph_neighbors", tokens_saved=120)
    tel.record("graph_neighbors", tokens_saved=80)
    tel.record("query_events", tokens_saved=0)
    snap = tel.snapshot()
    assert snap["total_tokens_saved"] == 200
    assert snap["total_calls"] == 3
    assert snap["by_tool"]["graph_neighbors"]["calls"] == 2
    assert snap["by_tool"]["graph_neighbors"]["tokens_saved"] == 200


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GRAP_TELEMETRY", raising=False)
    tel.reset()
    tel.record("graph_neighbors", tokens_saved=999)
    snap = tel.snapshot()
    assert snap["enabled"] is False
    assert snap["total_tokens_saved"] == 0


def test_get_telemetry_tool(monkeypatch):
    monkeypatch.setenv("GRAP_TELEMETRY", "1")
    monkeypatch.setitem(s._state, "role", "operator")
    tel.reset()
    tel.record("graph_neighbors", tokens_saved=50)
    out = s.get_telemetry()
    assert out["status"] == "ok"
    assert out["total_tokens_saved"] == 50
    assert out["total_calls"] == 1
