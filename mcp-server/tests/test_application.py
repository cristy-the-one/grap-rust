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


def test_extraction_view_has_native_aliases_and_rejects_ephemeral_edits(tmp_path):
    from grap_mcp.kee.embeddings import HashingEmbedder
    from grap_mcp.kee.working_graph import WorkingGraph
    root = tmp_path / "extraction"
    with WorkingGraph(HashingEmbedder(), workdir=str(root), merge_threshold=1.1) as wg:
        for name in ("Removed", "Kept"):
            wg.upsert_fragment({"outcomes": [{"name": name}]}, name)
        wg.retract_chunk("Removed")
        wg.save()
    app = GrapApplication(tmp_path / "workspace", role="admin", embedder_factory=HashingEmbedder)
    assert app.open_extraction_graph(str(root))["status"] == "ok"
    assert app.resolve_entity("Kept")["node_ids"] == [0]
    assert app._state.kg.get_node_text(0) == "Kept"
    for out in (app.ingest_documents(str(tmp_path)), app.project_relations([]),
                app.register_alias("temporary", 0), app.compact_graph(), app.reindex_entities()):
        assert out["status"] == "error" and "derived extraction view" in out["message"]
    assert app.create_knowledge_graph(str(tmp_path / "evidence"))["status"] == "ok"
    assert app._state.extraction_workdir is None
    assert app.project_relations([])["status"] == "ok"
