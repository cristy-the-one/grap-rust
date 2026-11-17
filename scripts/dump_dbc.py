#!/usr/bin/env python3
"""Print a loom-dbc-dump/1 document for one DBC file.

    python scripts/dump_dbc.py --dbc FILE

The file is read through grap_mcp.readers.dbc. Each signal object is built
from those SourceItems, sorted by (id, start_bit, signal). The text is
json.dumps(obj, indent=2, ensure_ascii=False) plus a newline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_MCP = Path(__file__).resolve().parents[1] / "mcp-server"
if str(_MCP) not in sys.path:
    sys.path.insert(0, str(_MCP))

from grap_mcp.readers.dbc import read_dbc  # noqa: E402


def dump_dbc(path: str | Path) -> dict:
    """The loom-dbc-dump/1 object for one DBC file."""
    items = read_dbc(path)
    messages = {
        item.item_id: item for item in items if item.kind_hint == "message"
    }
    signals = [
        _signal(item, messages)
        for item in items
        if item.kind_hint == "signal"
    ]
    signals.sort(key=lambda row: (row["id"], row["start_bit"], row["signal"]))
    return {"format": "loom-dbc-dump/1", "signals": signals}


def _signal(item, messages: dict) -> dict:
    message_name = _message_name(item)
    prefix = f"{message_name}."
    if not item.item_id.startswith(prefix):
        raise ValueError(f"{item.item_id} is not a signal of {message_name}")
    attrs = item.attributes
    return {
        "message": message_name,
        "id": int(messages[message_name].attributes["frame_id"], 16),
        "signal": item.item_id[len(prefix):],
        "start_bit": int(attrs["start_bit"]),
        "length": int(attrs["length"]),
        "byte_order": attrs["byte_order"],
        "signed": _flag(attrs["is_signed"]),
        "factor": _number(attrs["factor"]),
        "offset": _number(attrs["offset"]),
        "unit": attrs["unit"],
        "multiplexer": _flag(attrs["is_multiplexer"]),
        "multiplexed_by": _multiplexed_by(attrs["multiplexer_ids"]),
        "labels": _labels(attrs.get("value_table", "")),
    }


def _message_name(item) -> str:
    names = [link.target_id for link in item.links if link.type == "part_of"]
    if len(names) != 1:
        raise ValueError(f"{item.item_id} has {len(names)} part_of links")
    return names[0]


def _flag(text: str) -> bool:
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"flag is {text!r}")


def _number(text: str):
    if any(mark in text for mark in (".", "e", "E")):
        return float(text)
    return int(text)


def _multiplexed_by(text: str):
    if text == "":
        return None
    ids = json.loads(text)
    if not ids:
        return None
    if len(ids) != 1:
        raise ValueError(f"multiplexer_ids has {len(ids)} ids")
    return ids[0]


def _labels(text: str) -> list:
    if text == "":
        return []
    table = json.loads(text)
    rows = [[_number(raw), name] for raw, name in table.items()]
    rows.sort(key=lambda row: row[0])
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Print a loom-dbc-dump/1 document for one DBC file.",
    )
    parser.add_argument("--dbc", required=True, type=Path)
    args = parser.parse_args(argv)
    document = dump_dbc(args.dbc)
    sys.stdout.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
