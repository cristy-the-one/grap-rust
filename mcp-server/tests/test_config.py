from grap_mcp.config import default_config, load_config, save_config


def test_default_config_shape():
    cfg = default_config()
    assert cfg["weights"] == {}
    assert "production_days" in cfg["retention"]
    assert cfg["last_changed"] is None


def test_load_missing_returns_default(tmp_path):
    cfg = load_config(str(tmp_path))
    assert cfg == default_config()


def test_save_then_load_round_trips(tmp_path):
    cfg = default_config()
    cfg["weights"]["priority"] = 0.8
    cfg["last_changed"] = 123
    save_config(str(tmp_path), cfg)
    assert load_config(str(tmp_path)) == cfg


def test_corrupt_config_returns_default(tmp_path):
    (tmp_path / "config.json").write_text("{not valid json", encoding="utf-8")
    assert load_config(str(tmp_path)) == default_config()


def test_role_in_default_and_roundtrip(tmp_path):
    cfg = default_config()
    assert "role" in cfg
    assert cfg["role"] == "operator"
    cfg["role"] = "admin"
    save_config(str(tmp_path), cfg)
    loaded = load_config(str(tmp_path))
    assert loaded["role"] == "admin"


def test_get_role_reads_without_writing_config(tmp_path, monkeypatch):
    # Reading the role must never persist anything (write-on-read would turn a
    # one-off env override into permanent governed config).
    from grap_mcp.server import GrapServerState
    monkeypatch.delenv("GRAP_ROLE", raising=False)
    st = GrapServerState()
    st.ws_root = str(tmp_path)
    assert st.get_role() == "operator"
    assert not (tmp_path / "config.json").exists()


def test_get_role_env_override_is_process_scoped(tmp_path, monkeypatch):
    from grap_mcp.server import GrapServerState
    monkeypatch.setenv("GRAP_ROLE", "admin")
    st = GrapServerState()
    st.ws_root = str(tmp_path)
    assert st.get_role() == "admin"
    assert not (tmp_path / "config.json").exists()  # env role is never persisted


def test_list_proposed_ontologies_pagination_meta(monkeypatch):
    # Regression: an inner variable used to shadow the pagination meta dict,
    # spreading a node's metadata into the response instead of total/cursor.
    from grap_mcp.application import GrapApplication
    s = GrapApplication()

    class _Ev:
        def __init__(self, i):
            self.subject = f"hash{i}"
            self.ts = i

    class _WS:
        def query_events(self, event_type):
            assert event_type == "ontology_proposed"
            return [_Ev(i) for i in range(5)]

    monkeypatch.setattr(s, "_get_workspace", lambda: _WS())
    monkeypatch.setitem(s._state, "role", "operator")
    out = s.list_proposed_ontologies(limit=2)
    assert out["status"] == "ok"
    assert out["count"] == 2 and out["total"] == 5
    assert out["next_cursor"] == "2"
    assert out["proposed"][0] == {"version_hash": "hash0", "ts": 0}


def test_grap_server_state_reset_and_role():
    # Cross-cut test for P2 robust state (minimal, no full ws)
    from grap_mcp.server import GrapServerState
    st = GrapServerState()
    st.role = "viewer"
    st.kg = "dummy"
    st.explanations = {"a": 1}
    assert st.role == "viewer"
    st.reset()
    assert st.kg is None
    assert st.explanations == {}
    assert st.role == "viewer"  # role not volatile
