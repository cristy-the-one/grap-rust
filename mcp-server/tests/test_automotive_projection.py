"""S-03: project SourceItems onto typed nodes and edges.

docs/spec-automotive-schema.md §5 and §8. The ARXML fixture's mapping
object /ISignalIPdu/message1/Signal1 becomes one part_of edge from a
Signal to a Pdu, and it is not itself a node. §7's four counts are
printed with the number of rows and documents behind them.
"""

from pathlib import Path

import pygrap

from grap_mcp.kee.working_graph import _relation_properties
from grap_mcp.readers import SourceItem, SourceLink
from grap_mcp.readers.arxml import read_arxml
from grap_mcp.schema.project import project_items

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arxml"
_MAPPING = "/ISignalIPdu/message1/Signal1"
_SIGNAL = "/ISignal/signal1"
_PDU = "/ISignalIPdu/message1"


def _nodes(kg):
    return [
        (i, kg.get_node_label(i), kg.get_node_text(i))
        for i in range(kg.node_id_space())
    ]


def _with_id(nodes, label, native_id):
    found = [
        i for i, kind, text in nodes
        if kind == label and (text == native_id or text.startswith(native_id + "\n"))
    ]
    assert len(found) == 1, (label, native_id, len(found))
    return found[0]


def _item(item_id, links=(), *, kind_hint="text", attributes=None, source_id="fixture"):
    return SourceItem(
        source_id=source_id,
        release=None,
        item_id=item_id,
        kind_hint=kind_hint,
        text=item_id,
        heading_path=[],
        attributes={} if attributes is None else attributes,
        links=list(links),
        file=f"{source_id}.txt",
        sha256="0" * 64,
    )


def test_signal1_mapping_is_a_part_of_edge_and_not_a_node(tmp_path, capsys):
    """/ISignalIPdu/message1/Signal1 folds into Signal —part_of→ Pdu."""
    items, _report = read_arxml(
        [_FIXTURES / "system-4.2.arxml"], source_id="system-4.2",
    )
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    nodes = _nodes(kg)

    assert all(
        not (text == _MAPPING or text.startswith(_MAPPING + "\n"))
        for _i, _label, text in nodes
    )
    assert all(node["native_id"] != _MAPPING for node in result["nodes"])

    signal = _with_id(nodes, "Signal", _SIGNAL)
    pdu = _with_id(nodes, "Pdu", _PDU)
    assert kg.neighbors_by_relation(signal, "part_of", True) == [(pdu, 1.0)]
    assert kg.neighbors_by_relation(signal, "rel", "part_of") == []

    folded = [
        edge for edge in result["edges"]
        if edge["evidence"] == _MAPPING and edge["relation"] == "part_of"
    ]
    assert len(folded) == 1
    edge = folded[0]
    assert edge["from_native_id"] == _SIGNAL
    assert edge["to_native_id"] == _PDU
    assert edge["from_type"] == "Signal"
    assert edge["to_type"] == "Pdu"
    assert edge["attributes"]["START-POSITION"] == "36"
    assert edge["attributes"]["PACKING-BYTE-ORDER"] == "MOST-SIGNIFICANT-BYTE-FIRST"
    expected = dict(_relation_properties("part_of"))
    expected["origin"] = "format"
    expected["confidence"] = 1.0
    assert edge["properties"] == expected

    counts = result["counts"]
    out = capsys.readouterr().out
    assert (
        f"untyped_items={counts['untyped_items']} "
        f"(n={counts['items']} items, documents={counts['documents']})"
    ) in out
    for name in ("fallback_links", "rejected_links", "unresolved"):
        assert (
            f"{name}={counts[name]} (n={counts['links']} links, "
            f"documents={counts['documents']})"
        ) in out
    assert counts["documents"] >= 1
    assert counts["items"] >= 1
    assert counts["links"] >= 1


def test_empty_projection_prints_na(tmp_path, capsys):
    """Nothing to measure prints n/a, with a zero denominator."""
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items([], kg)
    out = capsys.readouterr().out
    assert "untyped_items=n/a (n=0 items, documents=0)" in out
    assert "fallback_links=n/a (n=0 links, documents=0)" in out
    assert "rejected_links=n/a (n=0 links, documents=0)" in out
    assert "unresolved=n/a (n=0 links, documents=0)" in out
    assert result["counts"]["untyped_items"] is None
    assert result["counts"]["links"] == 0
    assert kg.node_count() == 0


def test_free_verb_is_encoded_by_the_working_graph_helper(tmp_path):
    """A verb outside the schema is refers_to with that verb as role."""
    items = [
        _item("A", [SourceLink(type="emite", target_id="B", role="", context=[])]),
        _item("B"),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    nodes = _nodes(kg)
    source = _with_id(nodes, "Element", "A")
    target = _with_id(nodes, "Element", "B")
    assert kg.neighbors_by_relation(source, "refers_to", True) == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "role", "emite") == [(target, 1.0)]
    assert kg.neighbors_by_relation(source, "emite", True) == []
    encoded = [
        edge for edge in result["edges"]
        if edge["relation"] == "emite" and edge["from_native_id"] == "A"
    ]
    assert len(encoded) == 1
    assert encoded[0]["properties"] == dict(_relation_properties("emite")) | {
        "origin": "format",
        "confidence": 1.0,
    }


def test_disallowed_traces_to_is_not_written(tmp_path):
    """An edge whose ends break §4 is rejected, not stored."""
    items = [
        _item(
            "REQ",
            [SourceLink(type="traces_to", target_id="Sig", role="satisfies", context=[])],
            kind_hint="requirement",
        ),
        _item("Sig", kind_hint="signal", attributes={"tag": "I-SIGNAL"}),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    nodes = _nodes(kg)
    requirement = _with_id(nodes, "Requirement", "REQ")
    assert kg.neighbors_by_relation(requirement, "traces_to", True) == []
    assert result["counts"]["rejected_links"] == 1
    assert result["counts"]["links"] == 1
    assert result["rejected"] == [{
        "source_id": "fixture",
        "item_id": "REQ",
        "target_id": "Sig",
        "relation": "traces_to",
        "from_type": "Requirement",
        "to_type": "Signal",
        "role": "satisfies",
    }]


def test_dbc_sender_and_signal_part_of_frame(tmp_path):
    """A DBC message is a Frame, its signal is part_of it, and a sender sends it."""
    items = [
        _item(
            "Speed",
            kind_hint="message",
            attributes={"frame_id": "0x10", "senders": "Body"},
            source_id="bus",
        ),
        _item(
            "Speed.Veh",
            [SourceLink(type="part_of", target_id="Speed", role="", context=[])],
            kind_hint="signal",
            attributes={"start_bit": "0", "receivers": "Abs"},
            source_id="bus",
        ),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    nodes = _nodes(kg)
    frame = _with_id(nodes, "Frame", "Speed")
    signal = _with_id(nodes, "Signal", "Speed.Veh")
    body = _with_id(nodes, "Ecu", "Body")
    abs_ecu = _with_id(nodes, "Ecu", "Abs")
    assert kg.neighbors_by_relation(signal, "part_of", True) == [(frame, 1.0)]
    assert kg.neighbors_by_relation(body, "sends", True) == [(frame, 1.0)]
    assert kg.neighbors_by_relation(abs_ecu, "receives", True) == [(signal, 1.0)]
    assert any(
        node["type"] == "Network" and node["native_id"] == "bus"
        for node in result["nodes"]
    )
    assert any(node["kind"] == "dbc_signal" for node in result["nodes"])
    assert any(node["kind"] == "dbc_message" for node in result["nodes"])
