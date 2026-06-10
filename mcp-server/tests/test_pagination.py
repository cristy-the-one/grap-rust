"""Tests for the pagination helper and for the five paginated MCP tools."""

import grap_mcp.pagination as pg


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
