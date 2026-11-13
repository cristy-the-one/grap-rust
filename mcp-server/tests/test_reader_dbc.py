"""R-02: a DBC file becomes SourceItems, or the reader raises."""

import hashlib
from pathlib import Path

import pytest

from grap_mcp.readers import SourceLink
from grap_mcp.readers.dbc import read_dbc

# Two messages. Signal order inside EngineData is file order, which is not
# start-bit order (EngineTemp starts at bit 16, EngineSpeed at bit 0).
DBC = """\
VERSION "REL-2026.11"

NS_ :

BS_:

BU_: Engine Gateway Abs

BO_ 256 EngineData: 8 Engine
 SG_ EngineTemp : 16|8@1- (1,-40) [-40|215] "C" Gateway
 SG_ EngineSpeed : 0|16@1+ (0.1,0) [0|8000] "rpm" Gateway,Abs

BO_ 512 VehicleStatus: 4 Gateway
 SG_ Ignition : 7|2@0+ (1,0) [0|3] "" Engine

CM_ BO_ 256 "Engine measurements";
CM_ SG_ 256 EngineSpeed "Crankshaft speed";

BA_DEF_ BO_ "GenMsgCycleTime" INT 0 65535;
BA_DEF_DEF_ "GenMsgCycleTime" 0;
BA_ "GenMsgCycleTime" BO_ 256 10;

VAL_ 512 Ignition 0 "Off" 1 "Run" 3 "Start" ;
"""


def _part_of(target_id: str) -> SourceLink:
    return SourceLink(type="part_of", target_id=target_id, role="", context=[])


def test_two_message_dbc_items_ids_and_links(tmp_path: Path):
    path = tmp_path / "vehicle.dbc"
    payload = DBC.encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    items = read_dbc(path, corpus_root=tmp_path)
    assert read_dbc(path, corpus_root=tmp_path) == items

    assert len(items) == 5
    assert [item.item_id for item in items] == [
        "EngineData",
        "EngineData.EngineTemp",
        "EngineData.EngineSpeed",
        "VehicleStatus",
        "VehicleStatus.Ignition",
    ]
    assert [item.kind_hint for item in items] == [
        "message",
        "signal",
        "signal",
        "message",
        "signal",
    ]

    signals = [item for item in items if item.kind_hint == "signal"]
    assert len(signals) == 3
    assert signals[0].links == [_part_of("EngineData")]
    assert signals[1].links == [_part_of("EngineData")]
    assert signals[2].links == [_part_of("VehicleStatus")]
    assert items[0].links == []
    assert items[3].links == []

    for item in items:
        assert item.source_id == "vehicle"
        assert item.release == "REL-2026.11"
        assert item.heading_path == []
        assert item.file == "vehicle.dbc"
        assert item.sha256 == digest
        assert isinstance(item.text, str)
        assert all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in item.attributes.items()
        )

    assert items[0].text == "Engine measurements"
    assert items[0].attributes == {
        "frame_id": "0x100",
        "is_extended_frame": "false",
        "length": "8",
        "cycle_time": "10",
        "senders": "Engine",
    }
    assert items[1].text == ""
    assert items[1].attributes == {
        "unit": "C",
        "factor": "1",
        "offset": "-40",
        "min": "-40",
        "max": "215",
        "length": "8",
        "start_bit": "16",
        "byte_order": "little_endian",
        "is_signed": "true",
        "is_float": "false",
        "is_multiplexer": "false",
        "multiplexer_ids": "",
        "multiplexer_signal": "",
        "receivers": "Gateway",
    }
    assert items[2].text == "Crankshaft speed"
    assert items[2].attributes == {
        "unit": "rpm",
        "factor": "0.1",
        "offset": "0",
        "min": "0",
        "max": "8000",
        "length": "16",
        "start_bit": "0",
        "byte_order": "little_endian",
        "is_signed": "false",
        "is_float": "false",
        "is_multiplexer": "false",
        "multiplexer_ids": "",
        "multiplexer_signal": "",
        "receivers": "Gateway,Abs",
    }
    assert items[3].text == ""
    assert items[3].attributes == {
        "frame_id": "0x200",
        "is_extended_frame": "false",
        "length": "4",
        "senders": "Gateway",
    }
    assert items[4].text == ""
    assert items[4].attributes == {
        "unit": "",
        "factor": "1",
        "offset": "0",
        "min": "0",
        "max": "3",
        "length": "2",
        "start_bit": "7",
        "byte_order": "big_endian",
        "is_signed": "false",
        "is_float": "false",
        "is_multiplexer": "false",
        "multiplexer_ids": "",
        "multiplexer_signal": "",
        "receivers": "Engine",
        "value_table": '{"0": "Off", "1": "Run", "3": "Start"}',
    }
    assert all(" " not in key for item in items for key in item.attributes)


def test_identical_bytes_give_identical_items(tmp_path: Path):
    payload = DBC.encode("utf-8")
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    left_root.mkdir()
    right_root.mkdir()
    left = left_root / "vehicle.dbc"
    right = right_root / "vehicle.dbc"
    left.write_bytes(payload)
    right.write_bytes(payload)
    assert read_dbc(left, corpus_root=left_root) == read_dbc(right, corpus_root=right_root)


# 2147483904 is 0x80000100: CAN id 0x100 with the DBC extended-frame bit set.
FEATURES = """\
VERSION "1"

NS_ :

BS_:

BU_: Node

BO_ 2147483904 ExtStatus: 8 Node
 SG_ SignedVal : 0|8@1- (1,0) [-10|10] "u" Node

BO_ 100 MuxMsg: 8 Node
 SG_ Muxer M : 0|4@1+ (1,0) [0|3] "" Node
 SG_ Speed m0 : 8|16@1+ (1,0) [0|100] "kph" Node
 SG_ Temp m1 : 8|16@1- (0.1,0) [-40|150] "C" Node

SIG_VALTYPE_ 100 Speed : 1;
"""


def test_extended_mux_signed_and_float_attributes(tmp_path: Path):
    path = tmp_path / "features.dbc"
    path.write_bytes(FEATURES.encode("utf-8"))
    items = read_dbc(path, corpus_root=tmp_path)
    assert [item.item_id for item in items] == [
        "ExtStatus",
        "ExtStatus.SignedVal",
        "MuxMsg",
        "MuxMsg.Muxer",
        "MuxMsg.Speed",
        "MuxMsg.Temp",
    ]
    assert items[0].attributes == {
        "frame_id": "0x100",
        "is_extended_frame": "true",
        "length": "8",
        "senders": "Node",
    }
    assert items[1].links == [_part_of("ExtStatus")]
    assert items[1].attributes["is_signed"] == "true"
    assert items[1].attributes["is_float"] == "false"
    assert items[1].attributes["is_multiplexer"] == "false"
    assert items[1].attributes["multiplexer_ids"] == ""
    assert items[1].attributes["multiplexer_signal"] == ""
    assert items[2].attributes["frame_id"] == "0x64"
    assert items[2].attributes["is_extended_frame"] == "false"
    assert items[3].links == [_part_of("MuxMsg")]
    assert items[3].attributes["is_multiplexer"] == "true"
    assert items[3].attributes["multiplexer_signal"] == ""
    assert items[4].attributes["is_float"] == "true"
    assert items[4].attributes["is_signed"] == "false"
    assert items[4].attributes["is_multiplexer"] == "false"
    assert items[4].attributes["multiplexer_ids"] == "[0]"
    assert items[4].attributes["multiplexer_signal"] == "Muxer"
    assert items[5].attributes["is_signed"] == "true"
    assert items[5].attributes["is_float"] == "false"
    assert items[5].attributes["multiplexer_ids"] == "[1]"
    assert items[5].attributes["multiplexer_signal"] == "Muxer"
    assert items[5].links == [_part_of("MuxMsg")]


def test_corrupt_dbc_raises(tmp_path: Path):
    path = tmp_path / "broken.dbc"
    path.write_bytes(b"this is not a dbc\n")
    with pytest.raises(Exception):
        read_dbc(path, corpus_root=tmp_path)


def test_unassigned_signals_are_not_dropped(tmp_path: Path):
    # cantools discards BO_ VECTOR__INDEPENDENT_SIG_MSG. A partial list
    # would hide those signals, so the reader must raise instead.
    path = tmp_path / "independent.dbc"
    path.write_bytes(
        b"BO_ 3221225472 VECTOR__INDEPENDENT_SIG_MSG: 0 Vector__XXX\n"
        b" SG_ Unassigned : 0|8@1+ (1,0) [0|1] \"\" Vector__XXX\n"
        b"\n"
        b"BO_ 10 Keep: 1 Node\n"
        b" SG_ Speed : 0|8@1+ (1,0) [0|1] \"\" Node\n"
    )
    with pytest.raises(Exception, match="VECTOR__INDEPENDENT_SIG_MSG"):
        read_dbc(path, corpus_root=tmp_path)
