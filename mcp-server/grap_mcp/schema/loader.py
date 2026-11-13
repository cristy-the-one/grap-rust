"""Load the automotive schema transcribed into automotive.yaml.

Relation ends written as "any" or "any entity" are the spec's words. They are
resolved here to node-type names so every end the caller sees is a known type.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_TIERS = {"Evidence", "Claim", "Entity"}
_GROUPS = {
    "Evidence",
    "Structure and claims",
    "Requirements and test",
    "Architecture",
    "Communication and data",
    "Behaviour",
    "Terminology and versions",
}
_MARKS = {"✔", "○"}
_PHRASES = {"any", "any entity"}
_NODE_KEYS = {"name", "tier", "description", "typical_origin", "kind_examples"}
_RELATION_KEYS = {"name", "group", "meaning", "from", "to", "pairs", "same_type"}
_PAIR_KEYS = {"from", "to", "note"}
_TOP_KEYS = {
    "kind_attribute",
    "node_types",
    "goal_document",
    "relations",
    "item_mappings",
    "folded_edge_note",
    "folded_edges",
    "folded_edge_rule",
    "reader_links",
    "statements",
}
_ITEM_KEYS = {"reader", "item", "tags", "mark", "node_type", "kind", "note", "also"}
_ALSO_KEYS = {"node_type", "note"}
_FOLDED_KEYS = {
    "mark", "objects", "from", "from_kind", "relation", "relations",
    "to", "to_kind", "kept", "role",
}
_LINK_KEYS = {
    "link", "note", "relation", "relations", "when", "otherwise",
    "keep_role", "from", "to",
}
_EDGE_KEYS = {"relation", "direction", "node_type", "target", "when"}
_DIRECTIONS = {"to", "from"}

_DEFAULT_PATH = Path(__file__).with_name("automotive.yaml")


@dataclass(frozen=True)
class NodeType:
    name: str
    tier: str
    description: str
    typical_origin: str
    kind_examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Relation:
    name: str
    group: str
    meaning: str
    from_types: tuple[str, ...]
    to_types: tuple[str, ...]
    pairs: tuple[tuple[str, str], ...]
    # product: from × to. pairs: the listed pairs only. same_type: each type to itself.
    ends_form: str


@dataclass(frozen=True)
class AutomotiveSchema:
    node_types: tuple[NodeType, ...]
    relations: tuple[Relation, ...]
    kind_attribute: str
    goal_document: tuple[dict, ...]
    item_mappings: tuple[dict, ...]
    folded_edges: tuple[dict, ...]
    folded_edge_note: str
    folded_edge_rule: str
    reader_links: tuple[dict, ...]
    statements: dict


def load_automotive_schema(path: str | Path | None = None) -> AutomotiveSchema:
    """Load §3, §4 and §8. Raise if an end is not a known node type."""
    schema_path = Path(path) if path is not None else _DEFAULT_PATH
    if not schema_path.is_file():
        raise FileNotFoundError(f"Automotive schema file not found: {schema_path}")
    data = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Automotive schema root must be a mapping")
    _unknown(set(data), _TOP_KEYS, "schema")

    node_types = _node_types(data["node_types"])
    known = {node.name for node in node_types}
    entities = tuple(node.name for node in node_types if node.tier == "Entity")
    type_names = tuple(node.name for node in node_types)
    relations = _relations(data["relations"], type_names, entities, known)
    relation_names = {relation.name for relation in relations}

    goal = _rows(data["goal_document"], "goal_document", {"words", "schema"})
    items = _item_mappings(data["item_mappings"], known)
    folded = _folded_edges(data["folded_edges"], known, relation_names)
    links = _reader_links(data["reader_links"], known, relation_names)
    statements = _statements(data["statements"], known, relation_names)
    return AutomotiveSchema(
        node_types=node_types,
        relations=relations,
        kind_attribute=_text(data["kind_attribute"], "kind_attribute"),
        goal_document=goal,
        item_mappings=items,
        folded_edges=folded,
        folded_edge_note=_text(data["folded_edge_note"], "folded_edge_note"),
        folded_edge_rule=_text(data["folded_edge_rule"], "folded_edge_rule"),
        reader_links=links,
        statements=statements,
    )


def format_report(schema: AutomotiveSchema) -> str:
    """Counts with the number of rows behind each one."""
    n_types = len(schema.node_types)
    n_relations = len(schema.relations)
    n_entities = sum(1 for node in schema.node_types if node.tier == "Entity")
    known = {node.name for node in schema.node_types}
    lines = [
        f"node_types={n_types} (n={n_types})",
        "node_type_names=" + ", ".join(node.name for node in schema.node_types),
        f"entity_types={n_entities} (n={n_types} node types)",
        f"relations={n_relations} (n={n_relations})",
        "relation_names=" + ", ".join(relation.name for relation in schema.relations),
    ]
    unknown_total = 0
    for relation in schema.relations:
        n_from = len(relation.from_types)
        n_to = len(relation.to_types)
        n_pairs = len(relation.pairs)
        unknown = [
            end for end in (*relation.from_types, *relation.to_types) if end not in known
        ]
        unknown_total += len(unknown)
        lines.append(
            f"{relation.name}: ends_form={relation.ends_form} "
            f"from_types={n_from} (n={n_from}) to_types={n_to} (n={n_to}) "
            f"pairs={n_pairs} (n={n_pairs}) "
            f"unknown_ends={len(unknown)} (n={n_types} node types)"
        )
        if relation.ends_form != "product":
            for source, target in relation.pairs:
                lines.append(f"  {source} -> {target}")
    lines.append(f"unknown_ends={unknown_total} (n={n_types} node types)")
    for label, rows in (
        ("goal_document", schema.goal_document),
        ("item_mappings", schema.item_mappings),
        ("folded_edges", schema.folded_edges),
        ("reader_links", schema.reader_links),
        ("statement_attributes", schema.statements["attributes"]),
        ("statement_kinds", schema.statements["kinds"]),
        ("statement_edges", schema.statements["edges"]),
    ):
        n = len(rows)
        lines.append(f"{label}={n} (n={n})")
    return "\n".join(lines)


def _node_types(rows: object) -> tuple[NodeType, ...]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("node_types must be a non-empty list")
    loaded: list[NodeType] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        where = f"node_types[{index}]"
        if not isinstance(row, dict):
            raise ValueError(f"{where} must be a mapping")
        _unknown(set(row), _NODE_KEYS, where)
        name = _text(row.get("name"), f"{where}.name")
        if name in _PHRASES:
            raise ValueError(f"{where}: {name!r} is reserved for relation ends")
        if name in seen:
            raise ValueError(f"{where}: duplicate node type {name!r}")
        seen.add(name)
        tier = _text(row.get("tier"), f"{where}.tier")
        if tier not in _TIERS:
            raise ValueError(f"{where}: tier {tier!r} is not one of {sorted(_TIERS)}")
        examples = row.get("kind_examples", [])
        if not isinstance(examples, list) or not all(isinstance(item, str) and item for item in examples):
            raise ValueError(f"{where}.kind_examples must be a list of strings")
        loaded.append(NodeType(
            name=name,
            tier=tier,
            description=_text(row.get("description"), f"{where}.description"),
            typical_origin=_text(row.get("typical_origin"), f"{where}.typical_origin"),
            kind_examples=tuple(examples),
        ))
    return tuple(loaded)


def _relations(
    rows: object,
    type_names: tuple[str, ...],
    entities: tuple[str, ...],
    known: set[str],
) -> tuple[Relation, ...]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("relations must be a non-empty list")
    loaded: list[Relation] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        where = f"relations[{index}]"
        if not isinstance(row, dict):
            raise ValueError(f"{where} must be a mapping")
        _unknown(set(row), _RELATION_KEYS, where)
        name = _text(row.get("name"), f"{where}.name")
        if name in seen:
            raise ValueError(f"{where}: duplicate relation {name!r}")
        seen.add(name)
        group = _text(row.get("group"), f"{where}.group")
        if group not in _GROUPS:
            raise ValueError(f"{where}: group {group!r} is not a §4 heading")
        meaning = _text(row.get("meaning"), f"{where}.meaning")
        from_types, to_types, pairs, form = _ends(row, type_names, entities, known, f"{where} {name}")
        loaded.append(Relation(
            name=name,
            group=group,
            meaning=meaning,
            from_types=from_types,
            to_types=to_types,
            pairs=pairs,
            ends_form=form,
        ))
    return tuple(loaded)


def _ends(
    row: dict,
    type_names: tuple[str, ...],
    entities: tuple[str, ...],
    known: set[str],
    where: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...], str]:
    has_product = "from" in row or "to" in row
    has_pairs = "pairs" in row
    has_same = "same_type" in row
    chosen = sum((has_product, has_pairs, has_same))
    if chosen != 1:
        raise ValueError(f"{where}: set exactly one of from/to, pairs, or same_type")
    if has_same:
        if row["same_type"] is not True:
            raise ValueError(f"{where}: same_type must be true")
        pairs = tuple((name, name) for name in type_names)
        return type_names, type_names, pairs, "same_type"
    if has_pairs:
        return _explicit_pairs(row["pairs"], type_names, entities, known, where)
    if "from" not in row or "to" not in row:
        raise ValueError(f"{where}: from and to are both required")
    from_types = _expand(row["from"], type_names, entities, known, f"{where}.from")
    to_types = _expand(row["to"], type_names, entities, known, f"{where}.to")
    pairs = tuple((source, target) for source in from_types for target in to_types)
    return from_types, to_types, pairs, "product"


def _explicit_pairs(
    rows: object,
    type_names: tuple[str, ...],
    entities: tuple[str, ...],
    known: set[str],
    where: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...], str]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{where}.pairs must be a non-empty list")
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    from_order: list[str] = []
    to_order: list[str] = []
    for index, entry in enumerate(rows, start=1):
        slot = f"{where}.pairs[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{slot} must be a mapping")
        _unknown(set(entry), _PAIR_KEYS, slot)
        sources = _expand(_as_list(entry.get("from"), f"{slot}.from"), type_names, entities, known, f"{slot}.from")
        targets = _expand(entry.get("to"), type_names, entities, known, f"{slot}.to")
        for source in sources:
            if source not in from_order:
                from_order.append(source)
            for target in targets:
                if target not in to_order:
                    to_order.append(target)
                pair = (source, target)
                if pair in seen:
                    raise ValueError(f"{slot}: duplicate pair {source} -> {target}")
                seen.add(pair)
                pairs.append(pair)
    return tuple(from_order), tuple(to_order), tuple(pairs), "pairs"


def _expand(
    items: object,
    type_names: tuple[str, ...],
    entities: tuple[str, ...],
    known: set[str],
    where: str,
) -> tuple[str, ...]:
    if not isinstance(items, list) or not items:
        raise ValueError(f"{where}: ends must be a non-empty list")
    resolved: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item == "any":
            names = type_names
        elif item == "any entity":
            names = entities
            if not names:
                raise ValueError(f"{where}: no node type has tier Entity")
        elif isinstance(item, str) and item in known:
            names = (item,)
        else:
            raise ValueError(f"{where}: {item!r} is not a known node type")
        for name in names:
            if name not in seen:
                seen.add(name)
                resolved.append(name)
    return tuple(resolved)


def _item_mappings(rows: object, known: set[str]) -> tuple[dict, ...]:
    loaded = _rows(rows, "item_mappings", _ITEM_KEYS)
    for index, row in enumerate(loaded, start=1):
        where = f"item_mappings[{index}]"
        _text(row.get("reader"), f"{where}.reader")
        if "item" not in row and "tags" not in row:
            raise ValueError(f"{where}: item or tags is required")
        if "node_type" not in row and "also" not in row:
            raise ValueError(f"{where}: node_type or also is required")
        if "item" in row:
            _text(row["item"], f"{where}.item")
        if "tags" in row:
            _strings(row["tags"], f"{where}.tags")
        if "mark" in row and row["mark"] not in _MARKS:
            raise ValueError(f"{where}: mark {row['mark']!r} is not ✔ or ○")
        if "node_type" in row:
            _known_type(row["node_type"], known, f"{where}.node_type")
        if "kind" in row:
            _text(row["kind"], f"{where}.kind")
        if "note" in row:
            _text(row["note"], f"{where}.note")
        if "also" in row:
            also_rows = _rows(row["also"], f"{where}.also", _ALSO_KEYS)
            for also_index, also in enumerate(also_rows, start=1):
                _known_type(also.get("node_type"), known, f"{where}.also[{also_index}].node_type")
    return loaded


def _folded_edges(rows: object, known: set[str], relations: set[str]) -> tuple[dict, ...]:
    loaded = _rows(rows, "folded_edges", _FOLDED_KEYS)
    for index, row in enumerate(loaded, start=1):
        where = f"folded_edges[{index}]"
        _strings(row.get("objects"), f"{where}.objects")
        if "mark" in row and row["mark"] not in _MARKS:
            raise ValueError(f"{where}: mark {row['mark']!r} is not ✔ or ○")
        _one_relation(row, relations, where)
        if "from" in row:
            _known_type(row["from"], known, f"{where}.from")
        _known_type(row.get("to"), known, f"{where}.to")
        for key in ("from_kind", "to_kind", "role"):
            if key in row:
                _text(row[key], f"{where}.{key}")
        if "kept" in row:
            _strings(row["kept"], f"{where}.kept")
    return loaded


def _reader_links(rows: object, known: set[str], relations: set[str]) -> tuple[dict, ...]:
    loaded = _rows(rows, "reader_links", _LINK_KEYS)
    for index, row in enumerate(loaded, start=1):
        where = f"reader_links[{index}]"
        _text(row.get("link"), f"{where}.link")
        _one_relation(row, relations, where)
        if "otherwise" in row:
            _known_relation(row["otherwise"], relations, f"{where}.otherwise")
        if "from" in row:
            _known_type(row["from"], known, f"{where}.from")
        if "to" in row:
            targets = row["to"]
            if not isinstance(targets, list) or not targets:
                raise ValueError(f"{where}.to must be a non-empty list")
            for target in targets:
                _known_type(target, known, f"{where}.to")
        for key in ("note", "when"):
            if key in row:
                _text(row[key], f"{where}.{key}")
        if "keep_role" in row and row["keep_role"] is not True:
            raise ValueError(f"{where}.keep_role must be true")
    return loaded


def _statements(row: object, known: set[str], relations: set[str]) -> dict:
    if not isinstance(row, dict):
        raise ValueError("statements must be a mapping")
    _unknown(set(row), {"attributes", "kinds", "edges"}, "statements")
    attributes = _strings(row.get("attributes"), "statements.attributes")
    kinds = _strings(row.get("kinds"), "statements.kinds")
    edges = _rows(row.get("edges"), "statements.edges", _EDGE_KEYS)
    for index, edge in enumerate(edges, start=1):
        where = f"statements.edges[{index}]"
        _known_relation(edge.get("relation"), relations, f"{where}.relation")
        direction = edge.get("direction")
        if direction not in _DIRECTIONS:
            raise ValueError(f"{where}.direction must be 'to' or 'from'")
        has_type = "node_type" in edge
        has_target = "target" in edge
        if has_type == has_target:
            raise ValueError(f"{where}: set exactly one of node_type or target")
        if has_type:
            _known_type(edge["node_type"], known, f"{where}.node_type")
        else:
            _text(edge["target"], f"{where}.target")
        if "when" in edge:
            _text(edge["when"], f"{where}.when")
    return {"attributes": attributes, "kinds": kinds, "edges": edges}


def _one_relation(row: dict, relations: set[str], where: str) -> None:
    has_one = "relation" in row
    has_many = "relations" in row
    if has_one == has_many:
        raise ValueError(f"{where}: set exactly one of relation or relations")
    if has_one:
        _known_relation(row["relation"], relations, f"{where}.relation")
        return
    names = row["relations"]
    if not isinstance(names, list) or not names:
        raise ValueError(f"{where}.relations must be a non-empty list")
    for name in names:
        _known_relation(name, relations, f"{where}.relations")


def _rows(rows: object, where: str, allowed: set[str]) -> tuple[dict, ...]:
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{where} must be a non-empty list")
    loaded: list[dict] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"{where}[{index}] must be a mapping")
        _unknown(set(row), allowed, f"{where}[{index}]")
        loaded.append(row)
    return tuple(loaded)


def _known_type(name: object, known: set[str], where: str) -> None:
    if not isinstance(name, str) or name not in known:
        raise ValueError(f"{where}: {name!r} is not a known node type")


def _known_relation(name: object, relations: set[str], where: str) -> None:
    if not isinstance(name, str) or name not in relations:
        raise ValueError(f"{where}: {name!r} is not a known relation")


def _strings(items: object, where: str) -> tuple[str, ...]:
    if not isinstance(items, list) or not items or not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"{where} must be a non-empty list of strings")
    return tuple(items)


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be a non-empty string")
    return value.strip()


def _as_list(value: object, where: str) -> list:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    raise ValueError(f"{where} must be a name or a list")


def _unknown(present: set[str], allowed: set[str], where: str) -> None:
    extra = present - allowed
    if extra:
        raise ValueError(f"{where}: unknown keys {sorted(extra)}")


def main() -> None:
    schema = load_automotive_schema()
    print(format_report(schema))


if __name__ == "__main__":
    main()
