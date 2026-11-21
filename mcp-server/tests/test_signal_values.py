"""RUN-12: a DBC signal's value table reaches the model.

The DBC, the message, and the signals are synthetic. The names are made
up for this test. No corpus, no network.
"""

import textwrap
from pathlib import Path

import pygrap

from grap_mcp.procedure_template import compose_instructions, load_template
from grap_mcp.readers import SourceItem
from grap_mcp.readers.dbc import read_dbc
from grap_mcp.schema.project import project_items

# LampBox is not a corpus message. Mode has a value table. Plain has none.
# SortSig's raw numbers are written 10 then 2 so string order differs from
# numeric order.
_DBC = """\
VERSION "SYN-12"

NS_ :

BS_:

BU_: NodeA

BO_ 256 LampBox: 8 NodeA
 SG_ Mode : 0|8@1+ (1,0) [0|10] "" NodeA
 SG_ Plain : 8|8@1+ (1,0) [0|1] "" NodeA
 SG_ SortSig : 16|8@1+ (1,0) [0|10] "" NodeA

CM_ SG_ 256 Mode "selector position";
CM_ SG_ 256 SortSig "named positions";

VAL_ 256 Mode 0 "off" 1 "on" 10 "fault" ;
VAL_ 256 SortSig 10 "c" 2 "b" ;
"""

_MODE_TEXT = "LampBox.Mode\nselector position"
# Content hash of that engine text, measured on this graph before the
# values field existed.
_MODE_HASH = "0890487ea75661eb956abf2b0102807b01ed1fa0c69788e1a59f16f0bd8ba1cb"

_TINY = textwrap.dedent(
    """\
    ---
    id: tiny
    name: Tiny
    level: software
    ---
    Head {{title}}
    {{#cases}}[{{id}}]{{/cases}}
    Tail
    """
)


def _project(tmp_path: Path, *, requirement: bool = False):
    path = tmp_path / "synthbox.dbc"
    path.write_text(_DBC, encoding="utf-8")
    items = read_dbc(path, corpus_root=tmp_path)
    if requirement:
        items.append(SourceItem(
            source_id="synthbox",
            release=None,
            item_id="REQ-LAMP",
            kind_hint="requirement",
            text="The lamp shall follow the selector.",
            heading_path=[],
            attributes={},
            links=[],
            file="synthbox.txt",
            sha256="0" * 64,
        ))
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    return kg, project_items(items, kg)


def _node(projection, type_name: str, native_id: str):
    found = [
        node for node in projection["nodes"]
        if node["type"] == type_name and node["native_id"] == native_id
    ]
    assert len(found) == 1, (type_name, native_id, len(found))
    return found[0]


def test_a_dbc_signal_with_a_value_table_projects_values(tmp_path):
    """The Signal node carries the table. Keys stay strings."""
    _kg, projection = _project(tmp_path)
    signal = _node(projection, "Signal", "LampBox.Mode")
    assert signal.get("values") == {"0": "off", "1": "on", "10": "fault"}
    assert all(
        type(raw) is str and type(name) is str
        for raw, name in signal["values"].items()
    )
    item = _node(projection, "Item", "LampBox.Mode")
    assert "values" not in item


def test_b_dbc_signal_without_a_value_table_has_no_values_key(tmp_path):
    """A signal with no table does not grow a values key."""
    _kg, projection = _project(tmp_path)
    signal = _node(projection, "Signal", "LampBox.Plain")
    assert "values" not in signal


def test_c_signal_engine_text_and_content_hash_ignore_the_table(tmp_path):
    """Engine text and its content hash stay the pre-change pair."""
    kg, projection = _project(tmp_path)
    signal = _node(projection, "Signal", "LampBox.Mode")
    text = kg.get_node_text(signal["engine_id"])
    digest = kg.get_node_content_hash(signal["engine_id"])
    assert text == _MODE_TEXT
    assert digest == _MODE_HASH


def _instructions(tmp_path):
    kg, projection = _project(tmp_path, requirement=True)
    template_path = tmp_path / "tiny.md"
    template_path.write_text(_TINY, encoding="utf-8")
    template = load_template(template_path)
    requirement = _node(projection, "Requirement", "REQ-LAMP")
    record = {
        "key": requirement["key"],
        "source_id": "synthbox",
        "item_id": "REQ-LAMP",
    }
    context = {"requirements": [{
        "key": requirement["key"],
        "source_id": "synthbox",
        "item_id": "REQ-LAMP",
        "nodes": [
            _node(projection, "Signal", "LampBox.SortSig")["key"],
            _node(projection, "Signal", "LampBox.Plain")["key"],
        ],
    }]}
    return compose_instructions(kg, projection, [record], context, template)


def test_d_values_row_follows_own_text_in_numeric_order(tmp_path):
    """2 sorts before 10. The row sits on the line after the node's own text."""
    text = _instructions(tmp_path)
    assert "named positions\nvalues: 2 = b, 10 = c\n" in text
    assert "values: 10 = c, 2 = b" not in text


def test_e_context_node_without_values_has_no_values_row(tmp_path):
    """A context node with no values mapping gets no values row."""
    text = _instructions(tmp_path)
    marker = "LampBox.Plain (synthbox)\nLampBox.Plain\n"
    assert marker in text
    assert not text.split(marker, 1)[1].startswith("values:")
