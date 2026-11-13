"""ReqIF reader: one SourceItem per SPEC-OBJECT.

No model calls and no graph writes. Standard-library XML only. A file that
cannot be parsed raises; nothing is returned in its place.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from grap_mcp.readers import SourceItem, SourceLink

_DEFINITION_TAGS = (
    "ATTRIBUTE-DEFINITION-BOOLEAN",
    "ATTRIBUTE-DEFINITION-DATE",
    "ATTRIBUTE-DEFINITION-ENUMERATION",
    "ATTRIBUTE-DEFINITION-INTEGER",
    "ATTRIBUTE-DEFINITION-REAL",
    "ATTRIBUTE-DEFINITION-STRING",
    "ATTRIBUTE-DEFINITION-XHTML",
)
_LITERAL_VALUE_TAGS = (
    "ATTRIBUTE-VALUE-BOOLEAN",
    "ATTRIBUTE-VALUE-DATE",
    "ATTRIBUTE-VALUE-INTEGER",
    "ATTRIBUTE-VALUE-REAL",
    "ATTRIBUTE-VALUE-STRING",
)


class ReqifError(ValueError):
    """The file is not a ReqIF document this reader can return whole."""


def read_reqif(path: str | Path, corpus_root: str | Path | None = None) -> list[SourceItem]:
    """Read `path` into one item per SPEC-OBJECT, in file order.

    `source_id` is the file stem. `item_id` is the SPEC-OBJECT IDENTIFIER.
    An empty identifier uses ``{source_id}#{n}`` with `n` the 0-based index
    in that order. `release` is None: a ReqIF header does not state a
    document release. Attribute keys are snake_case of the attribute
    definition's LONG-NAME. When that name is ``ReqIF.ForeignID``,
    ``attributes["native_id"]`` is the same value. `text` is the ReqIF.Text
    value when that attribute is present, otherwise the only XHTML value,
    otherwise the chapter name. Bytes containing ``<!DOCTYPE`` or
    ``<!ENTITY`` raise ``dtd-not-allowed``.
    """
    file_path = Path(path)
    data = file_path.read_bytes()
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ReqifError(f"ReqIF reader cannot parse {file_path}: dtd-not-allowed")
    root = ET.fromstring(data)
    if _local(root.tag) != "REQ-IF":
        raise ReqifError(f"ReqIF reader cannot parse {file_path}: root is not REQ-IF")

    content = _child(_required(root, "CORE-CONTENT", file_path), "REQ-IF-CONTENT")
    if content is None:
        raise ReqifError(f"ReqIF reader cannot parse {file_path}: missing REQ-IF-CONTENT")

    definitions = _index_definitions(content, file_path)
    enums = _index_enums(content, file_path)
    object_types = _index_named(content, "SPEC-OBJECT-TYPE", file_path)
    relation_types = _index_named(content, "SPEC-RELATION-TYPE", file_path)
    objects = _objects(content, file_path, definitions, enums, object_types)
    by_ident = {obj.identifier: obj for obj in objects if obj.identifier}
    paths = _heading_paths(content, by_ident, file_path)
    links = _links(content, by_ident, relation_types, file_path)

    source_id = file_path.stem
    relative = _relative_file(file_path, corpus_root)
    digest = hashlib.sha256(data).hexdigest()
    return [
        SourceItem(
            source_id=source_id,
            release=None,
            item_id=obj.item_id,
            kind_hint=obj.kind,
            text=obj.text,
            heading_path=list(paths.get(obj.item_id, ())),
            attributes=obj.attributes,
            links=links.get(obj.item_id, []),
            file=relative,
            sha256=digest,
        )
        for obj in objects
    ]


class _Object:
    def __init__(self, identifier, item_id, long_name, kind, text, attributes):
        self.identifier = identifier
        self.item_id = item_id
        self.long_name = long_name
        self.kind = kind
        self.text = text
        self.attributes = attributes


def _objects(content, file_path, definitions, enums, object_types) -> list[_Object]:
    container = _child(content, "SPEC-OBJECTS")
    elements = _children(container, "SPEC-OBJECT") if container is not None else []
    source_id = file_path.stem
    objects: list[_Object] = []
    seen: set[str] = set()
    for index, element in enumerate(elements):
        identifier = (element.get("IDENTIFIER") or "").strip()
        if identifier:
            if identifier in seen:
                raise ReqifError(
                    f"ReqIF reader cannot parse {file_path}: "
                    f"duplicate SPEC-OBJECT IDENTIFIER {identifier!r}"
                )
            seen.add(identifier)
            item_id = identifier
        else:
            item_id = f"{source_id}#{index}"
        type_id = _nested_ref(element, "TYPE", "SPEC-OBJECT-TYPE-REF", file_path)
        if type_id not in object_types:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: "
                f"unresolved SPEC-OBJECT-TYPE {type_id!r}"
            )
        attributes, xhtml_values = _attributes(element, definitions, enums, file_path)
        objects.append(_Object(
            identifier,
            item_id,
            element.get("LONG-NAME") or "",
            _kind(object_types[type_id]),
            _item_text(attributes, xhtml_values),
            attributes,
        ))
    return objects


def _attributes(element, definitions, enums, file_path):
    values = _child(element, "VALUES")
    attributes: dict[str, str] = {}
    xhtml_values: list[str] = []
    if values is None:
        return attributes, xhtml_values
    for value in list(values):
        tag = _local(value.tag)
        if tag in _LITERAL_VALUE_TAGS:
            rendered = value.get("THE-VALUE")
            if rendered is None:
                rendered = ""
        elif tag == "ATTRIBUTE-VALUE-ENUMERATION":
            rendered = _enumeration(value, enums, file_path)
        elif tag == "ATTRIBUTE-VALUE-XHTML":
            rendered = _xhtml(value, file_path)
            xhtml_values.append(rendered)
        else:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: "
                f"unknown attribute value {tag}"
            )
        key, long_name = _attribute_key(value, definitions, file_path)
        if key in attributes:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: duplicate attribute {key!r}"
            )
        attributes[key] = rendered
        if long_name == "ReqIF.ForeignID":
            if "native_id" in attributes:
                raise ReqifError(
                    f"ReqIF reader cannot parse {file_path}: duplicate attribute 'native_id'"
                )
            attributes["native_id"] = rendered
    return attributes, xhtml_values


def _attribute_key(value, definitions, file_path) -> tuple[str, str]:
    definition = _child(value, "DEFINITION")
    if definition is None:
        raise ReqifError(
            f"ReqIF reader cannot parse {file_path}: ATTRIBUTE-VALUE has no DEFINITION"
        )
    ref = ""
    for child in list(definition):
        ref = (child.text or "").strip()
        if ref:
            break
    if not ref or ref not in definitions:
        raise ReqifError(
            f"ReqIF reader cannot parse {file_path}: unresolved attribute definition {ref!r}"
        )
    long_name, identifier = definitions[ref]
    return _snake(long_name or identifier, file_path), long_name


def _enumeration(value, enums, file_path) -> str:
    values = _child(value, "VALUES")
    if values is None:
        return ""
    names = []
    for ref in _children(values, "ENUM-VALUE-REF"):
        ident = (ref.text or "").strip()
        if ident not in enums:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: unresolved ENUM-VALUE-REF {ident!r}"
            )
        names.append(enums[ident])
    return ",".join(names)


def _xhtml(value, file_path) -> str:
    the_value = _child(value, "THE-VALUE")
    if the_value is None:
        return ""
    for node in the_value.iter():
        if _local(node.tag) == "object":
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: "
                "XHTML attribute values with embedded objects"
            )
    return "".join(the_value.itertext())


def _item_text(attributes: dict[str, str], xhtml_values: list[str]) -> str:
    if "req_if_text" in attributes:
        return attributes["req_if_text"]
    if len(xhtml_values) == 1:
        return xhtml_values[0]
    chapter = attributes.get("req_if_chapter_name", "")
    if chapter:
        return chapter
    return ""


def _kind(type_name: str) -> str:
    folded = type_name.casefold()
    if "heading" in folded:
        return "heading"
    if "parameter" in folded:
        return "parameter"
    if "definition" in folded:
        return "definition"
    if "table" in folded:
        return "table"
    return "requirement"


def _heading_paths(content, by_ident, file_path) -> dict[str, list[str]]:
    paths: dict[str, list[str]] = {}
    specifications = _child(content, "SPECIFICATIONS")
    if specifications is None:
        return paths

    def title_of(obj: _Object) -> str:
        chapter = obj.attributes.get("req_if_chapter_name", "")
        if chapter:
            return chapter
        if obj.long_name:
            return obj.long_name
        return obj.item_id

    def walk(node, ancestors: list[str]) -> None:
        box = _child(node, "OBJECT")
        if box is not None:
            ref = _child(box, "SPEC-OBJECT-REF")
            ident = (ref.text or "").strip() if ref is not None else ""
            obj = by_ident.get(ident)
            if obj is None:
                raise ReqifError(
                    f"ReqIF reader cannot parse {file_path}: "
                    f"SPEC-HIERARCHY object {ident!r} is not a SPEC-OBJECT"
                )
            if obj.item_id not in paths:
                paths[obj.item_id] = list(ancestors)
            title = title_of(obj)
        else:
            title = (node.get("LONG-NAME") or "").strip()
            if not title:
                raise ReqifError(
                    f"ReqIF reader cannot parse {file_path}: "
                    "SPEC-HIERARCHY has no object and no LONG-NAME"
                )
        children = _child(node, "CHILDREN")
        if children is None:
            return
        for child in _children(children, "SPEC-HIERARCHY"):
            walk(child, ancestors + [title])

    for specification in _children(specifications, "SPECIFICATION"):
        children = _child(specification, "CHILDREN")
        if children is None:
            continue
        for node in _children(children, "SPEC-HIERARCHY"):
            walk(node, [])
    return paths


def _links(content, by_ident, relation_types, file_path) -> dict[str, list[SourceLink]]:
    links = {obj.item_id: [] for obj in by_ident.values()}
    container = _child(content, "SPEC-RELATIONS")
    if container is None:
        return links
    for relation in _children(container, "SPEC-RELATION"):
        type_id = _nested_ref(relation, "TYPE", "SPEC-RELATION-TYPE-REF", file_path)
        if type_id not in relation_types:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: "
                f"unresolved SPEC-RELATION-TYPE {type_id!r}"
            )
        type_name = relation_types[type_id]
        source_id = _nested_ref(relation, "SOURCE", "SPEC-OBJECT-REF", file_path)
        target_id = _nested_ref(relation, "TARGET", "SPEC-OBJECT-REF", file_path)
        source = by_ident.get(source_id)
        if source is None:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: "
                f"SPEC-RELATION source {source_id!r} is not a SPEC-OBJECT"
            )
        links[source.item_id].append(SourceLink(
            type=_link_type(type_name),
            target_id=target_id,
            role=type_name,
            context=[],
        ))
    return links


def _link_type(type_name: str) -> str:
    """Map a SPEC-RELATION-TYPE LONG-NAME onto a SourceLink type.

    The card keeps "refine" and "satisf" on traces_to, and the same for a
    type the format itself calls a trace. Any other name is refers_to.
    """
    folded = type_name.casefold()
    if "refine" in folded or "satisf" in folded or "trace" in folded:
        return "traces_to"
    return "refers_to"


def _index_definitions(content, file_path) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for tag in _DEFINITION_TAGS:
        for node in content.iter():
            if _local(node.tag) != tag:
                continue
            ident = (node.get("IDENTIFIER") or "").strip()
            if not ident:
                raise ReqifError(
                    f"ReqIF reader cannot parse {file_path}: "
                    f"{tag} has no IDENTIFIER"
                )
            if ident in found:
                raise ReqifError(
                    f"ReqIF reader cannot parse {file_path}: "
                    f"duplicate attribute definition {ident!r}"
                )
            found[ident] = (node.get("LONG-NAME") or "", ident)
    return found


def _index_enums(content, file_path) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in content.iter():
        if _local(node.tag) != "ENUM-VALUE":
            continue
        ident = (node.get("IDENTIFIER") or "").strip()
        if not ident:
            continue
        if ident in found:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: duplicate ENUM-VALUE {ident!r}"
            )
        found[ident] = node.get("LONG-NAME") or ident
    return found


def _index_named(content, tag: str, file_path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in content.iter():
        if _local(node.tag) != tag:
            continue
        ident = (node.get("IDENTIFIER") or "").strip()
        if not ident:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: {tag} has no IDENTIFIER"
            )
        if ident in found:
            raise ReqifError(
                f"ReqIF reader cannot parse {file_path}: duplicate {tag} {ident!r}"
            )
        found[ident] = node.get("LONG-NAME") or ""
    return found


def _nested_ref(element, wrapper: str, ref_tag: str, file_path: Path) -> str:
    box = _child(element, wrapper)
    if box is None:
        raise ReqifError(f"ReqIF reader cannot parse {file_path}: missing {wrapper}")
    node = _child(box, ref_tag)
    ident = (node.text or "").strip() if node is not None else ""
    if not ident:
        raise ReqifError(f"ReqIF reader cannot parse {file_path}: missing {ref_tag}")
    return ident


def _required(element, name: str, file_path: Path):
    child = _child(element, name)
    if child is None:
        raise ReqifError(f"ReqIF reader cannot parse {file_path}: missing {name}")
    return child


def _child(element, name: str):
    found = _children(element, name)
    if not found:
        return None
    return found[0]


def _children(element, name: str):
    return [child for child in list(element) if _local(child.tag) == name]


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _snake(name: str, file_path: Path) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", name.strip())
    cleaned = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", cleaned)
    cleaned = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    if not cleaned:
        raise ReqifError(
            f"ReqIF reader cannot parse {file_path}: attribute name {name!r} is empty"
        )
    return cleaned


def _relative_file(path: Path, corpus_root: str | Path | None) -> str:
    if corpus_root is None:
        return path.as_posix()
    return path.resolve().relative_to(Path(corpus_root).resolve()).as_posix()
