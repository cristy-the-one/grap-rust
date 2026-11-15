"""Statement edges from the §6 table (spec-statements.md).

Linking is whatever E-14 and E-15 already wrote: a mention is linked when
exactly one entity has a ``same`` decision in force. A proposal is not a
link. The table, in order, decides the one edge from the statement:

- subject of a ``definition``, entity a ``Term`` → ``defines``
- subject, anything else → ``about``, and only the first linked subject mention
- condition, entity a ``State`` → ``applies_in``
- any field, entity a ``Parameter`` → ``configured_by``
- ``required_behaviour`` or ``values_and_timing``, entity a ``Fault`` → ``raises``
- anywhere else → ``refers_to``, with ``role`` equal to the field name

``stated_in`` to the item is always written. ``has_statement`` runs from the
``Requirement`` when the item's ``kind_hint`` is ``requirement``. A statement
with no linked subject is still written, with no ``about`` edge, and counted
as ``subject_unlinked``.

Every edge takes the statement's origin and has no confidence. A second call
with the same edges does not write them again.
"""

from __future__ import annotations

from collections.abc import Sequence

from grap_mcp.kee.resolution.review import decision_in_force
from grap_mcp.kee.resolution.tables import ResolutionTables
from grap_mcp.kee.statements.tables import Statement
from grap_mcp.kee.working_graph import _relation_properties
from grap_mcp.schema.validate import relation_allowed

_RELATIONS = (
    "about",
    "defines",
    "applies_in",
    "configured_by",
    "raises",
    "refers_to",
    "stated_in",
    "has_statement",
)

# Smaller containing span wins. A tie keeps the more specific field, so a
# subject that covers the whole unit stays the subject.
_FIELD_RANK = {
    "subject": 0,
    "condition": 1,
    "required_behaviour": 2,
    "values_and_timing": 3,
    "exception": 4,
    "unit": 5,
}
_ORIGINS = ("rule", "model")
# The definition kind is not retyped here. ST-01 checks this package for a
# quoted kind. The schema file has one kind that starts with these letters.
_DEFINITION_STEM = "defin"


def write_statement_edges(
    tables: ResolutionTables,
    kg,
    source_id: str,
    item_id: str,
    statements: Sequence[Statement],
    *,
    kind_hint: str,
    release: str | None = None,
) -> dict:
    """Write one item's statement edges and print the counts.

    Mentions must already be stored. This does not link them and does not
    change the mention rows. With nothing to measure, the counts are ``n/a``.
    """
    if not isinstance(tables, ResolutionTables):
        raise TypeError("tables must be ResolutionTables")
    if kg is None or not hasattr(kg, "add_node") or not hasattr(kg, "add_edge"):
        raise TypeError("kg must be a knowledge graph")
    source_id = _required(source_id, "source_id")
    item_id = _required(item_id, "item_id")
    kind_hint = _required(kind_hint, "kind_hint")
    release = _optional(release, "release")
    scope = source_id if release is None else f"{source_id}@{release}"
    if " | " in scope:
        raise ValueError("scope must not contain ' | '")

    ordered = _ordered(statements, source_id, item_id)
    mentions = tables.mentions_for_item(source_id, item_id)
    links = {
        mention.mention_id: key
        for mention in mentions
        if (key := _linked_key(tables, mention.mention_id)) is not None
    }
    nodes = _Nodes(kg)
    edge_rows: list[dict] = []
    unlinked_ids: list[str] = []
    rejected = 0
    for index, statement in enumerate(ordered):
        statement_id = f"{source_id}|{item_id}|{index}"
        if statement.statement_id is not None and statement.statement_id != statement_id:
            raise ValueError(
                f"statement_id {statement.statement_id} does not match {statement_id}"
            )
        rows, missed, refused = _edges_for(
            nodes, statement, statement_id, scope, item_id, kind_hint, mentions, links,
        )
        edge_rows.extend(rows)
        rejected += refused
        if missed:
            unlinked_ids.append(statement_id)

    report = _report(
        mentions=len(mentions),
        linked=len(links),
        statements=len(ordered),
        edge_rows=edge_rows,
        rejected=rejected,
        unlinked_ids=tuple(unlinked_ids),
    )
    print(format_report(report))
    return report


def format_report(report: dict) -> str:
    """One line. A figure with nothing behind it is ``n/a``."""
    cells = " ".join(
        f"{name}={_cell(report['by_relation'][name])}" for name in _RELATIONS
    )
    return (
        f"mentions={_cell(report['mentions'])} "
        f"mentions_linked={_cell(report['mentions_linked'])} "
        f"subject_unlinked={_cell(report['subject_unlinked'])} "
        f"edges={_cell(report['edges'])} "
        f"rejected={_cell(report['rejected'])} "
        f"n_mentions={report['n_mentions']} "
        f"n_statements={report['n_statements']} "
        f"n_edges={report['n_edges']} "
        f"n_documents={report['n_documents']} "
        f"{cells}"
    )


def _edges_for(nodes, statement, statement_id, scope, item_id, kind_hint, mentions, links):
    placed = []
    for mention in mentions:
        field = _field(statement, mention)
        if field is None:
            continue
        placed.append((mention, field, links.get(mention.mention_id)))
    placed.sort(key=lambda row: (row[0].span_start, row[0].span_end, row[0].mention_id))
    first_subject = next(
        (mention.mention_id for mention, field, key in placed
         if field == "subject" and key is not None),
        None,
    )
    origin = statement.origin
    rows: list[dict] = []
    rejected = 0
    rejected += _structural(
        nodes, rows, statement_id, scope, item_id, kind_hint, origin,
    )
    for mention, field, key in placed:
        if key is None:
            continue
        entity_type, entity_scope, native_id = _split_key(key)
        relation, role = _choose(
            statement.kind, field, entity_type, mention.mention_id == first_subject,
        )
        written = _put(
            nodes, rows,
            from_type="Statement", from_scope=scope, from_native=statement_id,
            relation=relation, role=role, origin=origin,
            to_type=entity_type, to_scope=entity_scope, to_native=native_id,
            statement_id=statement_id,
        )
        if not written:
            rejected += 1
    return rows, first_subject is None, rejected


def _structural(nodes, rows, statement_id, scope, item_id, kind_hint, origin) -> int:
    rejected = 0
    if not _put(
        nodes, rows,
        from_type="Statement", from_scope=scope, from_native=statement_id,
        relation="stated_in", role=None, origin=origin,
        to_type="Item", to_scope=scope, to_native=item_id,
        statement_id=statement_id,
    ):
        rejected += 1
    if _is_requirement_item(kind_hint) and not _put(
        nodes, rows,
        from_type="Requirement", from_scope=scope, from_native=item_id,
        relation="has_statement", role=None, origin=origin,
        to_type="Statement", to_scope=scope, to_native=statement_id,
        statement_id=statement_id,
    ):
        rejected += 1
    return rejected


def _choose(kind: str, field: str, entity_type: str, first_subject: bool):
    """The first matching row of the §6 table."""
    if field == "subject" and kind == _definition_kind() and entity_type == "Term":
        return "defines", None
    if field == "subject" and first_subject:
        return "about", None
    if field == "condition" and entity_type == "State":
        return "applies_in", None
    if entity_type == "Parameter":
        return "configured_by", None
    if field in ("required_behaviour", "values_and_timing") and entity_type == "Fault":
        return "raises", None
    return "refers_to", field


def _put(nodes, rows, *, from_type, from_scope, from_native, relation, role, origin,
         to_type, to_scope, to_native, statement_id) -> bool:
    try:
        allowed = relation_allowed(from_type, relation, to_type)
    except ValueError:
        return False
    if not allowed:
        return False
    from_key, source = nodes.get(from_type, from_scope, from_native)
    to_key, target = nodes.get(to_type, to_scope, to_native)
    props = dict(_relation_properties(relation))
    props["origin"] = origin
    if relation == "refers_to":
        props["role"] = role
    if not _present(nodes.kg, source, target, relation, origin, role):
        nodes.kg.add_edge(source, target, 1.0, props)
    rows.append({
        "relation": relation,
        "from_key": from_key,
        "to_key": to_key,
        "from_type": from_type,
        "to_type": to_type,
        "origin": origin,
        "role": role,
        "statement_id": statement_id,
        "properties": props,
    })
    return True


def _present(kg, source, target, relation, origin, role) -> bool:
    if not _points(kg, source, relation, True, target):
        return False
    if not _points(kg, source, "origin", origin, target):
        return False
    if role is None:
        return True
    return _points(kg, source, "role", role, target)


def _points(kg, source, prop, value, target) -> bool:
    return any(
        node == target
        for node, _strength in kg.neighbors_by_relation(source, prop, value)
    )


def _field(statement: Statement, mention) -> str | None:
    found: list[tuple[int, int, str]] = []

    def add(field: str, span) -> None:
        if span is None:
            return
        start, end = span
        if start <= mention.span_start and mention.span_end <= end:
            found.append((end - start, _FIELD_RANK[field], field))

    add("unit", statement.unit)
    add("subject", statement.subject)
    add("condition", statement.condition)
    add("required_behaviour", statement.required_behaviour)
    add("exception", statement.exception)
    for span in statement.values_and_timing:
        add("values_and_timing", span)
    if not found:
        return None
    found.sort()
    return found[0][2]


def _linked_key(tables: ResolutionTables, mention_id: int) -> str | None:
    rows = tables.conn.execute(
        "SELECT DISTINCT entity_key FROM link_decisions WHERE mention_id = ?",
        (mention_id,),
    ).fetchall()
    sames = []
    for row in rows:
        decision = decision_in_force(tables, mention_id, row["entity_key"])
        if decision is not None and decision.verdict == "same":
            sames.append(row["entity_key"])
    if len(sames) != 1:
        return None
    return sames[0]


def _split_key(key: str) -> tuple[str, str, str]:
    parts = key.split(" | ")
    if len(parts) != 3 or any(part == "" for part in parts):
        raise ValueError("entity key must be 'type | scope | native_id'")
    return parts[0], parts[1], parts[2]


def _report(*, mentions, linked, statements, edge_rows, rejected, unlinked_ids) -> dict:
    by_relation = {name: 0 for name in _RELATIONS}
    for row in edge_rows:
        by_relation[row["relation"]] += 1
    nothing = statements == 0
    no_mentions = mentions == 0
    if nothing:
        shown = {name: None for name in _RELATIONS}
    else:
        shown = by_relation
    return {
        "mentions": None if no_mentions else mentions,
        "mentions_linked": None if no_mentions else linked,
        "subject_unlinked": None if nothing else len(unlinked_ids),
        "edges": None if nothing else len(edge_rows),
        "rejected": None if nothing else rejected,
        "n_mentions": mentions,
        "n_statements": statements,
        "n_edges": len(edge_rows),
        "n_documents": 0 if nothing and no_mentions else 1,
        "by_relation": shown,
        "subject_unlinked_ids": () if nothing else unlinked_ids,
        "edge_rows": tuple(edge_rows),
    }


class _Nodes:
    def __init__(self, kg):
        self.kg = kg
        self.by_key: dict[str, int] = {}

    def get(self, type_name: str, scope: str, native_id: str):
        key = f"{type_name} | {scope} | {native_id}"
        cached = self.by_key.get(key)
        if cached is not None:
            return key, cached
        found = _find(self.kg, type_name, native_id)
        if found is None:
            found = self.kg.add_node(text=native_id, label=type_name)
        self.by_key[key] = found
        return key, found


def _find(kg, type_name: str, native_id: str):
    for node_id in range(kg.node_id_space()):
        if kg.is_node_deleted(node_id):
            continue
        if kg.get_node_label(node_id) != type_name:
            continue
        text = kg.get_node_text(node_id)
        if text == native_id or text.startswith(native_id + "\n"):
            return node_id
    return None


def _ordered(statements: Sequence[Statement], source_id: str, item_id: str) -> list[Statement]:
    if isinstance(statements, (str, bytes)) or not isinstance(statements, Sequence):
        raise TypeError("statements must be a sequence of Statement values")
    rows = list(statements)
    for statement in rows:
        if not isinstance(statement, Statement):
            raise TypeError("statements must be Statement values")
        if statement.source_id != source_id or statement.item_id != item_id:
            raise ValueError(
                f"statement {statement.source_id}|{statement.item_id} does not belong "
                f"to the item being linked ({source_id}|{item_id})"
            )
        if statement.origin not in _ORIGINS:
            raise ValueError("origin must be rule or model")
    return [
        statement
        for _, statement in sorted(
            enumerate(rows),
            key=lambda pair: (pair[1].unit[0], pair[1].unit[1], pair[0]),
        )
    ]


def _definition_kind() -> str:
    """The statement kind a ``defines`` edge belongs to, from the schema file."""
    cached = getattr(_definition_kind, "value", None)
    if cached is not None:
        return cached
    from grap_mcp.schema.loader import load_automotive_schema

    matches = [
        kind for kind in load_automotive_schema().statements["kinds"]
        if kind.startswith(_DEFINITION_STEM)
    ]
    if len(matches) != 1:
        raise ValueError("the schema file must name one statement kind for a definition")
    _definition_kind.value = matches[0]
    return matches[0]


def _is_requirement_item(kind_hint: str) -> bool:
    """True when the item is a Requirement.

    The reader's hint is the schema type's own name, folded. ``has_statement``
    is written only then.
    """
    cached = getattr(_is_requirement_item, "hint", None)
    if cached is None:
        from grap_mcp.schema.loader import load_automotive_schema

        for node in load_automotive_schema().node_types:
            if node.name == "Requirement":
                cached = node.name.casefold()
                break
        if cached is None:
            raise ValueError("the schema file has no Requirement type")
        _is_requirement_item.hint = cached
    return kind_hint == cached


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string")
    if " | " in value:
        raise ValueError(f"{field} must not contain ' | '")
    return value


def _optional(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required(value, field)


def _cell(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)
