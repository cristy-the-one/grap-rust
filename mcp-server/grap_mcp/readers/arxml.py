"""ARXML reader: refusals and the identity index.

Implements §3 and §4.1 steps 1, 2, and 5 of docs/spec-arxml-reader.md.
References, instance references, and SourceItem construction are later cards.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# The only dialect this reader accepts. Any other root is refused.
_AUTOSAR_NAMESPACE = "http://autosar.org/schema/r4.0"
_ROOT_TAG = f"{{{_AUTOSAR_NAMESPACE}}}AUTOSAR"
# "Larger than 500 MB" — checked before the file is read.
_MAX_BYTES = 500 * 1024 * 1024


class ArxmlError(Exception):
    """The whole file set was refused. The message names the reason and the file."""


@dataclass(frozen=True)
class ReferenceBase:
    """One reference base declared on a package.

    `base_path` is the declaring package when `BASE-IS-THIS-PACKAGE` is true,
    otherwise the `PACKAGE-REF` text. Resolution is a later card.
    """

    package: str
    label: str
    is_default: bool
    is_global: bool
    base_path: str


def read_arxml(
    paths: Sequence[str | Path],
    source_id: str,
) -> tuple[dict[str, str], dict]:
    """Index every identifiable element in an AUTOSAR 4.x file set.

    Files are read in sorted path order. The index maps each absolute path
    to the tag of its first occurrence. `source_id` is the caller-supplied
    name of the set; it is recorded on the report and is not read from the files.

    Returns `(index, report)`. A refusal raises `ArxmlError` and returns nothing.
    """
    files = sorted((Path(path) for path in paths), key=lambda path: path.as_posix())
    index: dict[str, str] = {}
    bases: list[ReferenceBase] = []
    for path in files:
        root = _root(path)
        # Carried for step 1 so a later card can resolve relative references
        # from the enclosing packages. Nothing in this card reads it back.
        _walk(root, "", [], index, bases)

    packages = sum(tag == "AR-PACKAGE" for tag in index.values())
    if len(index) - packages == 0:
        named = ", ".join(path.as_posix() for path in files) or source_id
        raise ArxmlError(f"vacuous: {named}")

    report = {
        "source_id": source_id,
        "files": len(files),
        "identifiables": len(index),
        "packages": packages,
        "base_declarations": len(bases),
        "bases": bases,
    }
    return index, report


def _root(path: Path) -> ET.Element:
    """Return the document element, or refuse the file."""
    if path.stat().st_size > _MAX_BYTES:
        raise ArxmlError(f"too-large: {path.as_posix()}")
    data = path.read_bytes()
    # Before the parser: ElementTree would otherwise see a DTD.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ArxmlError(f"dtd-not-allowed: {path.as_posix()}")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ArxmlError(f"not-xml: {path.as_posix()}") from None
    if root.tag != _ROOT_TAG:
        raise ArxmlError(f"unsupported-dialect: {path.as_posix()}: {root.tag}")
    return root


def _walk(
    element: ET.Element,
    path: str,
    packages: list[str],
    index: dict[str, str],
    bases: list[ReferenceBase],
) -> None:
    """Depth-first. Steps 1, 2, and 5. Steps 3 and 4 belong to a later card."""
    local = _local(element.tag)
    short = _short_name(element)
    if short is not None:
        path = f"{path}/{short}"
        # First occurrence wins. A later element at this path is still walked
        # so its children are indexed (§4.4, applied when a path repeats).
        if path not in index:
            index[path] = local
        if local == "AR-PACKAGE":
            packages = [*packages, path]
    if local == "AR-PACKAGE":
        for child in element:
            if _local(child.tag) == "REFERENCE-BASES":
                bases.extend(_reference_bases(path, child))
    for child in element:
        if local == "AR-PACKAGE" and _local(child.tag) == "REFERENCE-BASES":
            continue
        _walk(child, path, packages, index, bases)


def _reference_bases(package: str, bases_element: ET.Element) -> list[ReferenceBase]:
    recorded = []
    for child in bases_element:
        if _local(child.tag) != "REFERENCE-BASE":
            continue
        this_package = _text(child, "BASE-IS-THIS-PACKAGE") == "true"
        recorded.append(ReferenceBase(
            package=package,
            label=_text(child, "SHORT-LABEL"),
            is_default=_text(child, "IS-DEFAULT") == "true",
            is_global=_text(child, "IS-GLOBAL") == "true",
            base_path=package if this_package else _text(child, "PACKAGE-REF"),
        ))
    return recorded


def _short_name(element: ET.Element) -> str | None:
    """Stripped text of the first direct SHORT-NAME, or None when there is none."""
    child = _child(element, "SHORT-NAME")
    if child is None:
        return None
    return (child.text or "").strip()


def _text(element: ET.Element, name: str) -> str:
    child = _child(element, name)
    if child is None:
        return ""
    return (child.text or "").strip()


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag
