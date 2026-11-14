"""Project SourceItems onto the automotive schema.

Identity is §5. Edges are §8, checked against §4, and counted as in §7.
The relation name is encoded by ``working_graph._relation_properties``:
a schema relation is its own property, and any other verb is ``refers_to``
with that verb as ``role``. This module does not encode relations itself.

A projection prints the four §7 counts with the number of items or links
and the number of documents behind each one. An empty total prints ``n/a``.
Packages are namespaces (Amendment 1): ``namespace_items`` and
``namespace_links`` are printed the same way and left out of the §7 totals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from grap_mcp.kee.working_graph import _relation_properties
from grap_mcp.readers import SourceItem, SourceLink
from grap_mcp.schema.loader import AutomotiveSchema, load_automotive_schema
from grap_mcp.schema.validate import relation_allowed

# §3 names the four Signal.kind values. The tag is the source's own word.
_SIGNAL_KIND = {
    "SYSTEM-SIGNAL": "system_signal",
    "I-SIGNAL": "i_signal",
    "I-SIGNAL-GROUP": "signal_group",
    "SYSTEM-SIGNAL-GROUP": "signal_group",
}
_KIND_WORD = {"system": "system_signal", "bus": "i_signal"}
# AUTOSAR tags are uppercase segments. A segment may be a single letter (I-SIGNAL).
_TAG = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")
_REQIF_KINDS = {"requirement", "heading", "parameter", "definition", "table"}


@dataclass
class _Class:
    type_name: str
    kind: str
    untyped: bool
    placeholder: bool
    reader: str


@dataclass
class _Node:
    key: str
    type_name: str
    label: str
    scope: str
    native_id: str
    kind: str
    source_id: str
    engine_id: int


@dataclass
class _End:
    target_id: str
    type_name: str | None
    kind: str
    unresolved: bool
    link: SourceLink


@dataclass
class _Built:
    nodes: list[_Node] = field(default_factory=list)
    by_key: dict[str, _Node] = field(default_factory=dict)
    typed: dict[tuple[str, str], _Node] = field(default_factory=dict)
    items: dict[tuple[str, str], _Node] = field(default_factory=dict)
    sources: dict[str, _Node] = field(default_factory=dict)
    placeholders: dict[tuple[str, str], _Node] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    link_n: int = 0
    fallback_n: int = 0
    rejected_n: int = 0
    unresolved_n: int = 0


def project_items(items: list[SourceItem], kg) -> dict:
    """Write typed nodes and edges for `items` onto `kg` and print §7."""
    schema = load_automotive_schema()
    classes = {_id(item): _classify(item, schema) for item in items}
    by_id = {_id(item): item for item in items}
    folds = _folds(items, by_id, classes, schema)
    built = _Built()
    _create_nodes(items, classes, folds, built, kg)
    _structural_edges(items, classes, folds, built, kg)
    _folded_edges(items, folds, built, kg)
    _remaining_links(items, by_id, classes, folds, schema, built, kg)
    _dbc_edges(items, classes, built, kg)
    return _finish(items, classes, folds, built)


def _id(item: SourceItem) -> tuple[str, str]:
    return item.source_id, item.item_id


def _scope(source_id: str, release: str | None) -> str:
    if release:
        return f"{source_id}@{release}"
    return source_id


def _node_key(type_name: str, scope: str, native_id: str) -> str:
    return f"{type_name} | {scope} | {native_id}"


def _engine_text(native_id: str, description: str) -> str:
    description = description.strip()
    if description and description != native_id:
        return f"{native_id}\n{description}"
    return native_id


def _kind_for(tag: str, type_name: str) -> str:
    if type_name == "Signal":
        return _SIGNAL_KIND.get(tag, tag)
    return tag


def _classify(item: SourceItem, schema: AutomotiveSchema) -> _Class:
    if item.kind_hint == "unresolved":
        return _Class(
            type_name="unresolved",
            kind=item.attributes.get("expected_kind", ""),
            untyped=False,
            placeholder=True,
            reader="",
        )
    tag = item.attributes.get("tag")
    if tag:
        return _classify_arxml(tag, schema)
    if item.kind_hint == "message" and "frame_id" in item.attributes:
        return _Class(_dbc_kind(schema, "message", "Frame"), _dbc_kind_name(schema, "message"), False, False, "DBC")
    if item.kind_hint == "signal" and "start_bit" in item.attributes:
        return _Class(_dbc_kind(schema, "signal", "Signal"), _dbc_kind_name(schema, "signal"), False, False, "DBC")
    if item.kind_hint in _REQIF_KINDS:
        return _Class("Requirement", item.kind_hint, False, False, "ReqIF")
    if item.kind_hint == "statement":
        return _Class("Statement", item.attributes.get("kind", "statement"), False, False, "text")
    return _Class("Element", item.kind_hint, True, False, "")


def _classify_arxml(tag: str, schema: AutomotiveSchema) -> _Class:
    exact: dict[str, str] = {}
    suffixes: list[tuple[str, str]] = []
    tokens: dict[str, str] = {}
    fallback = "Element"
    for row in schema.item_mappings:
        if row.get("reader") != "ARXML" or "node_type" not in row:
            continue
        node_type = row["node_type"]
        for name in row.get("tags", ()):
            exact.setdefault(name, node_type)
        item = row.get("item", "")
        if item.startswith("tags ending "):
            suffixes.append((item.removeprefix("tags ending ").strip(), node_type))
        elif item.startswith("everything else"):
            fallback = node_type
        else:
            for token in _TAG.findall(item):
                tokens.setdefault(token, node_type)
    if tag in exact:
        type_name = exact[tag]
        return _Class(type_name, _kind_for(tag, type_name), False, False, "ARXML")
    for suffix, type_name in suffixes:
        if tag.endswith(suffix):
            return _Class(type_name, _kind_for(tag, type_name), False, False, "ARXML")
    if tag in tokens:
        type_name = tokens[tag]
        return _Class(type_name, _kind_for(tag, type_name), False, False, "ARXML")
    return _Class(fallback, tag, True, False, "ARXML")


def _dbc_kind(schema: AutomotiveSchema, item_name: str, default: str) -> str:
    for row in schema.item_mappings:
        if row.get("reader") == "DBC" and row.get("item") == item_name and row.get("node_type"):
            return row["node_type"]
    return default


def _dbc_kind_name(schema: AutomotiveSchema, item_name: str) -> str:
    for row in schema.item_mappings:
        if row.get("reader") == "DBC" and row.get("item") == item_name and row.get("kind"):
            return row["kind"]
    return item_name


def _fold_rules(schema: AutomotiveSchema) -> tuple[dict[str, dict], list[tuple[str, str | None, dict]]]:
    """Item tags, and reference roles, that §8 folds into one edge.

    A prose object with no tag, and a row that names several relations,
    is left for the item mapping. Those rows are the unconfirmed shapes.
    """
    by_tag: dict[str, dict] = {}
    by_role: list[tuple[str, str | None, dict]] = []
    for row in schema.folded_edges:
        if "relation" not in row:
            continue
        for obj in row["objects"]:
            separator = next(
                (sep for sep in (" on an ", " on a ") if sep in obj), None,
            )
            if separator is not None:
                role, owner = obj.split(separator, 1)
                by_role.append((role.strip(), owner.strip(), row))
                continue
            if not _TAG.fullmatch(obj):
                continue
            if obj.endswith("-REF"):
                by_role.append((obj, None, row))
            else:
                by_tag[obj] = row
    return by_tag, by_role


def _is_package(item: SourceItem) -> bool:
    return item.attributes.get("tag") == "AR-PACKAGE"


def _folds(items, by_id, classes, schema) -> dict[tuple[str, str], tuple[_End, _End, dict]]:
    """Fold an object that joins exactly two things.

    Amendment 1 withdrew the exception for an object other items refer to.
    A reference to the folded object is followed through to the edge's first
    end, which is the ``from`` end.
    """
    by_tag, _roles = _fold_rules(schema)
    chosen: dict[tuple[str, str], tuple[_End, _End, dict]] = {}
    for item in items:
        key = _id(item)
        rule = by_tag.get(item.attributes.get("tag", ""))
        if rule is None or classes[key].placeholder or _is_package(item):
            continue
        assigned = _assign_ends(item, rule, by_id, classes)
        if assigned is None:
            continue
        if not relation_allowed(rule["from"], rule["relation"], rule["to"]):
            continue
        chosen[key] = (*assigned, rule)
    return chosen


def _assign_ends(item, rule, by_id, classes) -> tuple[_End, _End] | None:
    from_type = rule.get("from")
    to_type = rule.get("to")
    if not from_type or not to_type:
        return None
    seen: dict[str, SourceLink] = {}
    for link in item.links:
        seen.setdefault(link.target_id, link)
    if len(seen) != 2:
        return None
    ends = [_end(item, link, by_id, classes) for link in seen.values()]
    options: list[tuple[_End, _End]] = []
    for source, target in (ends, (ends[1], ends[0])):
        if not _end_fits(source, from_type, rule.get("from_kind")):
            continue
        if not _end_fits(target, to_type, rule.get("to_kind")):
            continue
        if source.type_name == from_type or target.type_name == to_type:
            options.append((source, target))
    if len(options) == 1:
        return options[0]
    return None


def _end(item: SourceItem, link: SourceLink, by_id, classes) -> _End:
    key = (item.source_id, link.target_id)
    other = by_id.get(key)
    if other is None or classes[key].placeholder:
        kind = ""
        if other is not None:
            kind = _SIGNAL_KIND.get(classes[key].kind, classes[key].kind)
        return _End(link.target_id, None, kind, True, link)
    info = classes[key]
    return _End(link.target_id, info.type_name, info.kind, False, link)


def _end_fits(end: _End, type_name: str, kind_word: str | None) -> bool:
    if end.type_name not in (None, type_name):
        return False
    if not kind_word:
        return True
    if end.unresolved:
        return True
    wanted = _KIND_WORD.get(kind_word, kind_word)
    return end.kind == wanted


def _create_nodes(items, classes, folds, built: _Built, kg) -> None:
    seen_scope: set[str] = set()
    dbc_scopes: set[str] = set()
    for item in items:
        key = _id(item)
        info = classes[key]
        if info.placeholder or key in folds or _is_package(item):
            continue
        scope = _scope(item.source_id, item.release)
        if scope not in seen_scope:
            seen_scope.add(scope)
            built.sources[scope] = _add(
                built, kg,
                type_name="Source", scope=scope, native_id=item.source_id,
                kind="", source_id=item.source_id,
                text=_engine_text(item.source_id, item.file),
            )
        if info.reader == "DBC" and info.type_name == "Frame":
            dbc_scopes.add(scope)
        text = _engine_text(item.item_id, item.text)
        built.items[key] = _add(
            built, kg,
            type_name="Item", scope=scope, native_id=item.item_id,
            kind=item.kind_hint, source_id=item.source_id, text=text,
        )
        built.typed[key] = _add(
            built, kg,
            type_name=info.type_name, scope=scope, native_id=item.item_id,
            kind=info.kind, source_id=item.source_id, text=text,
        )
    for item in items:
        key = _id(item)
        if not classes[key].placeholder:
            continue
        built.placeholders[key] = _add(
            built, kg,
            type_name="unresolved", scope=_scope(item.source_id, None),
            native_id=item.item_id, kind=classes[key].kind,
            source_id=item.source_id, text=item.item_id, label="unresolved",
        )
    for item in items:
        if classes[_id(item)].reader != "DBC" or classes[_id(item)].type_name != "Frame":
            continue
        scope = _scope(item.source_id, item.release)
        if scope not in dbc_scopes:
            continue
        dbc_scopes.remove(scope)
        _add(
            built, kg,
            type_name="Network", scope=scope, native_id=item.source_id,
            kind="", source_id=item.source_id,
            text=_engine_text(item.source_id, item.file),
        )


def _add(built, kg, *, type_name, scope, native_id, kind, source_id, text, label=None) -> _Node:
    key = _node_key(type_name, scope, native_id)
    existing = built.by_key.get(key)
    if existing is not None:
        return existing
    engine_id = kg.add_node(text=text, label=type_name if label is None else label)
    node = _Node(
        key=key, type_name=type_name, label=type_name if label is None else label,
        scope=scope, native_id=native_id, kind=kind, source_id=source_id,
        engine_id=engine_id,
    )
    built.by_key[key] = node
    built.nodes.append(node)
    return node


def _structural_edges(items, classes, folds, built: _Built, kg) -> None:
    for item in items:
        key = _id(item)
        info = classes[key]
        if info.placeholder or key in folds or _is_package(item):
            continue
        scope = _scope(item.source_id, item.release)
        _write(
            built, kg,
            relation="in_source", source=built.items[key], target=built.sources[scope],
            role="", evidence=item.item_id, attributes={}, context=[],
            fallback=False, unresolved=False, counted=False,
        )
        if relation_allowed(info.type_name, "stated_in", "Item"):
            _write(
                built, kg,
                relation="stated_in", source=built.typed[key], target=built.items[key],
                role="", evidence=item.item_id, attributes={}, context=[],
                fallback=False, unresolved=False, counted=False,
            )


def _folded_edges(items, folds, built: _Built, kg) -> None:
    for item in items:
        chosen = folds.get(_id(item))
        if chosen is None:
            continue
        source_end, target_end, rule = chosen
        source = _resolve(built, kg, item.source_id, source_end)
        target = _resolve(built, kg, item.source_id, target_end)
        _write(
            built, kg,
            relation=rule["relation"], source=source, target=target, role="",
            evidence=item.item_id, attributes=dict(item.attributes), context=[],
            fallback=False, unresolved=source_end.unresolved or target_end.unresolved,
            counted=True,
        )


def _remaining_links(items, by_id, classes, folds, schema, built: _Built, kg) -> None:
    _tags, roles = _fold_rules(schema)
    for item in items:
        key = _id(item)
        if classes[key].placeholder or key in folds or _is_package(item):
            continue
        info = classes[key]
        for link in item.links:
            if _targets_package(item, link, by_id):
                continue
            link = _follow_folded(item.source_id, link, folds)
            if _write_role(item, info, link, roles, by_id, classes, built, kg):
                continue
            _write_plain(item, info, link, by_id, classes, built, kg)


def _targets_package(item: SourceItem, link: SourceLink, by_id) -> bool:
    """A containment link whose target is a package is not written."""
    if link.type != "part_of":
        return False
    target = by_id.get((item.source_id, link.target_id))
    return target is not None and _is_package(target)


def _follow_folded(source_id: str, link: SourceLink, folds) -> SourceLink:
    """A reference to a folded object arrives at that edge's first end."""
    chosen = folds.get((source_id, link.target_id))
    if chosen is None:
        return link
    source_end, _target_end, _rule = chosen
    if source_end.target_id == link.target_id:
        return link
    return SourceLink(
        type=link.type,
        target_id=source_end.target_id,
        role=link.role,
        context=list(link.context),
    )


def _write_role(item, info, link, roles, by_id, classes, built, kg) -> bool:
    tag = item.attributes.get("tag", "")
    row = None
    for role, owner, candidate in roles:
        if link.role != role:
            continue
        if owner is not None and tag != owner:
            continue
        row = candidate
        break
    if row is None:
        return False
    relation = row["relation"]
    target_key = (item.source_id, link.target_id)
    other = by_id.get(target_key)
    unresolved = other is None or classes[target_key].placeholder
    target_info = None if unresolved else classes[target_key]
    target_type = None if target_info is None else target_info.type_name
    target_kind = "" if target_info is None else target_info.kind
    if "from" not in row:
        return _write_forward_role(
            item, info, link, row, relation, unresolved, target_type, target_kind, built, kg,
        )
    return _write_backward_role(
        item, info, link, row, relation, unresolved, target_type, target_kind, built, kg,
    )


def _write_forward_role(item, info, link, row, relation, unresolved, target_type, target_kind, built, kg) -> bool:
    """Owner —relation→ target. Used for the has_type references."""
    to_type = row["to"]
    if not unresolved and row.get("to_kind") and not _kind_is(target_kind, row["to_kind"]):
        return False
    if not unresolved and target_type != to_type:
        _reject(built, item, link, relation, info.type_name, target_type)
        return True
    if not relation_allowed(info.type_name, relation, to_type):
        _reject(built, item, link, relation, info.type_name, to_type)
        return True
    _write(
        built, kg,
        relation=relation, source=built.typed[_id(item)],
        target=_target_node(built, kg, item, link, unresolved),
        role=link.role, evidence=item.item_id, attributes={},
        context=list(link.context), fallback=False, unresolved=unresolved, counted=True,
    )
    return True


def _write_backward_role(item, info, link, row, relation, unresolved, target_type, target_kind, built, kg) -> bool:
    """Target —relation→ owner. SYSTEM-SIGNAL-REF points up; the edge points down."""
    from_type = row["from"]
    to_type = row["to"]
    if info.type_name != to_type:
        return False
    if row.get("to_kind") and not _kind_is(info.kind, row["to_kind"]):
        return False
    if not unresolved and row.get("from_kind") and not _kind_is(target_kind, row["from_kind"]):
        return False
    if not unresolved and target_type != from_type:
        return False
    if not relation_allowed(from_type, relation, to_type):
        _reject(built, item, link, relation, from_type, to_type)
        return True
    _write(
        built, kg,
        relation=relation,
        source=_target_node(built, kg, item, link, unresolved),
        target=built.typed[_id(item)],
        role=link.role, evidence=item.item_id, attributes={},
        context=list(link.context), fallback=False, unresolved=unresolved, counted=True,
    )
    return True


def _kind_is(kind: str, word: str) -> bool:
    return kind == _KIND_WORD.get(word, word)


def _write_plain(item, info, link, by_id, classes, built, kg) -> None:
    target_key = (item.source_id, link.target_id)
    other = by_id.get(target_key)
    unresolved = other is None or classes[target_key].placeholder
    target_type = None if unresolved else classes[target_key].type_name
    target = _target_node(built, kg, item, link, unresolved)
    source = built.typed[_id(item)]
    if link.type == "part_of":
        if unresolved or relation_allowed(info.type_name, "part_of", target_type):
            _write(
                built, kg, relation="part_of", source=source, target=target, role=link.role,
                evidence=item.item_id, attributes={}, context=list(link.context),
                fallback=False, unresolved=unresolved, counted=True,
            )
            return
        _write(
            built, kg, relation="refers_to", source=source, target=target,
            role=link.role or "part_of", evidence=item.item_id, attributes={},
            context=list(link.context), fallback=True, unresolved=False, counted=True,
        )
        return
    if link.type == "traces_to":
        if unresolved or relation_allowed(info.type_name, "traces_to", target_type):
            _write(
                built, kg, relation="traces_to", source=source, target=target, role=link.role,
                evidence=item.item_id, attributes={}, context=list(link.context),
                fallback=False, unresolved=unresolved, counted=True,
            )
            return
        _reject(built, item, link, "traces_to", info.type_name, target_type)
        return
    if link.type == "refers_to":
        _write(
            built, kg, relation="refers_to", source=source, target=target, role=link.role,
            evidence=item.item_id, attributes={}, context=list(link.context),
            fallback=True, unresolved=unresolved, counted=True,
        )
        return
    if link.type in _schema_relations() and not unresolved:
        if relation_allowed(info.type_name, link.type, target_type):
            _write(
                built, kg, relation=link.type, source=source, target=target, role=link.role,
                evidence=item.item_id, attributes={}, context=list(link.context),
                fallback=False, unresolved=False, counted=True,
            )
            return
        _reject(built, item, link, link.type, info.type_name, target_type)
        return
    _write(
        built, kg, relation=link.type, source=source, target=target, role=link.role,
        evidence=item.item_id, attributes={}, context=list(link.context),
        fallback=True, unresolved=unresolved, counted=True,
    )


def _schema_relations() -> frozenset:
    return frozenset(relation.name for relation in load_automotive_schema().relations)


def _target_node(built: _Built, kg, item: SourceItem, link: SourceLink, unresolved: bool) -> _Node:
    key = (item.source_id, link.target_id)
    if not unresolved and key in built.typed:
        return built.typed[key]
    return _ensure_placeholder(built, kg, item.source_id, link.target_id)


def _resolve(built: _Built, kg, source_id: str, end: _End) -> _Node:
    key = (source_id, end.target_id)
    if not end.unresolved and key in built.typed:
        return built.typed[key]
    return _ensure_placeholder(built, kg, source_id, end.target_id)


def _ensure_placeholder(built: _Built, kg, source_id: str, target_id: str) -> _Node:
    key = (source_id, target_id)
    found = built.placeholders.get(key)
    if found is not None:
        return found
    node = _add(
        built, kg,
        type_name="unresolved", scope=source_id, native_id=target_id,
        kind="", source_id=source_id, text=target_id, label="unresolved",
    )
    built.placeholders[key] = node
    return node


def _dbc_edges(items, classes, built: _Built, kg) -> None:
    for item in items:
        info = classes[_id(item)]
        if info.reader != "DBC":
            continue
        scope = _scope(item.source_id, item.release)
        if info.type_name == "Frame":
            frame = built.typed[_id(item)]
            for name in _names(item.attributes.get("senders", "")):
                ecu = _ecu(built, kg, item, scope, name)
                _write(
                    built, kg, relation="sends", source=ecu, target=frame, role="",
                    evidence=item.item_id, attributes={}, context=[],
                    fallback=False, unresolved=False, counted=True,
                )
        elif info.type_name == "Signal":
            signal = built.typed[_id(item)]
            for name in _names(item.attributes.get("receivers", "")):
                ecu = _ecu(built, kg, item, scope, name)
                _write(
                    built, kg, relation="receives", source=ecu, target=signal, role="",
                    evidence=item.item_id, attributes={}, context=[],
                    fallback=False, unresolved=False, counted=True,
                )


def _names(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def _ecu(built, kg, item, scope, name) -> _Node:
    return _add(
        built, kg,
        type_name="Ecu", scope=scope, native_id=name, kind="",
        source_id=item.source_id, text=name,
    )


def _encode(relation: str, role: str) -> dict:
    """The relation encoding comes only from the working-graph helper."""
    props = dict(_relation_properties(relation))
    props["origin"] = "format"
    props["confidence"] = 1.0
    if "role" not in props:
        if role:
            props["role"] = role
        elif props.get("refers_to") is True:
            props["role"] = ""
    return props


def _write(built, kg, *, relation, source, target, role, evidence, attributes, context,
           fallback, unresolved, counted) -> None:
    props = _encode(relation, role)
    kg.add_edge(source.engine_id, target.engine_id, 1.0, props)
    built.edges.append({
        "relation": relation,
        "from_native_id": source.native_id,
        "to_native_id": target.native_id,
        "from_type": source.type_name,
        "to_type": target.type_name,
        "from_key": source.key,
        "to_key": target.key,
        "properties": props,
        "evidence": evidence,
        "attributes": attributes,
        "context": context,
        "fallback": fallback,
        "unresolved": unresolved,
    })
    if not counted:
        return
    built.link_n += 1
    if fallback:
        built.fallback_n += 1
    if unresolved:
        built.unresolved_n += 1


def _reject(built, item, link, relation, from_type, to_type) -> None:
    built.rejected.append({
        "source_id": item.source_id,
        "item_id": item.item_id,
        "target_id": link.target_id,
        "relation": relation,
        "from_type": from_type,
        "to_type": to_type,
        "role": link.role,
    })
    built.link_n += 1
    built.rejected_n += 1


def _finish(items, classes, folds, built: _Built) -> dict:
    present = [item for item in items if not classes[_id(item)].placeholder]
    packages = [item for item in present if _is_package(item)]
    package_ids = {_id(item) for item in packages}
    real = [item for item in present if _id(item) not in package_ids]
    documents = len({item.source_id for item in real})
    item_n = len(real)
    untyped = sum(
        1 for item in real
        if _id(item) not in folds and classes[_id(item)].untyped
    )
    namespace_links = sum(
        1
        for item in present
        for link in item.links
        if link.type == "part_of" and (item.source_id, link.target_id) in package_ids
    )
    if not present:
        namespace_items = None
        namespace_link_count = None
    else:
        namespace_items = len(packages)
        namespace_link_count = namespace_links
    counts = {
        "documents": documents,
        "items": item_n,
        "links": built.link_n,
        "untyped_items": None if item_n == 0 else untyped,
        "fallback_links": None if built.link_n == 0 else built.fallback_n,
        "rejected_links": None if built.link_n == 0 else built.rejected_n,
        "unresolved": None if built.link_n == 0 else built.unresolved_n,
        "namespace_items": namespace_items,
        "namespace_links": namespace_link_count,
    }
    alarms = {
        "untyped_items": _exceeds(untyped, item_n),
        "fallback_links": _exceeds(built.fallback_n, built.link_n),
    }
    _print_counts(counts, alarms, untyped, built)
    return {
        "nodes": [
            {
                "key": node.key,
                "type": node.type_name,
                "scope": node.scope,
                "native_id": node.native_id,
                "kind": node.kind,
                "label": node.label,
                "engine_id": node.engine_id,
                "source_id": node.source_id,
            }
            for node in built.nodes
        ],
        "edges": built.edges,
        "rejected": built.rejected,
        "counts": counts,
        "alarm": alarms,
    }


def _exceeds(count: int, total: int) -> bool:
    return total > 0 and count * 100 > 20 * total


def _print_counts(counts, alarms, untyped, built: _Built) -> None:
    documents = counts["documents"]

    def line(name: str, value, total: int, unit: str) -> None:
        shown = "n/a" if value is None else str(value)
        print(f"{name}={shown} (n={total} {unit}, documents={documents})")

    line("untyped_items", counts["untyped_items"], counts["items"], "items")
    line("fallback_links", counts["fallback_links"], counts["links"], "links")
    line("rejected_links", counts["rejected_links"], counts["links"], "links")
    line("unresolved", counts["unresolved"], counts["links"], "links")
    line(
        "namespace_items", counts["namespace_items"],
        0 if counts["namespace_items"] is None else counts["namespace_items"],
        "items",
    )
    line(
        "namespace_links", counts["namespace_links"],
        0 if counts["namespace_links"] is None else counts["namespace_links"],
        "links",
    )
    if alarms["untyped_items"]:
        print(
            f"alarm: untyped_items={untyped}/{counts['items']} exceeds 20% "
            f"(documents={documents})"
        )
    if alarms["fallback_links"]:
        print(
            f"alarm: fallback_links={built.fallback_n}/{counts['links']} exceeds 20% "
            f"(documents={documents})"
        )
