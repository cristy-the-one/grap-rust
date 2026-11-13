"""R-08: a SourceItem link is a typed edge; a missing target is a node."""

import pygrap

from grap_mcp.format_links import write_format_links
from grap_mcp.readers import SourceItem, SourceLink


def _item(item_id: str, links: list[SourceLink]) -> SourceItem:
    return SourceItem(
        source_id="fixture",
        release=None,
        item_id=item_id,
        kind_hint="requirement",
        text=item_id,
        heading_path=[],
        attributes={},
        links=links,
        file="fixture.txt",
        sha256="0" * 64,
    )


def _labels(kg) -> list[str | None]:
    return [kg.get_node_label(i) for i in range(kg.node_id_space())]


def test_missing_target_is_an_unresolved_node(tmp_path):
    """Two items, one linking to a missing ID → 3 nodes, 1 edge, 1 unresolved."""
    items = [
        _item("REQ-1", [SourceLink(
            type="traces_to", target_id="REQ-MISSING", role="derivedFrom", context=["REQ-2"],
        )]),
        _item("REQ-2", []),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    write_format_links(items, kg)

    labels = _labels(kg)
    assert kg.node_count() == 3
    assert kg.edge_count() == 1
    assert labels.count("unresolved") == 1

    source = labels.index("REQ-1")
    placeholder = labels.index("unresolved")
    assert kg.get_node_text(placeholder) == "REQ-MISSING"
    assert kg.neighbors_by_relation(source, "origin", "format") == [(placeholder, 1.0)]
    assert kg.neighbors_by_relation(source, "relation_type", "traces_to") == [
        (placeholder, 1.0),
    ]
    assert kg.neighbors_by_relation(source, "role", "derivedFrom") == [(placeholder, 1.0)]
    assert kg.neighbors_by_relation(source, "context", '["REQ-2"]') == [(placeholder, 1.0)]
    assert kg.neighbors_by_relation(labels.index("REQ-2"), "origin", "format") == []


def test_link_to_an_item_in_the_corpus_creates_no_placeholder(tmp_path):
    items = [
        _item("SIG", [SourceLink(
            type="part_of", target_id="MSG", role="", context=[],
        )]),
        _item("MSG", []),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    write_format_links(items, kg)

    labels = _labels(kg)
    assert kg.node_count() == 2
    assert kg.edge_count() == 1
    assert labels.count("unresolved") == 0
    source = labels.index("SIG")
    target = labels.index("MSG")
    assert kg.neighbors_by_relation(source, "origin", "format") == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "relation_type", "part_of") == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "role", "") == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "context", "[]") == [(target, 1.0)]
