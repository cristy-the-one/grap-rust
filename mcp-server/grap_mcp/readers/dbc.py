"""DBC reader: one SourceItem per message and per signal.

No model calls and no graph writes. A file that cannot be parsed raises.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import cantools
from cantools.database.can.database import Database as CanDatabase
from cantools.database.can.message import Message
from cantools.database.can.signal import Signal
from cantools.database.errors import ParseError

from grap_mcp.readers import SourceItem, SourceLink

# cantools drops this message and every signal on it. Returning the rest
# would be a partial list with no error.
_INDEPENDENT_MESSAGE = re.compile(
    br"(?m)^[ \t]*BO_[ \t]+\d+[ \t]+VECTOR__INDEPENDENT_SIG_MSG\b"
)


def read_dbc(path: str | Path, corpus_root: str | Path | None = None) -> list[SourceItem]:
    """Read `path` into message and signal items, in file order.

    `source_id` is the file stem. A message's `item_id` is its name. A
    signal's `item_id` is ``{message name}.{signal name}``. An empty name
    uses ``{source_id}#{n}`` with `n` the 0-based index in this list.
    `release` is the VERSION string, or None when the file has none.
    """
    file_path = Path(path)
    data = file_path.read_bytes()
    if _INDEPENDENT_MESSAGE.search(data):
        raise ParseError(
            "DBC reader cannot parse VECTOR__INDEPENDENT_SIG_MSG "
            "without dropping its signals"
        )

    # sort_signals=None keeps SG_ lines in file order. The default sorts
    # by start bit, so identical bytes would not keep source order.
    loaded = cantools.database.load_file(
        file_path,
        database_format="dbc",
        prune_choices=False,
        strict=True,
        sort_signals=None,
    )
    if not isinstance(loaded, CanDatabase):
        raise ParseError(f"DBC reader cannot parse {file_path}")

    source_id = file_path.stem
    release = loaded.version
    relative = _relative_file(file_path, corpus_root)
    digest = hashlib.sha256(data).hexdigest()
    items: list[SourceItem] = []
    for message in loaded.messages:
        message_id = _item_id(source_id, len(items), message.name)
        items.append(_message_item(
            message, source_id, release, message_id, relative, digest,
        ))
        for signal in message.signals:
            signal_id = _item_id(
                source_id,
                len(items),
                f"{message.name}.{signal.name}" if message.name and signal.name else "",
            )
            items.append(_signal_item(
                signal, source_id, release, signal_id, message_id, relative, digest,
            ))
    return items


def _item_id(source_id: str, index: int, native: str) -> str:
    if native:
        return native
    return f"{source_id}#{index}"


def _relative_file(path: Path, corpus_root: str | Path | None) -> str:
    if corpus_root is None:
        return path.as_posix()
    return path.resolve().relative_to(Path(corpus_root).resolve()).as_posix()


def _own_text(comment: str | None) -> str:
    if comment is None:
        return ""
    return comment


def _message_item(
    message: Message,
    source_id: str,
    release: str | None,
    item_id: str,
    relative: str,
    digest: str,
) -> SourceItem:
    attributes: dict[str, str] = {"length": str(message.length)}
    if message.cycle_time is not None:
        attributes["cycle time"] = str(message.cycle_time)
    attributes["senders"] = ",".join(message.senders)
    return SourceItem(
        source_id=source_id,
        release=release,
        item_id=item_id,
        kind_hint="message",
        text=_own_text(message.comment),
        heading_path=[],
        attributes=attributes,
        links=[],
        file=relative,
        sha256=digest,
    )


def _signal_item(
    signal: Signal,
    source_id: str,
    release: str | None,
    item_id: str,
    message_id: str,
    relative: str,
    digest: str,
) -> SourceItem:
    attributes: dict[str, str] = {
        "unit": "" if signal.unit is None else signal.unit,
        "factor": str(signal.scale),
        "offset": str(signal.offset),
    }
    if signal.minimum is not None:
        attributes["min"] = str(signal.minimum)
    if signal.maximum is not None:
        attributes["max"] = str(signal.maximum)
    attributes["length"] = str(signal.length)
    attributes["start bit"] = str(signal.start)
    attributes["byte order"] = signal.byte_order
    attributes["receivers"] = ",".join(signal.receivers)
    if signal.choices:
        table = {str(raw): str(name) for raw, name in signal.choices.items()}
        attributes["value table"] = json.dumps(table, ensure_ascii=False)
    return SourceItem(
        source_id=source_id,
        release=release,
        item_id=item_id,
        kind_hint="signal",
        text=_own_text(signal.comment),
        heading_path=[],
        attributes=attributes,
        links=[SourceLink(
            type="part_of",
            target_id=message_id,
            role="",
            context=[],
        )],
        file=relative,
        sha256=digest,
    )
