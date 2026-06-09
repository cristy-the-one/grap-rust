import asyncio
import grap_mcp.tiers as t
import grap_mcp.server as s


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


def test_tiers_map_is_self_consistent():
    for name in s._TIERS:
        # grap_guide/suggest_queries are added in later tasks; allow them now.
        if name in ("grap_guide", "suggest_queries"):
            continue
        assert hasattr(s, name), f"_TIERS references unknown tool {name}"

def test_full_tier_has_known_tools():
    names = {t.name for t in asyncio.run(s.mcp.list_tools())}
    for n in ["graph_neighbors", "get_context_bundle", "run_experiment", "get_config", "list_capabilities"]:
        assert n in names


def test_grap_guide():
    out = s.grap_guide()
    assert out["status"] == "ok"
    assert out["active_tier"] in ("core", "standard", "full")
    assert "core" in out["tiers"] and isinstance(out["tiers"]["core"], list)
    assert "GRAP_TOOL_TIER" in out["how_to_widen"]


class _GuideKG:
    def get_node_label(self, nid): return f"doc:{nid}"
    def get_node_text(self, nid): return "brake-by-wire redundancy requirement text"

def test_suggest_queries(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _GuideKG())
    out = s.suggest_queries(5)
    assert out["status"] == "ok" and out["node"] == 5
    tools = {sug["tool"] for sug in out["suggestions"]}
    assert {"graph_neighbors", "get_blast_radius", "get_context_bundle"} <= tools
    assert all("args" in sug and "why" in sug for sug in out["suggestions"])


import os, sys, subprocess

def test_core_tier_narrows_surface():
    mcp_server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../mcp-server
    script = ("import asyncio, grap_mcp.server as s;"
              "names={t.name for t in asyncio.run(s.mcp.list_tools())};"
              "print('grap_guide' in names, 'get_config' in names, len(names))")
    env = {**os.environ, "GRAP_TOOL_TIER": "core"}
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env=env, cwd=mcp_server_dir)
    assert out.returncode == 0, out.stderr
    guide, config, count = out.stdout.split()
    assert guide == "True"       # core tool present
    assert config == "False"     # full tool hidden in core tier
    assert int(count) < 20       # surface much smaller than full
