"""ARXML reader: refusals, the identity index, and reference classification.

Implements §3, §4.1 steps 1, 2, 3, and 5, §4.2, and §4.3 of
docs/spec-arxml-reader.md. The reference record carries the enclosing
instance-reference tag; collapsing one into a single link, and building
SourceItem records, are later cards.
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
    otherwise the `PACKAGE-REF` text.
    """

    package: str
    label: str
    is_default: bool
    is_global: bool
    base_path: str


@dataclass(frozen=True)
class ArxmlReference:
    """One DEST-bearing element after §4.3 classification.

    `base` is the BASE attribute, or None when the reference names none.
    `target` is the absolute path, or None when the reference has no text
    or no matching base. `iref` is the enclosing instance-reference tag, if any.
    """

    file: str
    owner: str
    role: str
    dest: str
    text: str
    base: str | None
    iref: str | None
    category: str
    target: str | None


@dataclass(frozen=True)
class _SeenReference:
    """A DEST element collected in pass 1, before classification."""

    file: str
    owner: str
    role: str
    dest: str
    text: str
    base: str | None
    packages: tuple[str, ...]
    iref: str | None


# §4.3. The conservation sum names each one.
_CATEGORIES = (
    "resolved",
    "dest_mismatch",
    "unresolved_definition",
    "dangling",
    "unresolvable_relative",
    "empty",
)
# Report entries. Each list is capped; the category count is the total (§8).
_ENTRY_CATEGORIES = (
    "dest_mismatch",
    "dangling",
    "unresolvable_relative",
    "empty",
)
_ENTRY_LIMIT = 50


def read_arxml(
    paths: Sequence[str | Path],
    source_id: str,
) -> tuple[dict[str, str], dict]:
    """Index every identifiable element in an AUTOSAR 4.x file set.

    Files are read in sorted path order. The index maps each absolute path
    to the tag of its first occurrence. `source_id` is the caller-supplied
    name of the set; it is recorded on the report and is not read from the files.

    Returns `(index, report)`. The report counts every reference and classifies
    it into exactly one §4.3 category. A refusal raises `ArxmlError` and
    returns nothing.
    """
    files = sorted((Path(path) for path in paths), key=lambda path: path.as_posix())
    index: dict[str, str] = {}
    bases: list[ReferenceBase] = []
    seen: list[_SeenReference] = []
    for path in files:
        root = _root(path)
        _walk(root, "", "", [], None, index, bases, seen, path.as_posix())

    packages = sum(tag == "AR-PACKAGE" for tag in index.values())
    if len(index) - packages == 0:
        named = ", ".join(path.as_posix() for path in files) or source_id
        raise ArxmlError(f"vacuous: {named}")

    by_package: dict[str, list[ReferenceBase]] = {}
    for base in bases:
        by_package.setdefault(base.package, []).append(base)
    references = tuple(
        _classify(ref, index, by_package) for ref in seen
    )
    counts = {name: 0 for name in _CATEGORIES}
    entries: dict[str, list[ArxmlReference]] = {name: [] for name in _ENTRY_CATEGORIES}
    for ref in references:
        counts[ref.category] += 1
        bucket = entries.get(ref.category)
        if bucket is not None and len(bucket) < _ENTRY_LIMIT:
            bucket.append(ref)
    _conserve(source_id, len(references), counts)

    report = {
        "source_id": source_id,
        "files": len(files),
        "identifiables": len(index),
        "packages": packages,
        "references_seen": len(references),
        **counts,
        "base_declarations": len(bases),
        "bases": bases,
        "references": references,
        "entries": {name: tuple(entries[name]) for name in _ENTRY_CATEGORIES},
    }
    return index, report


def _conserve(source_id: str, seen: int, counts: dict[str, int]) -> None:
    """Every recorded reference is in exactly one category.

    The six terms are written out so that dropping one fails here.
    """
    total = (
        counts["resolved"]
        + counts["dest_mismatch"]
        + counts["unresolved_definition"]
        + counts["dangling"]
        + counts["unresolvable_relative"]
        + counts["empty"]
    )
    assert total == seen, (
        "conservation failed: "
        f"source_id={source_id} references_seen={seen} n={seen} "
        f"resolved={counts['resolved']} "
        f"dest_mismatch={counts['dest_mismatch']} "
        f"unresolved_definition={counts['unresolved_definition']} "
        f"dangling={counts['dangling']} "
        f"unresolvable_relative={counts['unresolvable_relative']} "
        f"empty={counts['empty']} "
        f"sum={total}"
    )


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
    owner: str,
    packages: list[str],
    iref: str | None,
    index: dict[str, str],
    bases: list[ReferenceBase],
    seen: list[_SeenReference],
    file_name: str,
) -> None:
    """Depth-first. Steps 1, 2, 3, and 5. Children of an -IREF keep its tag."""
    local = _local(element.tag)
    short = _short_name(element)
    if short is not None:
        path = f"{path}/{short}"
        # First occurrence wins. A later element at this path is still walked
        # so its children are indexed (§4.4, applied when a path repeats).
        if path not in index:
            index[path] = local
        owner = path
        if local == "AR-PACKAGE":
            packages = [*packages, path]
    if local == "AR-PACKAGE":
        for child in element:
            if _local(child.tag) == "REFERENCE-BASES":
                bases.extend(_reference_bases(path, child))
    dest = _attribute(element, "DEST")
    if dest is not None:
        seen.append(_SeenReference(
            file=file_name,
            owner=owner,
            role=local,
            dest=dest,
            text=(element.text or "").strip(),
            base=_attribute(element, "BASE"),
            packages=tuple(packages),
            iref=iref,
        ))
    child_iref = local if local.endswith("-IREF") else iref
    for child in element:
        if local == "AR-PACKAGE" and _local(child.tag) == "REFERENCE-BASES":
            continue
        _walk(
            child, path, owner, packages, child_iref,
            index, bases, seen, file_name,
        )


def _classify(
    seen: _SeenReference,
    index: dict[str, str],
    by_package: dict[str, list[ReferenceBase]],
) -> ArxmlReference:
    """Pass 2. Exactly one §4.3 category."""
    category, target = _category(seen, index, by_package)
    return ArxmlReference(
        file=seen.file,
        owner=seen.owner,
        role=seen.role,
        dest=seen.dest,
        text=seen.text,
        base=seen.base,
        iref=seen.iref,
        category=category,
        target=target,
    )


def _category(
    seen: _SeenReference,
    index: dict[str, str],
    by_package: dict[str, list[ReferenceBase]],
) -> tuple[str, str | None]:
    if seen.text == "":
        return "empty", None
    if seen.text.startswith("/"):
        target = seen.text
    else:
        base = _matching_base(seen, by_package)
        if base is None:
            return "unresolvable_relative", None
        target = f"{base.base_path}/{seen.text}"
    # A definition path may coincide with a value container. It may only
    # resolve to an item whose tag ends in -DEF (§4.3).
    tag = index.get(target)
    if seen.dest.startswith("ECUC-") and seen.dest.endswith("-DEF"):
        if tag is not None and tag.endswith("-DEF"):
            if tag == seen.dest:
                return "resolved", target
            return "dest_mismatch", target
        return "unresolved_definition", target
    if tag is None:
        return "dangling", target
    if tag == seen.dest:
        return "resolved", target
    return "dest_mismatch", target


def _matching_base(
    seen: _SeenReference,
    by_package: dict[str, list[ReferenceBase]],
) -> ReferenceBase | None:
    """Innermost package first. An outer base applies only when it is global."""
    for depth, package in enumerate(reversed(seen.packages)):
        outer = depth > 0
        for base in by_package.get(package, ()):
            if outer and not base.is_global:
                continue
            if seen.base is not None:
                if base.label == seen.base:
                    return base
            elif base.is_default:
                return base
    return None


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


def _attribute(element: ET.Element, name: str) -> str | None:
    """Stripped value of an attribute, compared without its namespace."""
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value.strip()
    return None


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
