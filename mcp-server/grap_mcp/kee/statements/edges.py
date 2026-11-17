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

Endpoints are resolved by the full key ``type | scope | native_id`` through
``projection["nodes"]`` to ``engine_id``. The graph is not scanned. A linked
entity key that is not a live node writes no edge and is counted as
``entity_missing``. Its key is returned in ``entity_missing_keys``. Only
Statement nodes are created here. Item, Requirement, and entity nodes must
already be in the projection.

A ``stated_in`` or ``has_statement`` edge whose Item or Requirement is not a
live node is not written. That miss is ``structural_missing``, printed with
``n_statements``. It does not raise. The missing keys are returned in
``structural_missing_keys``. Neither key list is printed.

A Statement node's text is two lines: the statement id, then ``edges:`` and
the sha256 hex of its edge rows. The digest covers those rows sorted by
``(relation, to_key, role or "")``, each encoded
``relation|to_key|to_engine_id|role|origin`` and joined by a newline. Entity
nodes may be replaced. A live Statement is left untouched only when every
endpoint is live and the digest matches. Otherwise the old node, if any, is
removed with ``remove_node``, a new node is written, and its projection
entry is replaced. A live Statement of this item whose statement id is
absent from the new list is removed the same way and dropped from
``projection["nodes"]``.

``remove_node`` hides that node's edges from traversal and does not lower
``edge_count()``. A deletion shows up through ``neighbors_by_relation``,
``predecessors_by_relation``, ``is_node_deleted``, and ``node_count()``.
``edge_count()`` does not record it.

Every edge takes the statement's origin and has no confidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

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
    projection,
    source_id: str,
    item_id: str,
    statements: Sequence[Statement],
    *,
    kind_hint: str,
    release: str | None = None,
) -> dict:
    """Write one item's statement edges and print the counts.

    ``projection`` is the one ``save_projection`` stores. Keys resolve
    through ``projection["nodes"]``. The caller saves the projection again
    after this returns. Mentions must already be stored. This does not link
    them and does not change the mention rows. With nothing to measure, the
    counts are ``n/a``.
    """
    if not isinstance(tables, ResolutionTables):
        raise TypeError("tables must be ResolutionTables")
    if kg is None or not hasattr(kg, "add_node") or not hasattr(kg, "add_edge"):
        raise TypeError("kg must be a knowledge graph")
    if not isinstance(projection, Mapping) or not isinstance(projection.get("nodes"), list):
        raise TypeError("projection must have a nodes list")
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
    by_key = {
        node["key"]: node
        for node in projection["nodes"]
        if isinstance(node, dict) and isinstance(node.get("key"), str)
    }
    plans = []
    edge_rows: list[dict] = []
    unlinked_ids: list[str] = []
    rejected = 0
    entity_missing = 0
    entity_keys: list[str] = []
    structural_missing = 0
    structural_keys: list[str] = []
    for index, statement in enumerate(ordered):
        statement_id = f"{source_id}|{item_id}|{index}"
        if statement.statement_id is not None and statement.statement_id != statement_id:
            raise ValueError(
                f"statement_id {statement.statement_id} does not match {statement_id}"
            )
        rows, missed, refused, missing, missed_keys, struct_missing, struct_keys = _edges_for(
            kg, by_key, statement, statement_id, scope, item_id, kind_hint, mentions, links,
        )
        plans.append((statement_id, rows))
        edge_rows.extend(rows)
        rejected += refused
        entity_missing += missing
        entity_keys.extend(missed_keys)
        structural_missing += struct_missing
        structural_keys.extend(struct_keys)
        if missed:
            unlinked_ids.append(statement_id)

    unchanged, rewritten = _apply(kg, projection, by_key, scope, source_id, plans)
    removed = _remove_absent(kg, projection, by_key, scope, source_id, item_id, plans)
    report = _report(
        mentions=len(mentions),
        linked=len(links),
        statements=len(ordered),
        edge_rows=edge_rows,
        rejected=rejected,
        unlinked_ids=tuple(unlinked_ids),
        entity_missing=entity_missing,
        entity_missing_keys=entity_keys,
        structural_missing=structural_missing,
        structural_missing_keys=structural_keys,
        unchanged=unchanged,
        rewritten=rewritten,
        removed=removed,
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
        f"{cells} "
        f"entity_missing={_cell(report['entity_missing'])} "
        f"structural_missing={_cell(report['structural_missing'])} "
        f"statements_unchanged={_cell(report['statements_unchanged'])} "
        f"statements_rewritten={_cell(report['statements_rewritten'])} "
        f"statements_removed={_cell(report['statements_removed'])}"
    )


def _edges_for(kg, by_key, statement, statement_id, scope, item_id, kind_hint, mentions, links):
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
    missing = 0
    entity_keys: list[str] = []
    struct_rejected, struct_missing, struct_keys = _structural(
        kg, by_key, rows, statement_id, scope, item_id, kind_hint, origin,
    )
    rejected += struct_rejected
    statement_key = _node_key("Statement", scope, statement_id)
    for mention, field, key in placed:
        if key is None:
            continue
        entity_type, entity_scope, native_id = _split_key(key)
        relation, role = _choose(
            statement.kind, field, entity_type, mention.mention_id == first_subject,
        )
        status, missing_key = _plan(
            kg, by_key, rows,
            from_type="Statement", from_scope=scope, from_native=statement_id,
            relation=relation, role=role, origin=origin,
            to_type=entity_type, to_scope=entity_scope, to_native=native_id,
            statement_id=statement_id, statement_key=statement_key,
        )
        if status == "rejected":
            rejected += 1
        elif status == "missing":
            missing += 1
            entity_keys.append(missing_key)
    return (
        rows, first_subject is None, rejected, missing,
        entity_keys, struct_missing, struct_keys,
    )


def _structural(kg, by_key, rows, statement_id, scope, item_id, kind_hint, origin):
    """Schema refusals, and Item or Requirement misses.

    A missing Item or Requirement writes no edge and is not an entity miss.
    """
    rejected = 0
    missing = 0
    keys: list[str] = []
    statement_key = _node_key("Statement", scope, statement_id)
    for spec in (
        ("Statement", scope, statement_id, "stated_in", "Item", scope, item_id),
        ("Requirement", scope, item_id, "has_statement", "Statement", scope, statement_id),
    ):
        from_type, from_scope, from_native, relation, to_type, to_scope, to_native = spec
        if relation == "has_statement" and not _is_requirement_item(kind_hint):
            continue
        status, missing_key = _plan(
            kg, by_key, rows,
            from_type=from_type, from_scope=from_scope, from_native=from_native,
            relation=relation, role=None, origin=origin,
            to_type=to_type, to_scope=to_scope, to_native=to_native,
            statement_id=statement_id, statement_key=statement_key,
        )
        if status == "rejected":
            rejected += 1
        elif status == "missing":
            missing += 1
            keys.append(missing_key)
    return rejected, missing, keys


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


def _plan(kg, by_key, rows, *, from_type, from_scope, from_native, relation, role, origin,
          to_type, to_scope, to_native, statement_id, statement_key) -> tuple[str, str | None]:
    """Record one edge, or say why it was not recorded.

    ``missing`` means an endpoint other than this statement is not a live
    node in the projection. The statement itself is created later.
    """
    try:
        allowed = relation_allowed(from_type, relation, to_type)
    except ValueError:
        return "rejected", None
    if not allowed:
        return "rejected", None
    from_key = _node_key(from_type, from_scope, from_native)
    to_key = _node_key(to_type, to_scope, to_native)
    if from_key != statement_key and _live_id(kg, by_key.get(from_key)) is None:
        return "missing", from_key
    if to_key != statement_key and _live_id(kg, by_key.get(to_key)) is None:
        return "missing", to_key
    to_engine_id = None if to_key == statement_key else _live_id(kg, by_key.get(to_key))
    props = dict(_relation_properties(relation))
    props["origin"] = origin
    if relation == "refers_to":
        props["role"] = role
    rows.append({
        "relation": relation,
        "from_key": from_key,
        "to_key": to_key,
        "from_type": from_type,
        "to_type": to_type,
        "origin": origin,
        "role": role,
        "statement_id": statement_id,
        "to_engine_id": to_engine_id,
        "properties": props,
    })
    return "ok", None


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


def _report(
    *, mentions, linked, statements, edge_rows, rejected, unlinked_ids,
    entity_missing, entity_missing_keys, structural_missing, structural_missing_keys,
    unchanged, rewritten, removed,
) -> dict:
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
        "entity_missing": None if no_mentions else entity_missing,
        "entity_missing_keys": tuple(sorted(set(entity_missing_keys))),
        "structural_missing": None if nothing else structural_missing,
        "structural_missing_keys": (
            () if nothing else tuple(sorted(set(structural_missing_keys)))
        ),
        "statements_unchanged": None if _quiet(statements, removed) else unchanged,
        "statements_rewritten": None if _quiet(statements, removed) else rewritten,
        "statements_removed": None if _quiet(statements, removed) else removed,
    }


def _quiet(statements: int, removed: int) -> bool:
    return statements == 0 and removed == 0


def _apply(kg, projection, by_key, scope, source_id, plans) -> tuple[int, int]:
    unchanged = 0
    rewritten = 0
    for statement_id, rows in plans:
        key = _node_key("Statement", scope, statement_id)
        existing = _statement_engine(kg, by_key, key)
        if existing is not None:
            _bind_statement_engine(rows, key, existing)
            if (
                _endpoints_live(kg, by_key, rows, key)
                and _stored_digest(kg, existing, statement_id) == _digest(rows)
            ):
                unchanged += 1
                continue
            kg.remove_node(existing)
        engine_id = kg.add_node(text=statement_id, label="Statement")
        _bind_statement_engine(rows, key, engine_id)
        digest = _digest(rows)
        kg.update_node(engine_id, f"{statement_id}\nedges:{digest}")
        _put_node(projection, by_key, {
            "key": key,
            "type": "Statement",
            "scope": scope,
            "source_id": source_id,
            "native_id": statement_id,
            "engine_id": engine_id,
        })
        _write_rows(kg, by_key, rows, key, engine_id)
        rewritten += 1
    return unchanged, rewritten


def _remove_absent(kg, projection, by_key, scope, source_id, item_id, plans) -> int:
    keep = {statement_id for statement_id, _rows in plans}
    removed = 0
    for node in list(projection["nodes"]):
        if not _item_statement(node, scope, source_id, item_id):
            continue
        if node["native_id"] in keep:
            continue
        engine_id = _statement_engine(kg, by_key, node["key"])
        if engine_id is None:
            continue
        kg.remove_node(engine_id)
        _drop_node(projection, by_key, node["key"])
        removed += 1
    return removed


def _write_rows(kg, by_key, rows, statement_key, statement_engine) -> None:
    for row in rows:
        source = (
            statement_engine
            if row["from_key"] == statement_key
            else _live_id(kg, by_key[row["from_key"]])
        )
        target = (
            statement_engine
            if row["to_key"] == statement_key
            else _live_id(kg, by_key[row["to_key"]])
        )
        if not _present(kg, source, target, row["relation"], row["origin"], row["role"]):
            kg.add_edge(source, target, 1.0, row["properties"])


def _bind_statement_engine(rows, statement_key: str, engine_id: int) -> None:
    for row in rows:
        if row["to_key"] == statement_key:
            row["to_engine_id"] = engine_id


def _endpoints_live(kg, by_key, rows, statement_key: str) -> bool:
    """True when every endpoint named by these rows is a live node."""
    for row in rows:
        if row["to_key"] == statement_key:
            engine = row["to_engine_id"]
            if isinstance(engine, bool) or not isinstance(engine, int) or kg.is_node_deleted(engine):
                return False
        else:
            live = _live_id(kg, by_key.get(row["to_key"]))
            if live is None or live != row["to_engine_id"]:
                return False
        if row["from_key"] != statement_key and _live_id(kg, by_key.get(row["from_key"])) is None:
            return False
    return True


def _digest(rows) -> str:
    ordered = sorted(
        rows, key=lambda row: (row["relation"], row["to_key"], row["role"] or ""),
    )
    payload = "\n".join(
        f"{row['relation']}|{row['to_key']}|{row['to_engine_id']}|{row['role'] or ''}|{row['origin']}"
        for row in ordered
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stored_digest(kg, engine_id: int, statement_id: str) -> str | None:
    text = kg.get_node_text(engine_id)
    if not isinstance(text, str):
        return None
    lines = text.split("\n")
    prefix = "edges:"
    if len(lines) != 2 or lines[0] != statement_id or not lines[1].startswith(prefix):
        return None
    return lines[1][len(prefix):]


def _statement_engine(kg, by_key, key: str) -> int | None:
    engine_id = _live_id(kg, by_key.get(key))
    if engine_id is None:
        return None
    if kg.get_node_label(engine_id) != "Statement":
        return None
    return engine_id


def _live_id(kg, node) -> int | None:
    if not isinstance(node, dict):
        return None
    engine_id = node.get("engine_id")
    if isinstance(engine_id, bool) or not isinstance(engine_id, int):
        return None
    if kg.is_node_deleted(engine_id):
        return None
    return engine_id


def _put_node(projection, by_key, entry: dict) -> None:
    key = entry["key"]
    nodes = projection["nodes"]
    index = next(
        (
            i for i, node in enumerate(nodes)
            if isinstance(node, dict) and node.get("key") == key
        ),
        None,
    )
    if index is None:
        nodes.append(entry)
    else:
        nodes[index] = entry
        duplicates = [
            i for i, node in enumerate(nodes)
            if i != index and isinstance(node, dict) and node.get("key") == key
        ]
        for i in reversed(duplicates):
            del nodes[i]
    by_key[key] = entry


def _drop_node(projection, by_key, key: str) -> None:
    projection["nodes"][:] = [
        node for node in projection["nodes"]
        if not (isinstance(node, dict) and node.get("key") == key)
    ]
    by_key.pop(key, None)


def _item_statement(node, scope: str, source_id: str, item_id: str) -> bool:
    if not isinstance(node, dict):
        return False
    native = node.get("native_id")
    prefix = f"{source_id}|{item_id}|"
    return (
        node.get("type") == "Statement"
        and node.get("scope") == scope
        and node.get("source_id") == source_id
        and isinstance(native, str)
        and native.startswith(prefix)
        and native[len(prefix):].isdigit()
    )


def _node_key(type_name: str, scope: str, native_id: str) -> str:
    return f"{type_name} | {scope} | {native_id}"


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
