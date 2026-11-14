"""S-04: one executable check per question in schema §9.

Each check walks the path in that row and no other relation. Edges are the
ones project_items wrote. Relation lookup goes through
working_graph._relation_filter, which is the pair of _relation_properties:
a schema relation is its own property, and any other verb is refers_to
with role=<verb>. §8 has no reader row for TestConcept or TestCase, so the
verifies hop is attached with that same encoder. The projection is not
changed.
"""

from pathlib import Path

import pygrap

from grap_mcp.kee.working_graph import _relation_properties
from grap_mcp.readers import SourceItem, SourceLink
from grap_mcp.readers.arxml import read_arxml
from grap_mcp.schema.project import project_items
from grap_mcp.schema.questions import (
    affected_by_signal,
    involved_signals,
    parameter_settings,
    run_checks,
    same_signal,
    signal_travel,
    tester_needs as question_needs,
    unverified_requirements,
    upstream_requirements,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arxml"
_NA = (
    "q1 n/a (n=0 requirements, documents=0)\n"
    "q2 n/a (n=0 requirements, documents=0)\n"
    "q3 n/a (n=0 signals, documents=0)\n"
    "q4 n/a (n=0 requirements, documents=0)\n"
    "q5 n/a (n=0 statements, documents=0)\n"
    "q6 n/a (n=0 signals, documents=0)\n"
    "q7 n/a (n=0 requirements, documents=0)\n"
    "q8 n/a (n=0 signals, documents=0)\n"
)


def _link(kind, target, role=""):
    return SourceLink(type=kind, target_id=target, role=role, context=[])


def _item(item_id, links=(), *, kind_hint="text", attributes=None, release=None):
    return SourceItem(
        source_id="fix",
        release=release,
        item_id=item_id,
        kind_hint=kind_hint,
        text=item_id,
        heading_path=[],
        attributes={} if attributes is None else dict(attributes),
        links=list(links),
        file="fix.txt",
        sha256="0" * 64,
    )


def _tag(name):
    return {"tag": name}


def _fixture_items():
    """One projected graph that has a path for every §9 row.

    TestConcept and TestCase are not in this list. §8 does not map a reader
    item onto them, and S-03 is left as it is.
    """
    return [
        _item("REQ-A", [
            _link("traces_to", "REQ-B"),
            _link("has_statement", "ST-A"),
        ], kind_hint="requirement"),
        _item("REQ-B", [_link("traces_to", "REQ-C")], kind_hint="requirement"),
        _item("REQ-C", [_link("traces_to", "REQ-A")], kind_hint="requirement"),
        _item("REQ-D", [_link("traces_to", "SIG")], kind_hint="requirement"),
        _item("REQ-E", [_link("has_statement", "ST-E")], kind_hint="requirement"),
        _item("ST-A", [
            _link("about", "SIG"),
            _link("scaled_like", "SIG2"),
            _link("refers_to", "NOTE", role="note"),
            _link("applies_in", "MODE"),
            _link("configured_by", "CAL"),
            _link("raises", "NOT-A-FAULT"),
        ], kind_hint="statement"),
        _item("ST-E", [_link("about", "SIG-E")], kind_hint="statement"),
        _item("SIG", [
            _link("part_of", "PDU"),
            _link("has_type", "SCALE"),
            _link("has_type", "UNITM"),
        ], attributes=_tag("I-SIGNAL")),
        _item("SIG2", attributes=_tag("I-SIGNAL")),
        _item("SIG-E", attributes=_tag("I-SIGNAL")),
        _item("SIG-DIRECT", [_link("part_of", "FR")], attributes=_tag("I-SIGNAL")),
        _item("PDU", [_link("part_of", "FR")], attributes=_tag("I-SIGNAL-I-PDU")),
        _item("FR", [_link("transmitted_on", "NET")], attributes=_tag("CAN-FRAME")),
        _item("NET", attributes=_tag("CAN-CLUSTER")),
        _item("SCALE", attributes=_tag("COMPU-METHOD")),
        _item("UNITM", attributes=_tag("UNIT")),
        _item("MODE", attributes=_tag("MODE-DECLARATION")),
        _item("CAL", attributes=_tag("ECUC-CONTAINER-VALUE")),
        _item("CAL2", attributes=_tag("ECUC-CONTAINER-VALUE")),
        _item("COMP", [_link("configured_by", "CAL2")], attributes=_tag("APPLICATION-SW-COMPONENT-TYPE")),
        _item("NOTE"),
        _item("NOT-A-FAULT"),
        _item("VehSpd", release="1", attributes=_tag("I-SIGNAL")),
        _item("VehSpd2", [
            _link("corresponds_to", "VehSpd"),
            _link("corresponds_to", "PDU"),
        ], release="2", attributes=_tag("I-SIGNAL")),
    ]


def _project(tmp_path, items):
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    result = project_items(items, kg)
    return kg, result


def _id(result, type_name, native_id):
    found = [
        node["engine_id"] for node in result["nodes"]
        if node["type"] == type_name and node["native_id"] == native_id
    ]
    assert len(found) == 1, (type_name, native_id, len(found))
    return found[0]


def _names(kg, ids):
    return [kg.get_node_text(nid).split("\n", 1)[0] for nid in ids]


def _verifies(kg, native_id, label, target):
    """Attach a verifies edge with the projection's encoder."""
    node = kg.add_node(text=native_id, label=label)
    props = dict(_relation_properties("verifies"))
    props["origin"] = "format"
    props["confidence"] = 1.0
    kg.add_edge(node, target, 1.0, props)
    return node


def test_eight_questions_follow_the_section_9_paths(tmp_path):
    """Each §9 row returns only the nodes on its path."""
    kg, result = _project(tmp_path, _fixture_items())
    req_a = _id(result, "Requirement", "REQ-A")
    req_b = _id(result, "Requirement", "REQ-B")
    req_c = _id(result, "Requirement", "REQ-C")
    req_d = _id(result, "Requirement", "REQ-D")
    req_e = _id(result, "Requirement", "REQ-E")
    st_a = _id(result, "Statement", "ST-A")
    st_e = _id(result, "Statement", "ST-E")
    sig = _id(result, "Signal", "SIG")
    sig2 = _id(result, "Signal", "SIG2")
    sig_e = _id(result, "Signal", "SIG-E")
    direct = _id(result, "Signal", "SIG-DIRECT")
    old = _id(result, "Signal", "VehSpd")
    new = _id(result, "Signal", "VehSpd2")
    concept = _verifies(kg, "TC-A", "TestConcept", req_a)
    case = _verifies(kg, "TC-B", "TestCase", req_b)
    _verifies(kg, "TC-E", "TestConcept", st_e)

    assert kg.neighbors_by_relation(st_a, "scaled_like", True) == []
    assert kg.neighbors_by_relation(st_a, "refers_to", True) == [
        (sig2, 1.0),
        (_id(result, "Element", "NOTE"), 1.0),
    ]
    assert kg.neighbors_by_relation(req_d, "traces_to", True) == []

    assert _names(kg, upstream_requirements(kg, req_a)) == ["REQ-B", "REQ-C"]
    assert _names(kg, upstream_requirements(kg, req_b)) == ["REQ-A", "REQ-C"]
    assert _names(kg, upstream_requirements(kg, req_c)) == ["REQ-A", "REQ-B"]
    assert upstream_requirements(kg, req_d) == []
    assert "REQ-A" not in _names(kg, upstream_requirements(kg, req_a))

    involved = involved_signals(kg, req_a)
    assert [
        (row["native_id"], row["relations"], row["scaling"]) for row in involved
    ] == [
        ("SIG", ("about",), ("SCALE", "UNITM")),
        ("SIG2", ("refers_to",), ()),
    ]
    assert involved_signals(kg, req_e) == [{
        "native_id": "SIG-E",
        "engine_id": sig_e,
        "relations": ("about",),
        "scaling": (),
    }]
    assert involved_signals(kg, req_d) == []

    assert signal_travel(kg, sig, result) == [{
        "pdu": "PDU",
        "frame": "FR",
        "network": "NET",
        "identifier": None,
    }]
    assert signal_travel(kg, direct, result) == [{
        "pdu": None,
        "frame": "FR",
        "network": "NET",
        "identifier": None,
    }]
    assert signal_travel(kg, sig2, result) == [{
        "pdu": None,
        "frame": None,
        "network": None,
        "identifier": None,
        "last": "SIG2",
    }]

    needs = question_needs(kg, req_a)
    assert {
        kg.get_node_text(nid).split("\n", 1)[0]: depth for nid, depth in needs.items()
    } == {
        "SIG": 1,
        "SIG2": 1,
        "NOTE": 1,
        "MODE": 1,
        "CAL": 1,
        "PDU": 2,
        "SCALE": 2,
        "UNITM": 2,
    }
    assert question_needs(kg, req_b) == {}
    assert _names(kg, question_needs(kg, req_e)) == ["SIG-E"]

    assert _names(kg, parameter_settings(kg, st_a)) == ["CAL"]
    assert parameter_settings(kg, st_e) == []

    affected = affected_by_signal(kg, sig)
    assert _names(kg, affected["statements"]) == ["ST-A"]
    assert _names(kg, affected["requirements"]) == ["REQ-A"]
    assert _names(kg, affected["test_concepts"]) == ["TC-A"]
    assert concept in affected["test_concepts"]
    assert case not in affected["test_concepts"]
    assert affected_by_signal(kg, sig2)["requirements"] == [req_a]
    lone = affected_by_signal(kg, sig_e)
    assert _names(kg, lone["statements"]) == ["ST-E"]
    assert _names(kg, lone["requirements"]) == ["REQ-E"]
    assert lone["test_concepts"] == []
    assert affected_by_signal(kg, direct) == {
        "statements": [],
        "requirements": [],
        "test_concepts": [],
    }

    requirements = [req_a, req_b, req_c, req_d, req_e]
    assert _names(kg, unverified_requirements(kg, requirements)) == [
        "REQ-C", "REQ-D", "REQ-E",
    ]

    assert _names(kg, same_signal(kg, new)) == ["VehSpd"]
    assert same_signal(kg, old) == []
    assert kg.neighbors_by_relation(new, "corresponds_to", True) == [(old, 1.0)]

    text = run_checks(kg, result)
    assert "REQ-D upstream=0 (n=5 requirements, documents=1)" in text
    assert "q7 unverified=3 (n=5 requirements, documents=1)" in text
    assert "SIG frames=1 (n=6 signals, documents=1)" in text
    assert "pdu=PDU frame=FR identifier=n/a network=NET" in text
    assert "SIG-DIRECT frames=1 (n=6 signals, documents=1)" in text
    assert "pdu=n/a frame=FR identifier=n/a network=NET" in text
    assert "last=SIG2" in text


def test_question_3_on_the_arxml_fixture(tmp_path):
    """signal1 travels on message1, identifier 5, on channel Pch0."""
    items, _report = read_arxml(
        [_FIXTURES / "system-4.2.arxml"], source_id="system-4.2",
    )
    kg, result = _project(tmp_path, items)
    signal = _id(result, "Signal", "/ISignal/signal1")
    assert signal_travel(kg, signal, result) == [
        {
            "pdu": "/ContainerIPdu/OneToContainThemAll",
            "frame": "/CanFrame/OneToContainThemAll",
            "network": "/Cluster/Cluster0/Pch0",
            "identifier": "102",
        },
        {
            "pdu": "/ISignalIPdu/message1",
            "frame": "/CanFrame/Message1",
            "network": "/Cluster/Cluster0/Pch0",
            "identifier": "5",
        },
    ]
    hello = _id(result, "Signal", "/ISignal/Hello")
    assert {
        "pdu": "/MultiplexedIPdu/multiplexed_message",
        "frame": "/CanFrame/MultiplexedMessage",
        "network": "/Cluster/Cluster0/Pch0",
        "identifier": "4",
    } in signal_travel(kg, hello, result)
    carried = (
        "/ISignal/Hello",
        "/ISignal/World1",
        "/ISignal/World2",
        "/ISignal/MultiplexedStatic",
        "/ISignal/MultiplexedStatic2",
        "/ISignal/multiplexed_message_selector",
    )
    missing = [
        native for native in carried
        if not any(
            row["frame"]
            for row in signal_travel(kg, _id(result, "Signal", native), result)
        )
    ]
    assert missing == []
    text = run_checks(kg, result)
    assert (
        "pdu=/ISignalIPdu/message1 frame=/CanFrame/Message1 "
        "identifier=5 network=/Cluster/Cluster0/Pch0"
    ) in text
    assert (
        "pdu=/MultiplexedIPdu/multiplexed_message frame=/CanFrame/MultiplexedMessage "
        "identifier=4 network=/Cluster/Cluster0/Pch0"
    ) in text
    assert "q1 n/a (n=0 requirements, documents=1)" in text
    assert "q7 n/a (n=0 requirements, documents=1)" in text


def test_question_3_follows_nesting(tmp_path):
    """Amendment 1: part_of walks any number of Pdu and group steps to a frame.

    A signal that reaches no frame reports the last node, not an empty answer.
    """
    items = [
        _item("SIG", [_link("part_of", "INNER")], attributes=_tag("I-SIGNAL")),
        _item("MEM", [_link("part_of", "GRP")], attributes=_tag("I-SIGNAL")),
        _item("GRP", [_link("part_of", "INNER")], attributes=_tag("I-SIGNAL-GROUP")),
        _item("INNER", [_link("part_of", "OUTER")], attributes=_tag("I-SIGNAL-I-PDU")),
        _item("OUTER", [_link("part_of", "FR")], attributes=_tag("MULTIPLEXED-I-PDU")),
        _item("FR", [_link("transmitted_on", "NET")], attributes=_tag("CAN-FRAME")),
        _item("NET", attributes=_tag("CAN-CLUSTER")),
        _item("LONE", attributes=_tag("I-SIGNAL")),
    ]
    kg, result = _project(tmp_path, items)
    reached = {
        "pdu": "OUTER",
        "frame": "FR",
        "network": "NET",
        "identifier": None,
    }
    assert signal_travel(kg, _id(result, "Signal", "SIG"), result) == [reached]
    assert signal_travel(kg, _id(result, "Signal", "MEM"), result) == [reached]
    assert signal_travel(kg, _id(result, "Signal", "LONE"), result) == [{
        "pdu": None,
        "frame": None,
        "network": None,
        "identifier": None,
        "last": "LONE",
    }]
    text = run_checks(kg, result)
    n_sig = sum(1 for node in result["nodes"] if node["type"] == "Signal")
    assert f"SIG frames=1 (n={n_sig} signals, documents=1)" in text
    assert "pdu=OUTER frame=FR identifier=n/a network=NET" in text
    assert f"LONE frames=0 (n={n_sig} signals, documents=1)" in text
    assert "last=LONE" in text


def test_empty_graph_prints_na(tmp_path, capsys):
    """Nothing to ask prints n/a, with a zero denominator."""
    kg, result = _project(tmp_path, [])
    text = run_checks(kg, result)
    assert text == _NA
    assert capsys.readouterr().out.endswith(_NA)
