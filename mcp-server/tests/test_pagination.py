"""Tests for the pagination helper and for the five paginated MCP tools."""

import inspect

import grap_mcp.pagination as pg
from grap_mcp.application import GrapApplication
s = GrapApplication()


# ---------------------------------------------------------------------------
# Task 1 — helper tests
# ---------------------------------------------------------------------------

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


def test_cursor_past_end_and_nonnumeric_limit():
    items = list(range(10))
    # cursor beyond total -> empty page, no error, no further cursor
    page, meta = pg.paginate(items, limit=5, cursor="9999")
    assert page == [] and meta["total"] == 10 and meta["returned"] == 0
    assert meta["next_cursor"] is None
    # non-numeric limit -> falls back to the default cap (defensive)
    page2, _ = pg.paginate(list(range(250)), limit="oops", cursor=None)
    assert len(page2) == pg.DEFAULT_PAGE_LIMIT


# ---------------------------------------------------------------------------
# Task 2 — server-level tests for the five paginated tools
# ---------------------------------------------------------------------------

class _BigKG:
    """Fake KG returning 250-item lists for all analytics."""

    def blast_radius(self, node_id, relations=None, max_depth=10):
        return list(range(250))

    def find_cycles(self):
        return [[i, i + 1] for i in range(0, 250, 2)]

    def find_orphans(self, entry_points=None):
        return list(range(250))

    # graph_neighbors goes through the real tool; provide these methods
    def neighbors_by_relation(self, node_id, prop, ty):
        return [(i, 1.0) for i in range(150)] if ty == "cites" else []

    def predecessors_by_relation(self, node_id, prop, ty):
        return []

    def get_node_label(self, nid):
        return f"n{nid}"

    def get_node_text(self, nid):
        return f"text {nid}"


def test_blast_radius_paginates(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _BigKG())
    out = s.get_blast_radius(0)
    assert out["status"] == "ok"
    assert len(out["impacted"]) == 100
    assert out["total"] == 250
    assert out["next_cursor"] == "100"
    # second page starts at 100
    out2 = s.get_blast_radius(0, cursor=out["next_cursor"])
    assert out2["impacted"][0] == 100


def test_blast_radius_second_page(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _BigKG())
    out = s.get_blast_radius(0, cursor="100")
    assert out["impacted"][0] == 100 and out["impacted"][-1] == 199
    assert out["next_cursor"] == "200"


def test_graph_neighbors_paginates(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _BigKG())
    out = s.graph_neighbors(0, types=["cites"], limit=40)
    assert isinstance(out, dict)
    assert len(out["neighbors"]) == 40
    assert out["total"] == 150
    assert out["next_cursor"] == "40"
    # all returned edges must touch a paged neighbor
    paged_ids = {n["id"] for n in out["neighbors"]}
    assert all(
        e["source"] in paged_ids or e["target"] in paged_ids
        for e in out["edges"]
    )


def test_find_orphans_paginates(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _BigKG())
    out = s.find_orphans()
    assert out["status"] == "ok"
    assert len(out["orphans"]) == 100
    assert out["total"] == 250
    assert out["orphan_count"] == 250
    assert out["next_cursor"] == "100"


def test_find_cycles_paginates(monkeypatch):
    monkeypatch.setitem(s._state, "kg", _BigKG())
    out = s.find_cycles()
    assert out["status"] == "ok"
    assert len(out["cycles"]) == 100
    assert out["total"] == 125          # 250//2 = 125 cycles
    assert out["cycle_count"] == 125
    assert out["next_cursor"] == "100"


def test_query_events_has_limit_cursor_params():
    """At minimum, verify the new params are in the signature."""
    params = inspect.signature(s.query_events).parameters
    assert "limit" in params
    assert "cursor" in params


def test_query_events_paginates_small_list(monkeypatch):
    """Wire a tiny fake workspace to verify pagination meta passes through."""

    class _FakeEvent:
        def __init__(self, i):
            self.ts = i
            self.event_type = "test"
            self.subject = f"s{i}"
            self.links = []

    class _FakeWS:
        def query_events(self, event_type):
            return [_FakeEvent(i) for i in range(5)]

        def events_for_run(self, run_id):
            return []

    monkeypatch.setattr(s, "_get_workspace", lambda: _FakeWS())
    out = s.query_events(limit=3)
    assert len(out["events"]) == 3
    assert out["total"] == 5
    assert out["next_cursor"] == "3"
