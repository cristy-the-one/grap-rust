#!/usr/bin/env python3
"""Print an ARXML inventory: tags, DEST values, roles, iref shapes, and counts.

Every figure is printed with ``n`` (the rows behind it) and ``documents``
(the files in the set). A distribution with no rows prints ``n/a``.

Short names, paths, and values are never printed. A tag name or a ``DEST``
value is printed only when it matches ``^[A-Z][A-Z0-9-]*$``. Anything else
is counted under the literal bucket ``<non-standard>``. A role is the tag of
the element that carries ``DEST``, not a path segment. An instance-reference
shape is the tags of that element's children, in document order, joined by
commas.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

# AUTOSAR's upper-hyphen form. Any other tag or DEST is counted, not printed.
_STANDARD_NAME = re.compile(r"^[A-Z][A-Z0-9-]*$")
_NON_STANDARD = "<non-standard>"


class InventoryError(Exception):
    """The file set could not be read. The message names the reason only."""


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _publish(name: str) -> str:
    """Return the name when it is upper-hyphen, otherwise the shared bucket."""
    if _STANDARD_NAME.fullmatch(name):
        return name
    return _NON_STANDARD


def _dest(element: ET.Element) -> str | None:
    found = None
    for key, value in element.attrib.items():
        if _local(key) == "DEST":
            found = value
    return found


def _count(paths: list[Path]):
    tags: Counter[str] = Counter()
    dests: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    for path in paths:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            raise InventoryError("not-xml")
        except OSError:
            raise InventoryError("unreadable")
        for element in root.iter():
            tag = _local(element.tag)
            tags[_publish(tag)] += 1
            dest = _dest(element)
            if dest is not None:
                dests[_publish(dest)] += 1
                roles[_publish(tag)] += 1
            if tag.endswith("-IREF"):
                children = [_publish(_local(child.tag)) for child in list(element)]
                shape = ",".join(children) if children else _NON_STANDARD
                shapes[shape] += 1
    return tags, dests, roles, shapes


def _lines(kind: str, counts: Counter[str], documents: int) -> list[str]:
    total = sum(counts.values())
    if total == 0:
        return [f"{kind} n/a n=0 documents={documents}"]
    return [
        f"{kind} {name if name else _NON_STANDARD} count={counts[name]} n={total} documents={documents}"
        for name in sorted(counts)
    ]


def render(paths: list[Path]) -> str:
    """Return the inventory for this file set. The text holds no names or values."""
    files = [Path(path) for path in paths]
    tags, dests, roles, shapes = _count(files)
    documents = len(files)
    lines = [f"documents n={documents}"]
    lines.extend(_lines("tag", tags, documents))
    lines.extend(_lines("dest", dests, documents))
    lines.extend(_lines("role", roles, documents))
    lines.extend(_lines("iref_shape", shapes, documents))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Print ARXML tag, DEST, role, and instance-reference shape counts. "
            "No short names, paths, or values."
        )
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(render(args.paths))
    except InventoryError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
