"""R-08: format links are edges keyed by (source_id, item_id). Schema §6."""

import pygrap

from grap_mcp.format_links import write_format_links
from grap_mcp.readers import SourceItem, SourceLink


def _item(
    item_id: str,
    links: list[SourceLink],
    *,
    source_id: str = "fixture",
    kind_hint: str = "requirement",
    text: str | None = None,
) -> SourceItem:
    return SourceItem(
        source_id=source_id,
        release=None,
        item_id=item_id,
        kind_hint=kind_hint,
        text=item_id if text is None else text,
        heading_path=[],
        attributes={},
        links=links,
        file=f"{source_id}.txt",
        sha256="0" * 64,
    )


def _node_with_text(kg, text: str) -> int:
    found = [
        i for i in range(kg.node_id_space())
        if kg.get_node_text(i) == text
    ]
    assert len(found) == 1
    return found[0]


def test_missing_target_is_an_unresolved_node(tmp_path):
    """Two items, one linking to a missing ID → 3 nodes, 1 edge, 1 unresolved."""
    items = [
        _item("REQ-1", [SourceLink(
            type="traces_to", target_id="REQ-MISSING", role="derivedFrom", context=["REQ-2"],
        )]),
        _item("REQ-2", []),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = write_format_links(items, kg)

    assert kg.node_count() == 3
    assert kg.edge_count() == 1
    source = _node_with_text(kg, "REQ-1")
    other = _node_with_text(kg, "REQ-2")
    placeholder = _node_with_text(kg, "REQ-MISSING")
    assert kg.get_node_label(source) == "requirement"
    assert kg.get_node_label(other) == "requirement"
    assert kg.get_node_label(placeholder) == "unresolved"
    assert kg.neighbors_by_relation(source, "traces_to", True) == [(placeholder, 1.0)]
    assert kg.neighbors_by_relation(source, "origin", "format") == [(placeholder, 1.0)]
    assert kg.neighbors_by_relation(source, "role", "derivedFrom") == [(placeholder, 1.0)]
    assert kg.neighbors_by_relation(source, "relation_type", "traces_to") == []
    assert kg.neighbors_by_relation(source, "context", '["REQ-2"]') == []
    assert kg.neighbors_by_relation(other, "origin", "format") == []
    assert result == {
        "items": 2,
        "links": 1,
        "unresolved_targets": 1,
        "context": [{
            "source_id": "fixture",
            "item_id": "REQ-1",
            "target_id": "REQ-MISSING",
            "context": ["REQ-2"],
        }],
    }


def test_link_to_an_item_in_the_corpus_creates_no_placeholder(tmp_path):
    items = [
        _item("SIG", [SourceLink(
            type="part_of", target_id="MSG", role="", context=[],
        )], kind_hint="signal"),
        _item("MSG", [], kind_hint="message"),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = write_format_links(items, kg)

    assert kg.node_count() == 2
    assert kg.edge_count() == 1
    source = _node_with_text(kg, "SIG")
    target = _node_with_text(kg, "MSG")
    assert kg.get_node_label(source) == "signal"
    assert kg.get_node_label(target) == "message"
    assert kg.neighbors_by_relation(source, "part_of", True) == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "origin", "format") == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "role", "") == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "context", "[]") == []
    assert result["items"] == 2
    assert result["links"] == 1
    assert result["unresolved_targets"] == 0
    assert result["context"] == [{
        "source_id": "fixture",
        "item_id": "SIG",
        "target_id": "MSG",
        "context": [],
    }]


def test_same_item_id_in_two_sources_keep_their_links_apart(tmp_path):
    """Two sources with the same item IDs must not resolve each other's links."""
    link = SourceLink(type="part_of", target_id="MSG", role="", context=["note"])
    items = [
        _item("SIG", [link], source_id="a", kind_hint="signal", text="a:SIG"),
        _item("MSG", [], source_id="a", kind_hint="message", text="a:MSG"),
        _item("SIG", [link], source_id="b", kind_hint="signal", text="b:SIG"),
        _item("MSG", [], source_id="b", kind_hint="message", text="b:MSG"),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = write_format_links(items, kg)

    assert kg.node_count() == 4
    assert kg.edge_count() == 2
    a_sig = _node_with_text(kg, "a:SIG")
    b_sig = _node_with_text(kg, "b:SIG")
    assert kg.neighbors_by_relation(a_sig, "part_of", True) == [
        (_node_with_text(kg, "a:MSG"), 1.0),
    ]
    assert kg.neighbors_by_relation(b_sig, "part_of", True) == [
        (_node_with_text(kg, "b:MSG"), 1.0),
    ]
    assert result["items"] == 4
    assert result["links"] == 2
    assert result["unresolved_targets"] == 0
    assert result["context"] == [
        {"source_id": "a", "item_id": "SIG", "target_id": "MSG", "context": ["note"]},
        {"source_id": "b", "item_id": "SIG", "target_id": "MSG", "context": ["note"]},
    ]
