import grap_mcp.tiers as t

def test_tier_includes_is_cumulative():
    assert t.tier_includes("core", "core")
    assert t.tier_includes("core", "standard")
    assert t.tier_includes("core", "full")
    assert not t.tier_includes("standard", "core")
    assert not t.tier_includes("full", "standard")
    assert t.tier_includes("full", "full")

def test_active_tier_default_and_env(monkeypatch):
    monkeypatch.delenv("GRAP_TOOL_TIER", raising=False)
    assert t.active_tier() == "full"
    monkeypatch.setenv("GRAP_TOOL_TIER", "core")
    assert t.active_tier() == "core"
    monkeypatch.setenv("GRAP_TOOL_TIER", "bogus")
    assert t.active_tier() == "full"
