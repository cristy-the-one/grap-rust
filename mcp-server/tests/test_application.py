import os
import subprocess
import sys

from grap_mcp.application import GrapApplication


def test_instances_own_workspace_state_and_history(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAP_WORKSPACE", str(tmp_path / "environment"))
    a = GrapApplication(tmp_path / "a", role="admin")
    b = GrapApplication(tmp_path / "b", role="operator")
    assert a.create_knowledge_graph(str(tmp_path / "kg-a"))["status"] == "ok"
    a._state.extraction_workdir = "a-only"
    a.session.record_query("private to a", 1)
    assert b._state.kg is None
    assert b._state.extraction_workdir is None
    assert b._state.ontology_kg is None
    assert b.get_session_context()["total_unique_queries"] == 0
    assert a._resolve_ws_root() != b._resolve_ws_root()
    assert os.environ["GRAP_WORKSPACE"] == str(tmp_path / "environment")
    a._get_workspace().record_event("only-a", "a")
    assert not b._get_workspace().query_events("only-a")


def test_application_import_has_no_mcp_transport():
    proc = subprocess.run([sys.executable, "-c",
        "from grap_mcp.application import GrapApplication; import sys; "
        "assert 'grap_mcp.server' not in sys.modules; "
        "assert 'mcp.server.fastmcp' not in sys.modules"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
