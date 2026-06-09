# Tool Tiering + `grap_guide` + `suggest_queries` (P2-H) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a deployment shrink the agent-facing MCP tool surface (the ~50 tool schemas cost context on every turn) to a sensible subset via a `core | standard | full` tier, add a self-documenting `grap_guide` tool, and add `suggest_queries(node)` to propose next exploration steps.

**Architecture (low-churn, public-API only):** A `tiers.py` module computes the active tier once from `GRAP_TOOL_TIER` (default `full` → zero behavior change). In `server.py`, every `@mcp.tool()` becomes `@tiered`; `@tiered` looks up the function's tier in ONE central `_TIERS` policy dict (default `standard` for anything unlisted) and registers it via the public `mcp.tool()` **only if** within the active tier — so narrower tiers register fewer tools and FastMCP naturally advertises fewer schemas. No FastMCP internals are touched. The policy is one editable dict.

**Tech Stack:** Python (`grap_mcp`), `pytest`. No new deps.

**Decisions baked in (adjustable):**
- **Default tier = `full`** → opt-in narrowing (`GRAP_TOOL_TIER=core|standard|full`). Preserves current behavior and keeps the capabilities-coverage test green by default.
- **Policy:** only `core` and `full` tools are listed explicitly in `_TIERS`; everything else defaults to `standard`. So new tools are `standard` automatically.

**Scope discipline:** Tier is fixed at server start (env), not runtime-switchable (simpler; a runtime `set_tool_tier` would fight FastMCP's registration model — noted follow-up). `suggest_queries` returns templated next-step suggestions for a node (relation-count-aware enrichment is a follow-up).

---

## File Structure
- **Create** `mcp-server/grap_mcp/tiers.py` — `TIER_ORDER`, `active_tier()`, `tier_includes()`.
- **Modify** `mcp-server/grap_mcp/server.py` — `_TIERS` dict + `tiered` decorator; `@mcp.tool()` → `@tiered` everywhere; `grap_guide` + `suggest_queries` tools; `list_capabilities` reports tiers.
- **Tests** `mcp-server/tests/test_tiers.py`.

---

## Task 1: `tiers.py` — the tier model

**Files:** Create `mcp-server/grap_mcp/tiers.py`. Test: `mcp-server/tests/test_tiers.py`.

- [ ] **Step 1: Failing test** — `mcp-server/tests/test_tiers.py`:
```python
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
    assert t.active_tier() == "full"          # default: everything
    monkeypatch.setenv("GRAP_TOOL_TIER", "core")
    assert t.active_tier() == "core"
    monkeypatch.setenv("GRAP_TOOL_TIER", "bogus")
    assert t.active_tier() == "full"          # invalid → default
```

- [ ] **Step 2: Run, verify FAIL** — `cd mcp-server && python -m pytest tests/test_tiers.py -q` → module missing.

- [ ] **Step 3: Implement** `mcp-server/grap_mcp/tiers.py`:
```python
"""Tool-tier model: shrink the agent-facing MCP surface to core/standard/full.

The active tier is read once from GRAP_TOOL_TIER (default 'full' = every tool).
Tiers are cumulative: core ⊆ standard ⊆ full. A tool registers only if its tier
is included in the active tier.
"""
import os

TIER_ORDER = ("core", "standard", "full")
_RANK = {name: i for i, name in enumerate(TIER_ORDER)}
DEFAULT_TIER = "full"


def active_tier() -> str:
    """The tier this server run advertises. From GRAP_TOOL_TIER; invalid → default."""
    t = os.environ.get("GRAP_TOOL_TIER", DEFAULT_TIER)
    return t if t in _RANK else DEFAULT_TIER


def tier_includes(tool_tier: str, active: str) -> bool:
    """True if a tool tagged `tool_tier` should be visible under `active`.
    Cumulative: a 'core' tool shows in core/standard/full; a 'full' tool only in full."""
    return _RANK.get(tool_tier, _RANK["standard"]) <= _RANK.get(active, _RANK[DEFAULT_TIER])
```

- [ ] **Step 4: Run, verify PASS** — `cd mcp-server && python -m pytest tests/test_tiers.py -q`.

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/tiers.py mcp-server/tests/test_tiers.py
git commit -m "feat(mcp): tier model (core/standard/full; active from GRAP_TOOL_TIER, default full)"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: wire `@tiered` into `server.py`

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `test_tiers.py`.

- [ ] **Step 1: Failing test** — append to `test_tiers.py`:
```python
import asyncio
import grap_mcp.server as s

def test_full_tier_registers_everything():
    # Default (no env) = full: every defined tool is registered.
    names = {tool.name for tool in asyncio.run(s.mcp.list_tools())}
    # a sampling across tiers must all be present in full:
    for n in ["grap_guide", "suggest_queries", "graph_neighbors", "get_context_bundle",
              "run_experiment", "get_telemetry", "get_config", "list_capabilities"]:
        assert n in names, f"{n} missing from full-tier registration"

def test_tiers_map_is_self_consistent():
    # Every name in _TIERS is a real attribute of the server module.
    for name in s._TIERS:
        assert hasattr(s, name), f"_TIERS references unknown tool {name}"
```
(The core-tier-narrows-surface behavior is verified in Step 4 via a subprocess, since the tier is read at import.)

- [ ] **Step 2: Run, verify FAIL** — `cd mcp-server && python -m pytest tests/test_tiers.py -q` → `_TIERS`/`grap_guide`/`suggest_queries` missing (some fail now; grap_guide/suggest_queries come in Tasks 3-4 — that's fine, this task lands `_TIERS` + the decorator + the consistency test; the full-tier sampling test will fully pass after Tasks 3-4).

- [ ] **Step 3: Implement** in `server.py`:
   (a) After `mcp = FastMCP("Grap Engine")` (~line 34), add the policy + decorator:
```python
from .tiers import active_tier, tier_includes

# Tool-tier policy. Only core + full tools are listed; everything else is
# 'standard'. Tune freely — this dict is the single source of truth.
_TIERS = {
    # --- core: orient + build + retrieve + navigate ---
    "grap_guide": "core", "suggest_queries": "core", "list_capabilities": "core",
    "get_status": "core", "create_knowledge_graph": "core", "load_knowledge_graph": "core",
    "ingest_documents": "core", "graph_neighbors": "core", "get_context_bundle": "core",
    # --- full: power / governance / experimentation / audit-internals / legal-specific ---
    "run_experiment": "full", "get_quality_metrics": "full", "adopt_experiment": "full",
    "get_config": "full", "set_weight": "full", "set_retention": "full",
    "enforce_retention": "full", "get_audit_log": "full", "reconstruct_run": "full",
    "get_telemetry": "full", "build_legal_graph": "full", "legal_graph_from_relations": "full",
}
_ACTIVE_TIER = active_tier()


def tiered(func):
    """Register `func` as an MCP tool only if its tier (from _TIERS, default
    'standard') is included in the active tier. Out-of-tier tools remain defined
    on the module (callable in tests) but are not advertised to clients."""
    tool_tier = _TIERS.get(func.__name__, "standard")
    if tier_includes(tool_tier, _ACTIVE_TIER):
        return mcp.tool()(func)
    return func
```
   (b) Replace EVERY `@mcp.tool()` with `@tiered` throughout `server.py` (a global, mechanical replacement — they are all the bare `@mcp.tool()` form). Verify the count of replacements equals the count of `@mcp.tool()` occurrences before.
   (c) Update `list_capabilities` to report tiers — add to its returned dict:
```python
        "active_tier": _ACTIVE_TIER,
        "tier_of": {**{n: t_ for n, t_ in _TIERS.items()}},  # explicit non-standard tiers
        "tier_note": "Tools not listed in tier_of are 'standard'. Set GRAP_TOOL_TIER=core|standard|full.",
```
   (Keep the existing `domains` taxonomy; just add these keys. Add `grap_guide` and `suggest_queries` to an appropriate `domains` group — e.g. a new `"discovery": ["grap_guide", "suggest_queries", "list_capabilities"]` group, or put them in `audit`/`knowledge` — so the capabilities-coverage test still sees them once Tasks 3-4 register them. NOTE: the coverage test `test_capabilities_cover_all_registered_tools` compares the taxonomy to REGISTERED tools; in the default full tier all tools register, so the taxonomy must list ALL tools incl. grap_guide/suggest_queries. If you add the taxonomy entries here in Task 2 before the tools exist in Tasks 3-4, run the full suite only AFTER Tasks 3-4, or add the taxonomy entries in Task 4. Recommended: add `grap_guide`/`suggest_queries` to the taxonomy in Task 4 when they're registered, to keep each task's suite green.)

- [ ] **Step 4: Verify** —
   - `cd mcp-server && python -m pytest tests/test_tiers.py::test_tiers_map_is_self_consistent -q` passes (the full-tier sampling test may reference grap_guide/suggest_queries not-yet-added — run it after Task 4).
   - Full suite `python -m pytest -q`: must still pass in the DEFAULT (full) tier — every tool registered, `test_capabilities_cover_all_registered_tools` green (after Tasks 3-4 add the two new tools to the taxonomy).
   - Core-tier narrowing (subprocess, since tier is import-time) — add this test to `test_tiers.py`:
```python
import os, sys, subprocess

def test_core_tier_narrows_surface():
    script = (
        "import asyncio, grap_mcp.server as s;"
        "names={t.name for t in asyncio.run(s.mcp.list_tools())};"
        "print('grap_guide' in names, 'get_config' in names, len(names))"
    )
    env = {**os.environ, "GRAP_TOOL_TIER": "core"}
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env=env, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert out.returncode == 0, out.stderr
    guide_present, config_present, count = out.stdout.split()
    assert guide_present == "True"      # core tool present
    assert config_present == "False"    # full tool hidden
    assert int(count) < 20              # surface is much smaller than full
```
   (`cwd` should be the `mcp-server` dir so `grap_mcp` imports; adjust the path expression to resolve to `mcp-server`.)

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_tiers.py
git commit -m "feat(mcp): gate tool registration by tier (@tiered + _TIERS policy); report tiers in list_capabilities"
```

---

## Task 3: `grap_guide` tool (core)

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `test_tiers.py`.

- [ ] **Step 1: Failing test**
```python
def test_grap_guide(monkeypatch):
    out = s.grap_guide()
    assert out["status"] == "ok"
    assert out["active_tier"] in ("core", "standard", "full")
    assert "core" in out["tiers"] and isinstance(out["tiers"]["core"], list)
    assert "GRAP_TOOL_TIER" in out["how_to_widen"]
```

- [ ] **Step 2: Run, verify FAIL** — undefined.

- [ ] **Step 3: Implement** — add to `server.py` (decorated `@tiered`, tier `core` via `_TIERS`):
```python
@tiered
def grap_guide() -> dict:
    """Orientation for agents: what grap can do, the active tool tier, and how
    to widen it. Start here, then call list_capabilities for the full taxonomy."""
    core_tools = sorted(n for n, t_ in _TIERS.items() if t_ == "core")
    full_tools = sorted(n for n, t_ in _TIERS.items() if t_ == "full")
    return {
        "status": "ok",
        "active_tier": _ACTIVE_TIER,
        "overview": (
            "grap is a local graph engine with three graph kinds over one store: "
            "Knowledge (what we know), Procedure (how we do it), Temporal (what happened). "
            "Build a KG (create/load_knowledge_graph + ingest_documents), retrieve with "
            "graph_neighbors / get_context_bundle, analyse with blast_radius/find_cycles/"
            "kg_health, and run governed procedures (publish_pg → promote_pg → activate → "
            "run_procedure / detect_gaps) with a full audit trail."
        ),
        "tiers": {
            "core": core_tools,
            "standard": "default operator tools (everything not listed core/full)",
            "full": full_tools,
        },
        "how_to_widen": "Set the GRAP_TOOL_TIER env var to core | standard | full "
                        "(default full) before starting the server to control the tool surface.",
        "next": "Call list_capabilities for the full domain taxonomy, or "
                "suggest_queries(node_id) to explore a specific node.",
    }
```

- [ ] **Step 4: Verify** — `python -m pytest tests/test_tiers.py::test_grap_guide -q`.

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_tiers.py
git commit -m "feat(mcp): grap_guide — self-documenting orientation + tier discovery"
```

---

## Task 4: `suggest_queries` tool (core) + finalize taxonomy

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `test_tiers.py`.

- [ ] **Step 1: Failing test**
```python
class _GuideKG:
    def get_node_label(self, nid): return f"doc:{nid}"
    def get_node_text(self, nid): return "brake-by-wire redundancy requirement text"

def test_suggest_queries(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _GuideKG())
    out = s.suggest_queries(5)
    assert out["status"] == "ok" and out["node"] == 5
    tools = {sug["tool"] for sug in out["suggestions"]}
    assert "graph_neighbors" in tools and "get_blast_radius" in tools and "get_context_bundle" in tools
    # every suggestion references node 5 or its label
    assert all("args" in sug and "why" in sug for sug in out["suggestions"])
```

- [ ] **Step 2: Run, verify FAIL** — undefined.

- [ ] **Step 3: Implement** — add to `server.py` (`@tiered`, core):
```python
@tiered
def suggest_queries(node_id: int) -> dict:
    """Propose concrete next tool calls for exploring `node_id` — neighbor
    expansion, impact (blast radius), and related-context retrieval. A
    navigation aid for agents."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.get("kg")
    if not kg:
        return {"status": "error", "message": "no Knowledge Graph loaded"}
    label = None
    snippet = ""
    try:
        label = kg.get_node_label(node_id)
    except Exception:
        pass
    try:
        snippet = (kg.get_node_text(node_id) or "")[:60]
    except Exception:
        pass
    query_text = label or snippet or str(node_id)
    suggestions = [
        {"tool": "graph_neighbors", "args": {"node_id": node_id},
         "why": "Expand this node's direct neighbors (cites/mentions)."},
        {"tool": "get_blast_radius", "args": {"node_id": node_id},
         "why": "See everything that transitively depends on this node."},
        {"tool": "get_context_bundle", "args": {"query": query_text, "token_budget": 1500},
         "why": "Retrieve a budget-packed bundle of related KG context."},
    ]
    return {"status": "ok", "node": node_id, "label": label, "suggestions": suggestions}
```
   Then **finalize the taxonomy**: add `grap_guide` and `suggest_queries` to `list_capabilities`'s `domains` (e.g. a new `"discovery": ["grap_guide", "suggest_queries", "list_capabilities"]` entry, and REMOVE `list_capabilities` from `audit` if you move it, to avoid double-listing — the coverage test wants each registered tool listed exactly once / as a set). Ensure the set of all names across `domains` equals the set of registered tools in full tier.

- [ ] **Step 4: Verify** —
   - `cd mcp-server && python -m pytest tests/test_tiers.py -q` (all tier tests incl. the full-tier sampling + core-tier subprocess).
   - `cd mcp-server && python -m pytest -q` FULL suite — `test_capabilities_cover_all_registered_tools` MUST pass (taxonomy == registered tools in full tier).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_tiers.py
git commit -m "feat(mcp): suggest_queries (node exploration aid); add discovery tools to taxonomy"
```
(Do NOT add out/, terminals/, mcps/, *.patch, *.png, *.pdf, CLAUDE.md, untracked docs/*.md.)

---

## Self-Review
- **Coverage:** tier model (T1), registration gating + tier reporting (T2), grap_guide (T3), suggest_queries + taxonomy (T4).
- **Type consistency:** `active_tier()->str`, `tier_includes(tool_tier, active)->bool`, `_TIERS: dict[str,str]`, `tiered(func)->func` consistent across tasks; `grap_guide`/`suggest_queries` return `{status:"ok", ...}` like other tools.
- **No breakage:** default tier `full` registers every tool (identical to today); out-of-tier tools remain defined (so direct-call tests work); capabilities coverage holds in full tier.
- **Honest scope:** tier fixed at import (env), not runtime; `suggest_queries` is templated (relation-count enrichment deferred); a runtime `set_tool_tier` is a noted follow-up.
