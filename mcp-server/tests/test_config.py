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
