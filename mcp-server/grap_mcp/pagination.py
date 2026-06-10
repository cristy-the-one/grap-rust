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
