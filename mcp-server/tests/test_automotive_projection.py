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
    group = _with_id(nodes, "Signal", "/ISignal/message1Group")
    assert set(kg.neighbors_by_relation(signal, "part_of", True)) == {
        (pdu, 1.0),
        (group, 1.0),
    }
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
    assert "namespace_items=n/a (n=0 items, documents=0)" in out
    assert "namespace_links=n/a (n=0 links, documents=0)" in out
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


def test_packages_are_not_projected(tmp_path, capsys):
    """Amendment 1: an AR-PACKAGE is not a node, and containment to it is not an edge.

    Both are counted as namespace_items and namespace_links, outside the §7 totals.
    """
    items = [
        _item("/Pkg", kind_hint="package", attributes={"tag": "AR-PACKAGE"}),
        _item(
            "/Pkg/Sig",
            [SourceLink(type="part_of", target_id="/Pkg", role="", context=[])],
            kind_hint="signal",
            attributes={"tag": "I-SIGNAL"},
        ),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    assert all(node["native_id"] != "/Pkg" for node in result["nodes"])
    assert all(edge["to_native_id"] != "/Pkg" for edge in result["edges"])
    assert result["counts"]["namespace_items"] == 1
    assert result["counts"]["namespace_links"] == 1
    assert result["counts"]["items"] == 1
    assert result["counts"]["untyped_items"] == 0
    assert result["counts"]["links"] == 0
    assert result["counts"]["fallback_links"] is None
    out = capsys.readouterr().out
    assert "namespace_items=1 (n=1 items, documents=1)" in out
    assert "namespace_links=1 (n=1 links, documents=1)" in out
    assert "untyped_items=0 (n=1 items, documents=1)" in out


def test_reference_to_a_folded_object_arrives_at_its_first_end(tmp_path):
    """Amendment 1: a referred-to PDU-TRIGGERING still folds, and the reference arrives at the Pdu."""
    triggering = "/Ch/trig"
    pdu = "/Pdu/msg"
    network = "/Ch"
    items = [
        _item(network, attributes={"tag": "CAN-PHYSICAL-CHANNEL"}),
        _item(pdu, attributes={"tag": "I-SIGNAL-I-PDU"}),
        _item(
            triggering,
            [
                SourceLink(type="part_of", target_id=network, role="", context=[]),
                SourceLink(type="refers_to", target_id=pdu, role="I-PDU-REF", context=[]),
            ],
            attributes={"tag": "PDU-TRIGGERING"},
        ),
        _item(
            "/Note",
            [SourceLink(type="refers_to", target_id=triggering, role="NOTE-REF", context=[])],
        ),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    nodes = _nodes(kg)
    assert all(node["native_id"] != triggering for node in result["nodes"])
    folded = [
        edge for edge in result["edges"]
        if edge["evidence"] == triggering and edge["relation"] == "transmitted_on"
    ]
    assert len(folded) == 1
    assert folded[0]["from_native_id"] == pdu
    assert folded[0]["from_type"] == "Pdu"
    assert folded[0]["to_native_id"] == network
    assert folded[0]["to_type"] == "Network"
    note = _with_id(nodes, "Element", "/Note")
    arrived = _with_id(nodes, "Pdu", pdu)
    assert kg.neighbors_by_relation(note, "refers_to", True) == [(arrived, 1.0)]


def test_nested_pdus_and_signal_groups_run_from_part_to_whole(tmp_path):
    """Amendment 1: the reference points at the part; the part_of edge points at the whole."""
    items = [
        _item(
            "/Mux",
            [SourceLink(type="refers_to", target_id="/Part", role="I-PDU-REF", context=[])],
            attributes={"tag": "MULTIPLEXED-I-PDU"},
        ),
        _item("/Part", attributes={"tag": "I-SIGNAL-I-PDU"}),
        _item("/Net", attributes={"tag": "CAN-PHYSICAL-CHANNEL"}),
        _item(
            "/Trig",
            [
                SourceLink(type="part_of", target_id="/Net", role="", context=[]),
                SourceLink(type="refers_to", target_id="/Inner", role="I-PDU-REF", context=[]),
            ],
            attributes={"tag": "PDU-TRIGGERING"},
        ),
        _item("/Inner", attributes={"tag": "I-SIGNAL-I-PDU"}),
        _item(
            "/Box",
            [SourceLink(
                type="refers_to", target_id="/Trig",
                role="CONTAINED-PDU-TRIGGERING-REF", context=[],
            )],
            attributes={"tag": "CONTAINER-I-PDU"},
        ),
        _item(
            "/Trig2",
            [
                SourceLink(type="part_of", target_id="/Net", role="", context=[]),
                SourceLink(type="refers_to", target_id="/Pay", role="I-PDU-REF", context=[]),
            ],
            attributes={"tag": "PDU-TRIGGERING"},
        ),
        _item("/Pay", attributes={"tag": "I-SIGNAL-I-PDU"}),
        _item(
            "/Sec",
            [SourceLink(type="refers_to", target_id="/Trig2", role="PAYLOAD-REF", context=[])],
            attributes={"tag": "SECURED-I-PDU"},
        ),
        _item(
            "/Grp",
            [SourceLink(type="refers_to", target_id="/Mem", role="I-SIGNAL-REF", context=[])],
            attributes={"tag": "I-SIGNAL-GROUP"},
        ),
        _item("/Mem", attributes={"tag": "I-SIGNAL"}),
        _item(
            "/SGrp",
            [SourceLink(
                type="refers_to", target_id="/SMem", role="SYSTEM-SIGNAL-REF", context=[],
            )],
            attributes={"tag": "SYSTEM-SIGNAL-GROUP"},
        ),
        _item("/SMem", attributes={"tag": "SYSTEM-SIGNAL"}),
    ]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    nodes = _nodes(kg)

    def edge(source, target):
        return (target, 1.0) in kg.neighbors_by_relation(source, "part_of", True)

    part = _with_id(nodes, "Pdu", "/Part")
    mux = _with_id(nodes, "Pdu", "/Mux")
    inner = _with_id(nodes, "Pdu", "/Inner")
    box = _with_id(nodes, "Pdu", "/Box")
    pay = _with_id(nodes, "Pdu", "/Pay")
    sec = _with_id(nodes, "Pdu", "/Sec")
    member = _with_id(nodes, "Signal", "/Mem")
    group = _with_id(nodes, "Signal", "/Grp")
    system_member = _with_id(nodes, "Signal", "/SMem")
    system_group = _with_id(nodes, "Signal", "/SGrp")
    assert edge(part, mux)
    assert edge(inner, box)
    assert edge(pay, sec)
    assert edge(member, group)
    assert edge(system_member, system_group)
    assert not edge(mux, part)
    assert not edge(group, member)
    assert all(node["native_id"] not in {"/Trig", "/Trig2"} for node in result["nodes"])
    assert kg.neighbors_by_relation(mux, "refers_to", True) == []
    assert kg.neighbors_by_relation(group, "refers_to", True) == []


def test_system_4_2_packages_triggerings_and_shares(tmp_path, capsys):
    """Amendment 1 on system-4.2.arxml: no package node, and the five triggerings fold.

    Untyped items fall below 20%. Fallback links do not: the ones that remain
    are the categories Amendment 1 leaves as Element (PDU groups, network
    management, controllers, transformation, secure-communication properties).
    §7 says that alarm is a finding for the spec, so the test records it.
    """
    items, _report = read_arxml(
        [_FIXTURES / "system-4.2.arxml"], source_id="system-4.2",
    )
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    packages = {
        item.item_id for item in items if item.attributes.get("tag") == "AR-PACKAGE"
    }
    assert packages
    leaked = sorted({
        node["native_id"] for node in result["nodes"] if node["native_id"] in packages
    })
    assert leaked == []
    assert all(edge["to_native_id"] not in packages for edge in result["edges"])
    assert all(edge["from_native_id"] not in packages for edge in result["edges"])
    namespace_links = sum(
        1
        for item in items
        for link in item.links
        if link.type == "part_of" and link.target_id in packages
    )
    assert result["counts"]["namespace_items"] == len(packages)
    assert result["counts"]["namespace_links"] == namespace_links
    projected = [
        item for item in items
        if item.kind_hint != "unresolved" and item.attributes.get("tag") != "AR-PACKAGE"
    ]
    assert result["counts"]["items"] == len(projected)
    triggerings = [
        item for item in items if item.attributes.get("tag") == "PDU-TRIGGERING"
    ]
    assert len(triggerings) == 5
    triggering_ids = {item.item_id for item in triggerings}
    assert all(node["native_id"] not in triggering_ids for node in result["nodes"])
    for item in triggerings:
        folded = [
            edge for edge in result["edges"]
            if edge["evidence"] == item.item_id and edge["relation"] == "transmitted_on"
        ]
        assert len(folded) == 1, item.item_id
        assert folded[0]["from_type"] == "Pdu"
        assert folded[0]["to_type"] == "Network"
    assert all(
        edge["from_native_id"] not in triggering_ids
        and edge["to_native_id"] not in triggering_ids
        for edge in result["edges"]
    )
    counts = result["counts"]
    assert counts["untyped_items"] * 5 < counts["items"], counts
    assert result["alarm"]["untyped_items"] is False
    assert counts["fallback_links"] * 5 > counts["links"], counts
    assert result["alarm"]["fallback_links"] is True
    out = capsys.readouterr().out
    documents = counts["documents"]
    assert (
        f"namespace_items={len(packages)} (n={len(packages)} items, documents={documents})"
    ) in out
    assert (
        f"namespace_links={namespace_links} "
        f"(n={namespace_links} links, documents={documents})"
    ) in out
    assert (
        f"untyped_items={counts['untyped_items']} "
        f"(n={counts['items']} items, documents={documents})"
    ) in out
    assert (
        f"fallback_links={counts['fallback_links']} "
        f"(n={counts['links']} links, documents={documents})"
    ) in out
    assert (
        f"alarm: fallback_links={counts['fallback_links']}/{counts['links']} exceeds 20% "
        f"(documents={documents})"
    ) in out
    assert (
        f"alarm: untyped_items={counts['untyped_items']}/{counts['items']} exceeds 20%"
    ) not in out
