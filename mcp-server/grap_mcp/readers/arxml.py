"""ARXML reader: refusals, the identity index, references, and items.

Implements §3, §4.1 steps 1, 2, 3, and 5, §4.2, §4.3, §4.4, §4.5, §5, and §6
of docs/spec-arxml-reader.md. `read_arxml` returns `(items, report)`.
Release text on an item comes from §7. An -IREF with exactly one TARGET-
child is one refers_to link (§4.5). An ECUC-CONTAINER-VALUE also carries
param.* attributes. A VALUE-REF under its REFERENCE-VALUES takes its link
role from the DEFINITION-REF (§6). A repeated path is recorded under
split_elements or path_conflicts (§4.4). Two packages at the same path are
merged and not recorded.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from grap_mcp.readers import SourceItem, SourceLink

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
    # §6. The SourceLink role when it differs from the element tag.
    link_role: str | None = None


@dataclass(frozen=True)
class MalformedIref:
    """An -IREF that does not have exactly one TARGET- child (§4.5)."""

    file: str
    owner: str
    role: str


@dataclass(frozen=True)
class SplitElement:
    """Same path and tag, not a package. The first occurrence keeps its text."""

    path: str
    tag: str
    first_file: str
    other_file: str


@dataclass(frozen=True)
class PathConflict:
    """Same path, different tags. The first occurrence keeps the index tag."""

    path: str
    first_file: str
    first_tag: str
    other_file: str
    other_tag: str


@dataclass(frozen=True)
class _IrefGroup:
    """One -IREF element, in document order. `role` is its tag."""

    file: str
    owner: str
    role: str


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
    iref_group: int | None
    # §6. Set only for a VALUE-REF whose link role is a definition segment.
    link_role: str | None = None


@dataclass(frozen=True)
class _RawItem:
    """The first occurrence of one identifiable element, before pass 2."""

    path: str
    tag: str
    short_name: str
    parent: str
    heading_path: tuple[str, ...]
    element: ET.Element
    file: str
    release: str | None
    sha256: str


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
# §4.3 categories that emit a refers_to link. The other two are report-only.
_LINK_CATEGORIES = frozenset({
    "resolved",
    "dest_mismatch",
    "unresolved_definition",
    "dangling",
})
_PLACEHOLDER_CATEGORIES = frozenset({
    "unresolved_definition",
    "dangling",
})
# §5 flattening limits. Hitting either one sets attributes["_truncated"].
_MAX_ATTRIBUTE_DEPTH = 10
_MAX_ATTRIBUTES = 300
_LANGUAGE_TAG = re.compile(r"^L-\d+\Z")
_FIVE_DIGIT_SCHEMA = re.compile(r"AUTOSAR_(\d{5})\.xsd\Z")
_HYPHEN_SCHEMA = re.compile(r"AUTOSAR_(\d+(?:-\d+)+)\.xsd\Z")
# §7. Five-digit schema ids. Anything else in that shape is null.
_RELEASE_BY_FIVE = {
    "00042": "R17-03",
    "00043": "R17-10",
    "00044": "4.3.1",
    "00045": "R18-03",
    "00046": "4.4.0",
    "00047": "R19-03",
    "00048": "4.5.0",
    "00049": "R20-11",
    "00050": "R21-11",
    "00051": "R22-11",
    "00052": "R23-11",
    "00053": "R24-11",
    "00054": "R25-11",
}
# §5. An unlisted tag is "element". Suffix rows are applied after this table.
_KIND_BY_TAG = {
    "AR-PACKAGE": "package",
    "ECU-INSTANCE": "ecu",
    "CAN-CLUSTER": "network",
    "CAN-PHYSICAL-CHANNEL": "network",
    "CAN-COMMUNICATION-CONTROLLER": "network",
    "LIN-CLUSTER": "network",
    "LIN-PHYSICAL-CHANNEL": "network",
    "LIN-COMMUNICATION-CONTROLLER": "network",
    "FLEXRAY-CLUSTER": "network",
    "FLEXRAY-PHYSICAL-CHANNEL": "network",
    "FLEXRAY-COMMUNICATION-CONTROLLER": "network",
    "ETHERNET-CLUSTER": "network",
    "ETHERNET-PHYSICAL-CHANNEL": "network",
    "ETHERNET-COMMUNICATION-CONTROLLER": "network",
    "CAN-FRAME": "message",
    "LIN-UNCONDITIONAL-FRAME": "message",
    "FLEXRAY-FRAME": "message",
    "I-SIGNAL-I-PDU": "pdu",
    "MULTIPLEXED-I-PDU": "pdu",
    "CONTAINER-I-PDU": "pdu",
    "SECURED-I-PDU": "pdu",
    "NM-PDU": "pdu",
    "N-PDU": "pdu",
    "DCM-I-PDU": "pdu",
    "GENERAL-PURPOSE-PDU": "pdu",
    "GENERAL-PURPOSE-I-PDU": "pdu",
    "USER-DEFINED-PDU": "pdu",
    "I-SIGNAL": "signal",
    "SYSTEM-SIGNAL": "signal",
    "I-SIGNAL-GROUP": "signal",
    "SYSTEM-SIGNAL-GROUP": "signal",
    "I-SIGNAL-TO-I-PDU-MAPPING": "relation",
    "PDU-TO-FRAME-MAPPING": "relation",
    "CAN-FRAME-TRIGGERING": "relation",
    "PDU-TRIGGERING": "relation",
    "I-SIGNAL-TRIGGERING": "relation",
    "ASSEMBLY-SW-CONNECTOR": "relation",
    "DELEGATION-SW-CONNECTOR": "relation",
    "SENDER-RECEIVER-TO-SIGNAL-MAPPING": "relation",
    "SWC-TO-ECU-MAPPING": "relation",
    "COMPU-METHOD": "scaling",
    "UNIT": "unit",
    "SW-BASE-TYPE": "datatype",
    "APPLICATION-PRIMITIVE-DATA-TYPE": "datatype",
    "APPLICATION-RECORD-DATA-TYPE": "datatype",
    "APPLICATION-ARRAY-DATA-TYPE": "datatype",
    "IMPLEMENTATION-DATA-TYPE": "datatype",
    "DATA-CONSTR": "datatype",
    "SYSTEM": "system",
    "SW-COMPONENT-PROTOTYPE": "component",
    "P-PORT-PROTOTYPE": "port",
    "R-PORT-PROTOTYPE": "port",
    "PR-PORT-PROTOTYPE": "port",
    "VARIABLE-DATA-PROTOTYPE": "data_element",
    "PARAMETER-DATA-PROTOTYPE": "data_element",
    "ARGUMENT-DATA-PROTOTYPE": "data_element",
    "CLIENT-SERVER-OPERATION": "data_element",
    "SWC-INTERNAL-BEHAVIOR": "behavior",
    "RUNNABLE-ENTITY": "behavior",
    "MODE-DECLARATION-GROUP": "mode",
    "MODE-DECLARATION": "mode",
    "ECUC-MODULE-CONFIGURATION-VALUES": "config_module",
    "ECUC-VALUE-COLLECTION": "config_module",
    "ECUC-CONTAINER-VALUE": "config_container",
}


def read_arxml(
    paths: Sequence[str | Path],
    source_id: str,
) -> tuple[tuple[SourceItem, ...], dict]:
    """Read an AUTOSAR 4.x file set into source items.

    Files are read in sorted path order. `source_id` is the caller-supplied
    name of the set; it is recorded on the report and is not read from the files.

    Returns `(items, report)`. Items are one SourceItem per identifiable, in
    first-occurrence order, then one placeholder per distinct dangling or
    undefined target. The report counts every reference. Each list in it holds
    at most 50 entries; the counts are the totals. `malformed_irefs` counts
    -IREF elements that do not have exactly one TARGET- child. An
    ECUC-CONTAINER-VALUE also gains §6 parameter attributes, and a qualifying
    VALUE-REF link uses the definition's last path segment as its role. A
    later element at a path already in the index is a split when the tag
    matches and is not a package, and a path conflict when the tag differs
    (§4.4). Two packages at one path are merged and not recorded. A refusal
    raises `ArxmlError` and returns nothing.
    """
    files = sorted((Path(path) for path in paths), key=lambda path: path.as_posix())
    index: dict[str, str] = {}
    first_files: dict[str, str] = {}
    bases: list[ReferenceBase] = []
    seen: list[_SeenReference] = []
    raw_items: list[_RawItem] = []
    iref_groups: list[_IrefGroup] = []
    splits: list[SplitElement] = []
    conflicts: list[PathConflict] = []
    for path in files:
        root, digest = _root(path)
        _walk(
            root, "", "", [], None, index, bases, seen, path.as_posix(),
            raw_items, _release(root), digest, None, "", iref_groups,
            first_files, splits, conflicts,
        )

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
    dest_elements = sum(_dest_elements(path) for path in files)
    _conserve(source_id, len(references), counts, dest_elements)
    assert len(raw_items) == len(index), (
        "index and items diverged: "
        f"identifiables={len(index)} items={len(raw_items)}"
    )
    group_ids = [ref.iref_group for ref in seen]
    items, tag_counts, truncated, variations, malformed = _build_items(
        source_id, raw_items, references, iref_groups, group_ids,
    )
    listed = {name: tuple(entries[name]) for name in _ENTRY_CATEGORIES}
    listed["malformed_irefs"] = tuple(malformed[:_ENTRY_LIMIT])
    listed["split_elements"] = tuple(splits[:_ENTRY_LIMIT])
    listed["path_conflicts"] = tuple(conflicts[:_ENTRY_LIMIT])

    report = {
        "source_id": source_id,
        "files": len(files),
        "identifiables": len(index),
        "packages": packages,
        "references_seen": len(references),
        **counts,
        "base_declarations": len(bases),
        "split_elements": len(splits),
        "path_conflicts": len(conflicts),
        "bases": tuple(bases[:_ENTRY_LIMIT]),
        "references": references[:_ENTRY_LIMIT],
        "entries": listed,
        "tag_counts": tag_counts,
        "attributes_truncated": truncated,
        "variation_points": variations,
        "malformed_irefs": len(malformed),
    }
    return tuple(items), report


def _conserve(
    source_id: str,
    seen: int,
    counts: dict[str, int],
    dest_elements: int,
) -> None:
    """Every recorded reference is in exactly one category, and none were missed.

    The six terms are written out so that dropping one fails here. `dest_elements`
    is counted by a separate pass, so a reference the collector never recorded
    fails here too. This raises `ArxmlError`; it is not an assert.
    """
    total = (
        counts["resolved"]
        + counts["dest_mismatch"]
        + counts["unresolved_definition"]
        + counts["dangling"]
        + counts["unresolvable_relative"]
        + counts["empty"]
    )
    if total != seen or dest_elements != seen:
        raise ArxmlError(
            "conservation failed: "
            f"source_id={source_id} references_seen={seen} n={seen} "
            f"resolved={counts['resolved']} "
            f"dest_mismatch={counts['dest_mismatch']} "
            f"unresolved_definition={counts['unresolved_definition']} "
            f"dangling={counts['dangling']} "
            f"unresolvable_relative={counts['unresolvable_relative']} "
            f"empty={counts['empty']} "
            f"sum={total} "
            f"dest_elements={dest_elements} n_dest={dest_elements}"
        )


def _dest_elements(path: Path) -> int:
    """DEST attributes in one file, not walking into REFERENCE-BASES.

    A separate parse from the collector, so a missed reference changes this
    count and not `references_seen`.
    """
    root = ET.fromstring(path.read_bytes())
    return _count_dest(root)


def _count_dest(element: ET.Element) -> int:
    if _local(element.tag) == "REFERENCE-BASES":
        return 0
    found = 1 if _attribute(element, "DEST") is not None else 0
    for child in element:
        found += _count_dest(child)
    return found


def _root(path: Path) -> tuple[ET.Element, str]:
    """Return the document element and the sha256 of its bytes, or refuse."""
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
    return root, hashlib.sha256(data).hexdigest()


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
    raw_items: list[_RawItem],
    release: str | None,
    digest: str,
    iref_group: int | None,
    iref_owner: str,
    iref_groups: list[_IrefGroup],
    first_files: dict[str, str],
    splits: list[SplitElement],
    conflicts: list[PathConflict],
    config_role: str | None = None,
    config_owner: str = "",
) -> None:
    """Depth-first. Steps 1, 2, 3, and 5. Children of an -IREF keep its tag.

    A DEST descendant is a member of that -IREF only while the owner is still
    the owner at the -IREF. A nested identifiable keeps its own references.
    """
    local = _local(element.tag)
    short = _short_name(element)
    if short is not None:
        parent = owner
        heading = tuple(package.rsplit("/", 1)[-1] for package in packages)
        path = f"{path}/{short}"
        # First occurrence wins (§4.4). A later element is still walked, so
        # its children are indexed and its references are kept.
        if path not in index:
            index[path] = local
            first_files[path] = file_name
            raw_items.append(_RawItem(
                path=path,
                tag=local,
                short_name=short,
                parent=parent,
                heading_path=heading,
                element=element,
                file=file_name,
                release=release,
                sha256=digest,
            ))
        else:
            _note_repeat(
                path, local, index[path], first_files[path], file_name,
                splits, conflicts,
            )
        owner = path
        if local == "AR-PACKAGE":
            packages = [*packages, path]
    if local == "AR-PACKAGE":
        for child in element:
            if _local(child.tag) == "REFERENCE-BASES":
                bases.extend(_reference_bases(path, child))
    dest = _attribute(element, "DEST")
    if dest is not None:
        link_role = None
        if (
            local == "VALUE-REF"
            and config_role is not None
            and owner == config_owner
        ):
            link_role = config_role
        seen.append(_SeenReference(
            file=file_name,
            owner=owner,
            role=local,
            dest=dest,
            text=(element.text or "").strip(),
            base=_attribute(element, "BASE"),
            packages=tuple(packages),
            iref=iref,
            iref_group=(
                iref_group if iref_group is not None and owner == iref_owner else None
            ),
            link_role=link_role,
        ))
    if local.endswith("-IREF"):
        child_iref = local
        child_group = len(iref_groups)
        child_owner = owner
        iref_groups.append(_IrefGroup(file=file_name, owner=owner, role=local))
    else:
        child_iref = iref
        child_group = iref_group
        child_owner = iref_owner
    for child in element:
        if local == "AR-PACKAGE" and _local(child.tag) == "REFERENCE-BASES":
            continue
        child_config_role = config_role
        child_config_owner = config_owner
        if (
            local == "REFERENCE-VALUES"
            and index.get(owner) == "ECUC-CONTAINER-VALUE"
        ):
            child_config_role = _reference_value_role(child)
            child_config_owner = owner
        _walk(
            child, path, owner, packages, child_iref,
            index, bases, seen, file_name, raw_items, release, digest,
            child_group, child_owner, iref_groups,
            first_files, splits, conflicts,
            child_config_role, child_config_owner,
        )


def _note_repeat(
    path: str,
    tag: str,
    first_tag: str,
    first_file: str,
    other_file: str,
    splits: list[SplitElement],
    conflicts: list[PathConflict],
) -> None:
    """§4.4. Two packages at one path merge. Any other repeat is recorded."""
    if tag == "AR-PACKAGE" and first_tag == "AR-PACKAGE":
        return
    if tag == first_tag:
        splits.append(SplitElement(
            path=path,
            tag=tag,
            first_file=first_file,
            other_file=other_file,
        ))
        return
    conflicts.append(PathConflict(
        path=path,
        first_file=first_file,
        first_tag=first_tag,
        other_file=other_file,
        other_tag=tag,
    ))


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
        link_role=seen.link_role,
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


def _build_items(
    source_id: str,
    raw_items: Sequence[_RawItem],
    references: Sequence[ArxmlReference],
    iref_groups: Sequence[_IrefGroup],
    group_ids: Sequence[int | None],
) -> tuple[list[SourceItem], dict[str, int], int, int, list[MalformedIref]]:
    """§5 items, with §4.5 collapsing an -IREF into one link."""
    collapsed, malformed = _iref_links(references, iref_groups, group_ids)
    refers: dict[str, list[SourceLink]] = {}
    placeholder_dest: dict[str, str] = {}
    placeholder_order: list[str] = []
    emitted_groups: set[int] = set()
    for ref, gid in zip(references, group_ids, strict=True):
        # Placeholders follow classification, including children of a collapsed
        # instance reference. The collapse changes the link, not the count.
        if (
            ref.category in _PLACEHOLDER_CATEGORIES
            and ref.target is not None
            and ref.target not in placeholder_dest
        ):
            placeholder_dest[ref.target] = ref.dest
            placeholder_order.append(ref.target)
        if gid is not None and gid in collapsed:
            if gid not in emitted_groups:
                emitted_groups.add(gid)
                link = collapsed[gid]
                if link is not None:
                    refers.setdefault(ref.owner, []).append(link)
            continue
        if ref.category not in _LINK_CATEGORIES:
            continue
        assert ref.target is not None, ref
        refers.setdefault(ref.owner, []).append(SourceLink(
            type="refers_to",
            target_id=ref.target,
            role=ref.role if ref.link_role is None else ref.link_role,
            context=[],
        ))

    items: list[SourceItem] = []
    tag_counts: dict[str, int] = {}
    truncated = 0
    variations = 0
    for raw in raw_items:
        attributes, hit_limit, has_variation = _attributes(raw.element, raw.tag, raw.short_name)
        if raw.tag == "ECUC-CONTAINER-VALUE":
            for key, value in _config_params(raw.element):
                attributes.setdefault(key, value)
        if hit_limit:
            truncated += 1
        if has_variation:
            variations += 1
        tag_counts[raw.tag] = tag_counts.get(raw.tag, 0) + 1
        links: list[SourceLink] = []
        if raw.parent:
            links.append(SourceLink(
                type="part_of",
                target_id=raw.parent,
                role="",
                context=[],
            ))
        links.extend(refers.get(raw.path, ()))
        items.append(SourceItem(
            source_id=source_id,
            release=raw.release,
            item_id=raw.path,
            kind_hint=_kind_hint(raw.tag),
            text=_item_text(raw.element, raw.short_name),
            heading_path=list(raw.heading_path),
            attributes=attributes,
            links=links,
            file=raw.file,
            sha256=raw.sha256,
        ))
    for target in placeholder_order:
        items.append(SourceItem(
            source_id=source_id,
            release=None,
            item_id=target,
            kind_hint="unresolved",
            text="",
            heading_path=[],
            attributes={"expected_kind": placeholder_dest[target]},
            links=[],
            file="",
            sha256="",
        ))
    return items, tag_counts, truncated, variations, malformed


def _iref_links(
    references: Sequence[ArxmlReference],
    groups: Sequence[_IrefGroup],
    group_ids: Sequence[int | None],
) -> tuple[dict[int, SourceLink | None], list[MalformedIref]]:
    """One link per -IREF that has exactly one TARGET- child.

    `context` is the paths of the other children, in document order. A child
    with no path (empty or unresolvable) is left out of the context. Any other
    -IREF is malformed, and the caller emits its children as ordinary links.
    A collapsed group whose TARGET- child has no path contributes no link.
    """
    members: dict[int, list[ArxmlReference]] = {gid: [] for gid in range(len(groups))}
    for ref, gid in zip(references, group_ids, strict=True):
        if gid is not None:
            members[gid].append(ref)
    collapsed: dict[int, SourceLink | None] = {}
    malformed: list[MalformedIref] = []
    for gid, group in enumerate(groups):
        targets = [ref for ref in members[gid] if ref.role.startswith("TARGET-")]
        if len(targets) != 1:
            malformed.append(MalformedIref(
                file=group.file, owner=group.owner, role=group.role,
            ))
            continue
        target = targets[0]
        if target.target is None:
            collapsed[gid] = None
            continue
        context = [
            ref.target
            for ref in members[gid]
            if ref is not target and ref.target is not None
        ]
        collapsed[gid] = SourceLink(
            type="refers_to",
            target_id=target.target,
            role=group.role,
            context=context,
        )
    return collapsed, malformed


def _kind_hint(tag: str) -> str:
    kind = _KIND_BY_TAG.get(tag)
    if kind is not None:
        return kind
    if tag.endswith("-SW-COMPONENT-TYPE"):
        return "component"
    if tag.endswith("-INTERFACE"):
        return "interface"
    if tag.endswith("-EVENT"):
        return "behavior"
    return "element"


def _item_text(element: ET.Element, short_name: str) -> str:
    """First non-empty of DESC, LONG-NAME, SHORT-NAME. EN wins inside one."""
    for name in ("DESC", "LONG-NAME"):
        child = _child(element, name)
        if child is None:
            continue
        text = _language_text(child)
        if text:
            return text
    return short_name


def _language_text(element: ET.Element) -> str:
    paragraphs = [
        child for child in element
        if _attribute(child, "L") is not None or _LANGUAGE_TAG.fullmatch(_local(child.tag))
    ]
    if not paragraphs:
        return (element.text or "").strip()
    chosen = paragraphs[0]
    for para in paragraphs:
        if _attribute(para, "L") == "EN":
            chosen = para
            break
    return (chosen.text or "").strip()


def _attributes(
    element: ET.Element,
    tag: str,
    short_name: str,
) -> tuple[dict[str, str], bool, bool]:
    """Flatten leaf text. Stop at nested items, references, and reference bases."""
    leaves: list[tuple[str, str]] = []
    truncated = False
    has_variation = False

    def walk(node: ET.Element, tags: list[str]) -> None:
        nonlocal truncated, has_variation
        for child in node:
            local = _local(child.tag)
            if local == "REFERENCE-BASES" or _attribute(child, "DEST") is not None:
                continue
            if local == "SHORT-NAME" or _short_name(child) is not None:
                continue
            if local == "VARIATION-POINT":
                has_variation = True
            child_tags = [*tags, local]
            if len(child_tags) > _MAX_ATTRIBUTE_DEPTH:
                truncated = True
                continue
            if len(child) == 0:
                text = (child.text or "").strip()
                if text:
                    leaves.append((".".join(child_tags), text))
                continue
            walk(child, child_tags)

    walk(element, [])
    numbered = _number_repeats(leaves)
    if len(numbered) > _MAX_ATTRIBUTES:
        truncated = True
        numbered = numbered[:_MAX_ATTRIBUTES]
    attributes = {"tag": tag, "short_name": short_name}
    for key, value in numbered:
        if key in ("tag", "short_name"):
            continue
        attributes[key] = value
    if truncated:
        attributes["_truncated"] = "true"
    if has_variation:
        attributes["_has_variation_point"] = "true"
    return attributes, truncated, has_variation


def _number_repeats(leaves: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """A repeated key becomes key[0], key[1], … on every occurrence."""
    totals: dict[str, int] = {}
    for key, _value in leaves:
        totals[key] = totals.get(key, 0) + 1
    seen: dict[str, int] = {}
    numbered: list[tuple[str, str]] = []
    for key, value in leaves:
        if totals[key] == 1:
            numbered.append((key, value))
            continue
        index = seen.get(key, 0)
        seen[key] = index + 1
        numbered.append((f"{key}[{index}]", value))
    return numbered


def _reference_value_role(element: ET.Element) -> str | None:
    """Last DEFINITION-REF segment, when this element has both children (§6)."""
    if _child(element, "VALUE-REF") is None:
        return None
    segment = _text(element, "DEFINITION-REF").rsplit("/", 1)[-1]
    if segment == "":
        return None
    return segment


def _config_params(element: ET.Element) -> list[tuple[str, str]]:
    """param.<segment> for each PARAMETER-VALUES child that has both (§6).

    A repeated name becomes param.Name[0], param.Name[1], … on every occurrence.
    """
    found: list[tuple[str, str]] = []
    for values in element:
        if _local(values.tag) != "PARAMETER-VALUES":
            continue
        for child in values:
            if _child(child, "VALUE") is None:
                continue
            segment = _text(child, "DEFINITION-REF").rsplit("/", 1)[-1]
            if segment == "":
                continue
            found.append((f"param.{segment}", _text(child, "VALUE")))
    return _number_repeats(found)


def _release(root: ET.Element) -> str | None:
    """§7. The schema file name, never the namespace or the document name."""
    location = _attribute(root, "schemaLocation")
    if not location:
        return None
    schema_name = None
    for token in location.split():
        name = token.replace("\\", "/").rsplit("/", 1)[-1]
        if name.endswith(".xsd"):
            schema_name = name
            break
    if schema_name is None:
        return None
    five = _FIVE_DIGIT_SCHEMA.fullmatch(schema_name)
    if five:
        return _RELEASE_BY_FIVE.get(five.group(1))
    hyphen = _HYPHEN_SCHEMA.fullmatch(schema_name)
    if hyphen:
        return hyphen.group(1).replace("-", ".")
    return None
