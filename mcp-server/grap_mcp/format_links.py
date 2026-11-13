"""Write SourceItem links into a graph as typed edges.

Schema §6: the relation is the property name, value true. `origin` and
`role` are the other engine properties. `context` stays out of the engine
and is returned with the counts.
"""

from __future__ import annotations

from grap_mcp.readers import SourceItem


def write_format_links(items: list[SourceItem], kg) -> dict:
    """Write `items` and their format links onto `kg`.

    Each item becomes a node labelled with its `kind_hint`. Items are keyed
    by `(source_id, item_id)`; a repeated key keeps every node, and a link
    resolves to the first. A target missing from that source becomes one
    placeholder node labelled ``unresolved``, shared by links in that source
    that miss the same id. The edge is still written.

    Returns `items`, `links`, and `unresolved_targets`, plus `context`: one
    record per link, in write order, holding the context the engine does not.
    """
    written: list[tuple[SourceItem, int]] = []
    by_key: dict[tuple[str, str], int] = {}
    for item in items:
        node_id = kg.add_node(text=item.text, label=item.kind_hint)
        written.append((item, node_id))
        by_key.setdefault((item.source_id, item.item_id), node_id)

    placeholders: dict[tuple[str, str], int] = {}
    contexts: list[dict] = []
    links = 0
    for item, source in written:
        for link in item.links:
            key = (item.source_id, link.target_id)
            target = by_key.get(key)
            if target is None:
                target = placeholders.get(key)
                if target is None:
                    target = kg.add_node(text=link.target_id, label="unresolved")
                    placeholders[key] = target
            kg.add_edge(source, target, 1.0, {
                link.type: True,
                "origin": "format",
                "role": link.role,
            })
            contexts.append({
                "source_id": item.source_id,
                "item_id": item.item_id,
                "target_id": link.target_id,
                "context": list(link.context),
            })
            links += 1

    return {
        "items": len(written),
        "links": links,
        "unresolved_targets": len(placeholders),
        "context": contexts,
    }
