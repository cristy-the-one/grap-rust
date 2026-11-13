"""Write SourceItem links into a graph as typed edges.

Readers only produce records. This is the step that stores those records:
one node per item, and one edge per link. A link whose target is not among
the items is kept, aimed at a placeholder node labelled ``unresolved``.
"""

from __future__ import annotations

import json

from grap_mcp.readers import SourceItem


def write_format_links(items: list[SourceItem], kg) -> None:
    """Write `items` and their format links onto `kg`.

    Each item becomes a node whose label is its `item_id`. Each link becomes
    a directed edge to the item with that `target_id`, or, when no item has
    that id, to one placeholder node shared by every link that misses the
    same id. The edge carries `relation_type` (the link's type), `origin`
    ``format``, the format's `role`, and `context` as a JSON list of item ids.
    The first item with a repeated `item_id` is the one a link resolves to;
    the later item is still written.
    """
    written: list[tuple[SourceItem, int]] = []
    by_item_id: dict[str, int] = {}
    for item in items:
        node_id = kg.add_node(text=item.text, label=item.item_id)
        written.append((item, node_id))
        by_item_id.setdefault(item.item_id, node_id)

    placeholders: dict[str, int] = {}
    for item, source in written:
        for link in item.links:
            target = by_item_id.get(link.target_id)
            if target is None:
                target = placeholders.get(link.target_id)
                if target is None:
                    target = kg.add_node(text=link.target_id, label="unresolved")
                    placeholders[link.target_id] = target
            kg.add_edge(source, target, 1.0, {
                "relation_type": link.type,
                "origin": "format",
                "role": link.role,
                "context": json.dumps(link.context, ensure_ascii=False),
            })
