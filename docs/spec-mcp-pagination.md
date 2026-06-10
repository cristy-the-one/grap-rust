# MCP Pagination for list-returning tools (P1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (or executing-plans). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Stop large KG/PG/TG answers from blowing the agent's context. Add offset/opaque-cursor pagination to the unbounded list-returning read tools, with a protective default page size, so responses are bounded by default and the caller can page through the rest.

**Architecture:** A small reusable `grap_mcp/pagination.py::paginate(items, limit, cursor)` returns `(page, meta)` where `meta = {total, returned, next_cursor}`. `next_cursor` is an opaque string (a stringified offset); pass it back as `cursor` for the next page; `None` means no more. Each piloted tool gains optional `limit`/`cursor` params and merges the meta into its response. Underlying lists are already stably ordered (analytics are deterministic; neighbors are in insertion order; events are chronological), so offset paging is stable.

**Tech Stack:** Python (`grap_mcp`), `pytest`. No new deps. No Rust changes.

**Behavior change (intended, signaled):** results are capped at `DEFAULT_PAGE_LIMIT = 100` by default; the response always reports `total` and a `next_cursor` when truncated, so truncation is explicit and recoverable. Pass a larger `limit`, or page with `cursor`, for more. (Per-node neighbor sets, analytics over typical graphs, and the demo/ro-law/aspice-gui flows are well under 100, so they're unaffected in practice.)

**Scope:** `graph_neighbors`, `get_blast_radius`, `find_orphans`, `find_cycles`, `query_events`. `get_context_bundle` is left as-is (it already token-budgets). Routing paginated pages through the `#MUNCH` compact encoder for more tools is a natural follow-up, out of scope here.

---

## File Structure
- **Create** `mcp-server/grap_mcp/pagination.py` — `DEFAULT_PAGE_LIMIT`, `paginate`.
- **Modify** `mcp-server/grap_mcp/server.py` — add `limit`/`cursor` to the five tools; merge pagination meta.
- **Tests** `mcp-server/tests/test_pagination.py`.

---

## Task 1: `pagination.py` helper

**Files:** Create `mcp-server/grap_mcp/pagination.py`. Test: `mcp-server/tests/test_pagination.py`.

- [ ] **Step 1: Failing test** — `mcp-server/tests/test_pagination.py`:
```python
import grap_mcp.pagination as pg

def test_default_caps_and_reports_total():
    items = list(range(250))
    page, meta = pg.paginate(items, limit=None, cursor=None)
    assert page == list(range(100))                 # default cap
    assert meta["total"] == 250 and meta["returned"] == 100
    assert meta["next_cursor"] == "100"             # opaque offset string

def test_cursor_advances_to_next_page():
    items = list(range(250))
    page, meta = pg.paginate(items, limit=100, cursor="100")
    assert page == list(range(100, 200))
    assert meta["next_cursor"] == "200"

def test_last_page_has_no_next_cursor():
    items = list(range(250))
    page, meta = pg.paginate(items, limit=100, cursor="200")
    assert page == list(range(200, 250))
    assert meta["returned"] == 50 and meta["next_cursor"] is None

def test_explicit_limit_and_bad_cursor():
    items = list(range(10))
    page, meta = pg.paginate(items, limit=3, cursor=None)
    assert page == [0, 1, 2] and meta["next_cursor"] == "3"
    # garbage cursor -> treated as offset 0 (defensive)
    page2, meta2 = pg.paginate(items, limit=3, cursor="not-a-number")
    assert page2 == [0, 1, 2]
    # limit larger than remaining is fine
    page3, meta3 = pg.paginate(items, limit=1000, cursor=None)
    assert page3 == items and meta3["next_cursor"] is None
```

- [ ] **Step 2: Run, verify FAIL** — `cd mcp-server && python -m pytest tests/test_pagination.py -q` → module missing.

- [ ] **Step 3: Implement** `mcp-server/grap_mcp/pagination.py`:
```python
"""Offset/opaque-cursor pagination for list-returning MCP tools.

Keeps large KG/PG/TG answers from blowing the agent's context: results are
capped at DEFAULT_PAGE_LIMIT unless a larger `limit` is requested, and a
`next_cursor` (opaque offset string) lets the caller page through the rest.
The underlying lists must be stably ordered (they are: analytics are
deterministic, neighbors insertion-ordered, events chronological).
"""
from typing import Any, Optional

DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 1000


def paginate(items: list, limit: Optional[int] = None,
             cursor: Optional[str] = None) -> tuple[list, dict[str, Any]]:
    """Return (page, meta). `cursor` is an opaque offset string from a prior
    `next_cursor`; `meta` = {total, returned, next_cursor}. A bad/missing cursor
    starts at 0; `limit` is clamped to [1, MAX_PAGE_LIMIT] (default 100)."""
    total = len(items)
    start = 0
    if cursor:
        try:
            start = max(0, int(cursor))
        except (TypeError, ValueError):
            start = 0
    if limit is None:
        eff = DEFAULT_PAGE_LIMIT
    else:
        eff = max(1, min(int(limit), MAX_PAGE_LIMIT))
    end = min(total, start + eff)
    page = items[start:end]
    next_cursor = str(end) if end < total else None
    return page, {"total": total, "returned": len(page), "next_cursor": next_cursor}
```

- [ ] **Step 4: Run, verify PASS** — `cd mcp-server && python -m pytest tests/test_pagination.py -q`.

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/pagination.py mcp-server/tests/test_pagination.py
git commit -m "feat(mcp): offset/opaque-cursor pagination helper (default-capped, reports total + next_cursor)"
```
(trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

## Task 2: apply pagination to the five list tools

**Files:** Modify `mcp-server/grap_mcp/server.py`. Test: extend `mcp-server/tests/test_pagination.py`.

For EACH tool below, add `limit: int = None` and `cursor: str = None` params, paginate the primary list, and merge `meta` into the returned dict. Keep the existing role guards / KG guards / `@tiered` decorators / `wire_format` (graph_neighbors) intact.

- [ ] **Step 1: Failing tests** — append server-level tests (mirror `test_analytics.py`'s `_FakeKG`/monkeypatch). Cover at least:
```python
import grap_mcp.server as s

class _BigKG:
    def blast_radius(self, node_id, relations=None, max_depth=10): return list(range(250))
    def find_cycles(self): return [[i, i + 1] for i in range(0, 250, 2)]
    def find_orphans(self, entry_points=None): return list(range(250))
    # graph_neighbors goes through the real tool; provide neighbors_by_relation/predecessors
    def neighbors_by_relation(self, node_id, prop, ty):
        return [(i, 1.0) for i in range(150)] if ty == "cites" else []
    def predecessors_by_relation(self, node_id, prop, ty): return []
    def get_node_label(self, nid): return f"n{nid}"
    def get_node_text(self, nid): return f"text {nid}"

def test_blast_radius_paginates(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _BigKG())
    out = s.get_blast_radius(0)
    assert out["status"] == "ok"
    assert len(out["impacted"]) == 100 and out["total"] == 250 and out["next_cursor"] == "100"
    out2 = s.get_blast_radius(0, cursor=out["next_cursor"])
    assert out2["impacted"][0] == 100

def test_graph_neighbors_paginates(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _BigKG())
    out = s.graph_neighbors(0, types=["cites"], limit=40)
    assert len(out["neighbors"]) == 40 and out["total"] == 150 and out["next_cursor"] == "40"
    # edges are filtered to the returned neighbors
    paged_ids = {n["id"] for n in out["neighbors"]}
    assert all(e["source"] in paged_ids or e["target"] in paged_ids for e in out["edges"])
```
(Add similar quick asserts for `find_orphans` and `find_cycles`. For `query_events`, use the workspace path or a small fake; if wiring a workspace fake is heavy, at least assert the `limit`/`cursor` params exist via `inspect.signature` and that a small event list passes through with `total`.)

- [ ] **Step 2: Run, verify FAIL** — params/keys missing.

- [ ] **Step 3: Implement.** In `server.py`, `from grap_mcp.pagination import paginate`. Then:

**`get_blast_radius`** — add `limit`/`cursor`; paginate `impacted`:
```python
def get_blast_radius(node_id: int, relations: list = None, max_depth: int = 10,
                     limit: int = None, cursor: str = None) -> dict:
    ...
    impacted = kg.blast_radius(node_id, relations, max_depth)
    page, meta = paginate(impacted, limit, cursor)
    return {"status": "ok", "center": node_id, "impacted": page, **meta}
```

**`find_orphans`** — paginate `orphans`:
```python
    orphans = kg.find_orphans(entry_points)
    page, meta = paginate(orphans, limit, cursor)
    return {"status": "ok", "orphan_count": meta["total"], "orphans": page, **meta}
```
(Keep `orphan_count` = total for back-compat; `total` mirrors it.)

**`find_cycles`** — paginate the outer `cycles` list:
```python
    cycles = kg.find_cycles()
    page, meta = paginate(cycles, limit, cursor)
    return {"status": "ok", "cycle_count": meta["total"], "cycles": page, **meta}
```

**`query_events`** — paginate `events` (build the full list as today, then page):
```python
    events = [ ...as today... ]
    page, meta = paginate(events, limit, cursor)
    return {"events": page, **meta}
```

**`graph_neighbors`** — paginate `neighbors`, then filter `edges` to the paged neighbor ids; preserve the existing `wire_format` encoding (paginate BEFORE `encode_response`):
```python
def graph_neighbors(node_id: int, types: list = None, wire_format: str = "json",
                    limit: int = None, cursor: str = None) -> dict:
    ...
    # build full `neighbors` and `edges` exactly as today, then:
    page, meta = paginate(neighbors, limit, cursor)
    paged_ids = {n["id"] for n in page}
    edges_page = [e for e in edges if e["source"] in paged_ids or e["target"] in paged_ids]
    r = {"status": "ok", "center": node_id, "neighbors": page, "edges": edges_page, **meta}
    payload, _meta = encode_response("graph_neighbors", r, requested_format=wire_format)
    # ...session-journal hook + return payload, as today...
```
(Keep the existing `session.record_node_access(...)` hook — record the PAGED neighbor ids. Keep returning `payload` so the compact path still works; the encoder schema already lists `neighbors`/`edges` and ignores extra scalar keys, but verify `total`/`returned`/`next_cursor` don't break `encode_response` — they are top-level scalars the schema doesn't enumerate, so the JSON path returns them and the compact path simply omits unschema'd scalars. If the compact path errors on extra keys, leave the pagination meta only on the JSON path / add them to the `graph_neighbors` schema's `scalars`.)

Confirm the `@tiered`/`_require`/no-KG guards stay. The new params are optional (default None) → MCP schema gains optional params, additive.

- [ ] **Step 4: Verify** — `cd mcp-server && python -m pytest tests/test_pagination.py -q`; then full `python -m pytest -q` (existing `test_analytics.py`/`test_graph_neighbors_wire.py`/`test_session.py` must still pass — they use small fakes well under 100, so results are unchanged; the added top-level keys are additive). If `test_graph_neighbors_wire.py`'s compact assertion breaks because of the extra scalar keys, add `total`/`returned`/`next_cursor` to the `graph_neighbors` schema `scalars` in `encoding/schemas.py` (or confirm the encoder ignores unschema'd scalars).

- [ ] **Step 5: Commit**
```
git add mcp-server/grap_mcp/server.py mcp-server/tests/test_pagination.py mcp-server/grap_mcp/encoding/schemas.py
git commit -m "feat(mcp): paginate graph_neighbors/get_blast_radius/find_orphans/find_cycles/query_events"
```
(Do NOT add out/, terminals/, mcps/, *.patch, *.png, *.pdf, CLAUDE.md, untracked docs/*.md.)

---

## Self-Review
- **Coverage:** helper (T1), five tools paginated (T2). `get_context_bundle` intentionally excluded (already token-budgeted).
- **Type consistency:** `paginate(items, limit, cursor) -> (page, {total, returned, next_cursor})` used uniformly; `next_cursor` opaque str / None; cursor round-trips.
- **Back-compat:** `orphan_count`/`cycle_count` preserved (== total); new params optional; new response keys additive. Default cap (100) is the intended protective behavior, signaled via `total`/`next_cursor`.
- **Edge filtering:** `graph_neighbors` edges restricted to the paged neighbors so a page is internally consistent.
- **Follow-up:** route paginated pages through the `#MUNCH` compact encoder for more tools (compose pagination + compaction).
