"""ST-05: statement edges from the §6 table (spec-statements.md).

One fixture per row. A mention is linked only by a Stage 1 decision (E-14
identifier match, or E-15 self-reference). The first linked subject mention
is ``about`` unless the statement is a definition of a ``Term``, which is
``defines``. A statement whose subject links to nothing is still written,
with no ``about`` edge, and counted as ``subject_unlinked``.
"""

import hashlib
import io
from contextlib import redirect_stdout

import pygrap

from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.resolution.alias import link_defined
from grap_mcp.kee.resolution.exact import IdentifiedEntity, link_identifiers
from grap_mcp.kee.resolution.tables import ResolutionTables
from grap_mcp.kee.statements.mentions import write_statement_mentions
from grap_mcp.kee.statements.tables import Statement

DECIDED_AT = "2026-11-14T00:00:00Z"
SOURCE = "spec"
ITEM = "spec#0"

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


def _span(text, quote, start=0):
    at = text.index(quote, start)
    return (at, at + len(quote))


def _statement(text, **overrides):
    fields = dict(
        source_id=SOURCE,
        item_id=ITEM,
        unit=(0, len(text)),
        kind="requirement",
        subject=None,
        condition=None,
        required_behaviour=None,
        values_and_timing=(),
        exception=None,
        origin="rule",
        extractor="ruleset 1",
    )
    fields.update(overrides)
    return Statement(**fields)


def _entity(type_name, native_id):
    return IdentifiedEntity(type=type_name, native_id=native_id, source_id=SOURCE)


def _key(type_name, native_id, scope=SOURCE):
    return f"{type_name} | {scope} | {native_id}"


def _statement_key(n):
    return _key("Statement", f"{SOURCE}|{ITEM}|{n}")


def _line(mentions, linked, unlinked, edges, rejected, *, n_mentions, n_statements,
          n_edges, n_documents, by_relation):
    cells = " ".join(f"{name}={by_relation[name]}" for name in _RELATIONS)
    return (
        f"mentions={mentions} mentions_linked={linked} subject_unlinked={unlinked} "
        f"edges={edges} rejected={rejected} "
        f"n_mentions={n_mentions} n_statements={n_statements} n_edges={n_edges} "
        f"n_documents={n_documents} {cells}"
    )


def _node(kg, label, native_id):
    found = []
    for i in range(kg.node_id_space()):
        if kg.is_node_deleted(i):
            continue
        if kg.get_node_label(i) != label:
            continue
        text = kg.get_node_text(i)
        if text == native_id or text.startswith(native_id + "\n"):
            found.append(i)
    assert len(found) == 1, (label, native_id, found)
    return found[0]


def _rows(report, relation, to_key):
    return [
        row for row in report["edge_rows"]
        if row["relation"] == relation and row["to_key"] == to_key
    ]


def _assert_props(row, relation, origin, role):
    assert row["origin"] == origin
    assert row["role"] == role
    assert row["from_type"] == ("Requirement" if relation == "has_statement" else "Statement")
    assert "confidence" not in row["properties"]
    assert row["properties"]["origin"] == origin
    assert row["properties"][relation] is True
    if relation == "refers_to":
        assert row["properties"]["role"] == role
    else:
        assert "role" not in row["properties"]


def _projection():
    return {"nodes": [], "edges": [], "counts": {}}


def _project_node(kg, projection, type_name, native_id, source_id, scope):
    key = f"{type_name} | {scope} | {native_id}"
    for node in projection["nodes"]:
        if node["key"] == key:
            return node
    engine_id = kg.add_node(text=native_id, label=type_name)
    node = {
        "key": key,
        "type": type_name,
        "scope": scope,
        "source_id": source_id,
        "native_id": native_id,
        "engine_id": engine_id,
    }
    projection["nodes"].append(node)
    return node


def _seed(kg, projection, entities, kind_hint):
    """Item, Requirement, and the linked entities. This call creates no entity."""
    _project_node(kg, projection, "Item", ITEM, SOURCE, SOURCE)
    if kind_hint == "requirement":
        _project_node(kg, projection, "Requirement", ITEM, SOURCE, SOURCE)
    for entity in entities:
        release = entity.release
        scope = entity.source_id if release is None else f"{entity.source_id}@{release}"
        _project_node(
            kg, projection, entity.type, entity.native_id, entity.source_id, scope,
        )


def _statement_text(statement_id, rows):
    """The two-line node text. Each digest line names that edge's target engine id."""
    ordered = sorted(
        rows, key=lambda row: (row["relation"], row["to_key"], row["role"] or ""),
    )
    payload = "\n".join(
        f"{row['relation']}|{row['to_key']}|{row['to_engine_id']}|{row['role'] or ''}|{row['origin']}"
        for row in ordered
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{statement_id}\nedges:{digest}"


def _entry(projection, type_name, native_id, scope=SOURCE):
    key = f"{type_name} | {scope} | {native_id}"
    found = [node for node in projection["nodes"] if node["key"] == key]
    assert len(found) == 1, (key, found)
    return found[0]


def _ids(pairs):
    return [node for node, _strength in pairs]


def _run(tmp_path, text, statements, entities, *, kind_hint, defined=None, repeat=False):
    from grap_mcp.kee.statements.edges import write_statement_edges

    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    seeded = list(entities)
    if defined is not None:
        seeded.extend(defined["entities"])
    _seed(kg, projection, seeded, kind_hint)
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, statements)
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            link_identifiers(tables, mentions, entities, decided_at=DECIDED_AT)
            if defined is not None:
                link_defined(
                    tables,
                    mentions,
                    defined["entities"],
                    [],
                    subjects=defined["subjects"],
                    languages=defined["languages"],
                    decided_at=DECIDED_AT,
                )
        reports = []
        printed = []
        seen = None
        for _ in range(2 if repeat else 1):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                report = write_statement_edges(
                    tables, kg, projection, SOURCE, ITEM, statements, kind_hint=kind_hint,
                )
            reports.append(report)
            printed.append(buffer.getvalue())
            if seen is None:
                seen = (kg.node_count(), kg.edge_count())
            else:
                assert (kg.node_count(), kg.edge_count()) == seen
                assert reports[-1]["edge_rows"] == reports[0]["edge_rows"]
    return reports[0], printed[0], kg


def test_definition_subject_term_is_defines(tmp_path, capsys):
    """Row 1: a definition whose subject links to a Term is ``defines``, not ``about``."""
    text = "Com_Timeout means the limit."
    statement = _statement(
        text,
        kind="definition",
        subject=_span(text, "Com_Timeout"),
        required_behaviour=_span(text, "means the limit."),
        origin="model",
    )
    report, printed, kg = _run(
        tmp_path, text, [statement], [_entity("Term", "Com_Timeout")], kind_hint="text",
    )
    term = _key("Term", "Com_Timeout")
    rows = _rows(report, "defines", term)
    assert len(rows) == 1
    _assert_props(rows[0], "defines", "model", None)
    assert _rows(report, "about", term) == []
    assert report["subject_unlinked"] == 0
    assert report["subject_unlinked_ids"] == ()
    statement_node = _node(kg, "Statement", f"{SOURCE}|{ITEM}|0")
    term_node = _node(kg, "Term", "Com_Timeout")
    item_node = _node(kg, "Item", ITEM)
    assert kg.neighbors_by_relation(statement_node, "defines", True) == [(term_node, 1.0)]
    assert kg.neighbors_by_relation(statement_node, "about", True) == []
    assert kg.neighbors_by_relation(statement_node, "stated_in", True) == [(item_node, 1.0)]
    assert set(kg.neighbors_by_relation(statement_node, "origin", "model")) == {
        (term_node, 1.0),
        (item_node, 1.0),
    }
    assert _line(
        1, 1, 0, 2, 0,
        n_mentions=1, n_statements=1, n_edges=2, n_documents=1,
        by_relation={
            "about": 0, "defines": 1, "applies_in": 0, "configured_by": 0,
            "raises": 0, "refers_to": 0, "stated_in": 1, "has_statement": 0,
        },
    ) in printed
    capsys.readouterr()


def test_subject_about_is_the_first_linked_mention_only(tmp_path):
    """Row 2: only the first linked subject mention is ``about``.

    BrakeModule is that mention. VehSpdFL is also in the subject and links,
    so it is ``refers_to`` with role ``subject``, not a second ``about``.
    """
    text = "The BrakeModule and VehSpdFL shall start."
    statement = _statement(
        text,
        subject=_span(text, "The BrakeModule and VehSpdFL"),
        required_behaviour=_span(text, "shall start."),
    )
    report, printed, kg = _run(
        tmp_path, text, [statement],
        [_entity("Component", "BrakeModule"), _entity("Signal", "VehSpdFL")],
        kind_hint="requirement",
    )
    component = _key("Component", "BrakeModule")
    signal = _key("Signal", "VehSpdFL")
    about = _rows(report, "about", component)
    assert len(about) == 1
    _assert_props(about[0], "about", "rule", None)
    referred = _rows(report, "refers_to", signal)
    assert len(referred) == 1
    _assert_props(referred[0], "refers_to", "rule", "subject")
    assert _rows(report, "about", signal) == []
    assert report["subject_unlinked"] == 0
    statement_node = _node(kg, "Statement", f"{SOURCE}|{ITEM}|0")
    component_node = _node(kg, "Component", "BrakeModule")
    signal_node = _node(kg, "Signal", "VehSpdFL")
    item_node = _node(kg, "Item", ITEM)
    requirement = _node(kg, "Requirement", ITEM)
    assert kg.neighbors_by_relation(statement_node, "about", True) == [(component_node, 1.0)]
    assert kg.neighbors_by_relation(statement_node, "refers_to", True) == [(signal_node, 1.0)]
    assert kg.neighbors_by_relation(statement_node, "role", "subject") == [(signal_node, 1.0)]
    assert kg.neighbors_by_relation(requirement, "has_statement", True) == [(statement_node, 1.0)]
    assert kg.neighbors_by_relation(statement_node, "stated_in", True) == [(item_node, 1.0)]
    assert _line(
        3, 2, 0, 4, 0,
        n_mentions=3, n_statements=1, n_edges=4, n_documents=1,
        by_relation={
            "about": 1, "defines": 0, "applies_in": 0, "configured_by": 0,
            "raises": 0, "refers_to": 1, "stated_in": 1, "has_statement": 1,
        },
    ) in printed


def test_condition_state_is_applies_in(tmp_path):
    """Row 3: a State in the condition is ``applies_in``.

    The subject is the self-reference ``The module``, linked by E-15 to the
    component the source declares. The state is linked by E-14.
    """
    text = "The module shall transmit when VehMode is active."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        condition=_span(text, "when VehMode is active"),
        required_behaviour=_span(text, "shall transmit"),
    )
    component = _entity("Component", "Com")
    report, printed, kg = _run(
        tmp_path, text, [statement], [_entity("State", "VehMode")],
        kind_hint="requirement",
        defined={
            "entities": [component],
            "subjects": {SOURCE: [component]},
            "languages": {SOURCE: "en"},
        },
    )
    state = _key("State", "VehMode")
    comp = _key("Component", "Com")
    applies = _rows(report, "applies_in", state)
    assert len(applies) == 1
    _assert_props(applies[0], "applies_in", "rule", None)
    assert len(_rows(report, "about", comp)) == 1
    assert _rows(report, "refers_to", state) == []
    statement_node = _node(kg, "Statement", f"{SOURCE}|{ITEM}|0")
    state_node = _node(kg, "State", "VehMode")
    assert kg.neighbors_by_relation(statement_node, "applies_in", True) == [(state_node, 1.0)]
    assert kg.neighbors_by_relation(statement_node, "origin", "rule")
    assert _line(
        2, 2, 0, 4, 0,
        n_mentions=2, n_statements=1, n_edges=4, n_documents=1,
        by_relation={
            "about": 1, "defines": 0, "applies_in": 1, "configured_by": 0,
            "raises": 0, "refers_to": 0, "stated_in": 1, "has_statement": 1,
        },
    ) in printed


def test_parameter_in_any_field_is_configured_by(tmp_path):
    """Row 4: a Parameter in any field is ``configured_by``.

    This one sits in the exception, so the condition and behaviour rows do
    not decide it.
    """
    text = "The module shall start unless Cal_Timeout is set."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall start"),
        exception=_span(text, "unless Cal_Timeout is set."),
    )
    report, printed, kg = _run(
        tmp_path, text, [statement],
        [_entity("Component", "The module"), _entity("Parameter", "Cal_Timeout")],
        kind_hint="requirement",
    )
    parameter = _key("Parameter", "Cal_Timeout")
    rows = _rows(report, "configured_by", parameter)
    assert len(rows) == 1
    _assert_props(rows[0], "configured_by", "rule", None)
    assert _rows(report, "refers_to", parameter) == []
    statement_node = _node(kg, "Statement", f"{SOURCE}|{ITEM}|0")
    parameter_node = _node(kg, "Parameter", "Cal_Timeout")
    assert kg.neighbors_by_relation(statement_node, "configured_by", True) == [
        (parameter_node, 1.0),
    ]
    assert kg.neighbors_by_relation(statement_node, "refers_to", True) == []
    assert _line(
        2, 2, 0, 4, 0,
        n_mentions=2, n_statements=1, n_edges=4, n_documents=1,
        by_relation={
            "about": 1, "defines": 0, "applies_in": 0, "configured_by": 1,
            "raises": 0, "refers_to": 0, "stated_in": 1, "has_statement": 1,
        },
    ) in printed


def test_fault_in_behaviour_or_values_is_raises(tmp_path):
    """Row 5: a Fault in required behaviour or in values_and_timing is ``raises``."""
    text = "The module shall report Det_Err and return E_NOT_OK."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall report Det_Err and return E_NOT_OK."),
        values_and_timing=(_span(text, "E_NOT_OK"),),
    )
    report, printed, kg = _run(
        tmp_path, text, [statement],
        [
            _entity("Component", "The module"),
            _entity("Fault", "Det_Err"),
            _entity("Fault", "E_NOT_OK"),
        ],
        kind_hint="requirement",
    )
    det = _key("Fault", "Det_Err")
    code = _key("Fault", "E_NOT_OK")
    assert len(_rows(report, "raises", det)) == 1
    assert len(_rows(report, "raises", code)) == 1
    _assert_props(_rows(report, "raises", det)[0], "raises", "rule", None)
    _assert_props(_rows(report, "raises", code)[0], "raises", "rule", None)
    assert _rows(report, "refers_to", det) == []
    assert _rows(report, "refers_to", code) == []
    statement_node = _node(kg, "Statement", f"{SOURCE}|{ITEM}|0")
    det_node = _node(kg, "Fault", "Det_Err")
    code_node = _node(kg, "Fault", "E_NOT_OK")
    assert set(kg.neighbors_by_relation(statement_node, "raises", True)) == {
        (det_node, 1.0),
        (code_node, 1.0),
    }
    assert _line(
        3, 3, 0, 5, 0,
        n_mentions=3, n_statements=1, n_edges=5, n_documents=1,
        by_relation={
            "about": 1, "defines": 0, "applies_in": 0, "configured_by": 0,
            "raises": 2, "refers_to": 0, "stated_in": 1, "has_statement": 1,
        },
    ) in printed


def test_anywhere_else_is_refers_to_with_field_role(tmp_path):
    """Row 6: anything the earlier rows do not name is ``refers_to``.

    The role is the field the mention was read from.
    """
    text = "The module shall read VehSpdFL."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall read VehSpdFL."),
    )
    report, printed, kg = _run(
        tmp_path, text, [statement],
        [_entity("Component", "The module"), _entity("Signal", "VehSpdFL")],
        kind_hint="requirement",
    )
    signal = _key("Signal", "VehSpdFL")
    rows = _rows(report, "refers_to", signal)
    assert len(rows) == 1
    _assert_props(rows[0], "refers_to", "rule", "required_behaviour")
    assert _rows(report, "about", signal) == []
    statement_node = _node(kg, "Statement", f"{SOURCE}|{ITEM}|0")
    signal_node = _node(kg, "Signal", "VehSpdFL")
    assert kg.neighbors_by_relation(statement_node, "refers_to", True) == [(signal_node, 1.0)]
    assert kg.neighbors_by_relation(statement_node, "role", "required_behaviour") == [
        (signal_node, 1.0),
    ]
    assert _line(
        2, 2, 0, 4, 0,
        n_mentions=2, n_statements=1, n_edges=4, n_documents=1,
        by_relation={
            "about": 1, "defines": 0, "applies_in": 0, "configured_by": 0,
            "raises": 0, "refers_to": 1, "stated_in": 1, "has_statement": 1,
        },
    ) in printed


def test_subject_unlinked_is_counted_and_the_statement_is_written(tmp_path):
    """A statement with no linked subject is written, without ``about``.

    One of the two statements links. The count is 1, over 2 statements and
    1 document. A second call writes nothing more.
    """
    text = "The module shall start. Com_SendSignal shall stop."
    first = _statement(
        text,
        unit=_span(text, "The module shall start."),
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall start"),
    )
    second = _statement(
        text,
        unit=_span(text, "Com_SendSignal shall stop."),
        subject=_span(text, "Com_SendSignal"),
        required_behaviour=_span(text, "shall stop."),
        origin="model",
    )
    report, printed, kg = _run(
        tmp_path, text, [first, second], [_entity("Signal", "Com_SendSignal")],
        kind_hint="requirement", repeat=True,
    )
    assert report["subject_unlinked"] == 1
    assert report["subject_unlinked_ids"] == (f"{SOURCE}|{ITEM}|0",)
    assert report["n_statements"] == 2
    assert report["n_documents"] == 1
    signal = _key("Signal", "Com_SendSignal")
    assert _rows(report, "about", signal)[0]["origin"] == "model"
    assert _rows(report, "about", _key("Component", "The module")) == []
    unlinked = _node(kg, "Statement", f"{SOURCE}|{ITEM}|0")
    linked = _node(kg, "Statement", f"{SOURCE}|{ITEM}|1")
    item_node = _node(kg, "Item", ITEM)
    requirement = _node(kg, "Requirement", ITEM)
    signal_node = _node(kg, "Signal", "Com_SendSignal")
    assert kg.neighbors_by_relation(unlinked, "about", True) == []
    assert kg.neighbors_by_relation(unlinked, "stated_in", True) == [(item_node, 1.0)]
    assert kg.neighbors_by_relation(unlinked, "origin", "rule") == [(item_node, 1.0)]
    assert kg.neighbors_by_relation(linked, "about", True) == [(signal_node, 1.0)]
    assert set(kg.neighbors_by_relation(requirement, "has_statement", True)) == {
        (unlinked, 1.0),
        (linked, 1.0),
    }
    stated = _rows(report, "stated_in", _key("Item", ITEM))
    assert {row["origin"] for row in stated} == {"rule", "model"}
    assert all("confidence" not in row["properties"] for row in report["edge_rows"])
    assert _line(
        2, 1, 1, 5, 0,
        n_mentions=2, n_statements=2, n_edges=5, n_documents=1,
        by_relation={
            "about": 1, "defines": 0, "applies_in": 0, "configured_by": 0,
            "raises": 0, "refers_to": 0, "stated_in": 2, "has_statement": 2,
        },
    ) in printed


def test_nothing_to_measure_prints_na(tmp_path, capsys):
    """No statements and no mentions is n/a, with zero denominators."""
    from grap_mcp.kee.statements.edges import write_statement_edges

    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, _projection(), SOURCE, ITEM, [], kind_hint="text",
            )
        printed = buffer.getvalue()
    assert report["subject_unlinked"] is None
    assert report["mentions"] is None
    assert report["mentions_linked"] is None
    assert report["edges"] is None
    assert report["n_statements"] == 0
    assert report["n_mentions"] == 0
    assert report["n_edges"] == 0
    assert report["n_documents"] == 0
    assert report["edge_rows"] == ()
    assert kg.node_count() == 0
    na = {name: "n/a" for name in _RELATIONS}
    assert _line(
        "n/a", "n/a", "n/a", "n/a", "n/a",
        n_mentions=0, n_statements=0, n_edges=0, n_documents=0, by_relation=na,
    ) in printed
    capsys.readouterr()


def test_same_native_id_under_two_scopes_uses_the_statement_scope(tmp_path):
    """The edge follows the full key of the statement's own scope.

    The same native id also exists in another scope, and that node is
    created first. It gains no edge. The statement's ``about`` neighbor
    is the node of its own scope.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges

    other = "other"
    text = "VehSpdFL shall be fresh."
    statement = _statement(
        text,
        subject=_span(text, "VehSpdFL"),
        required_behaviour=_span(text, "shall be fresh."),
    )
    own = _entity("Signal", "VehSpdFL")
    foreign = IdentifiedEntity(type="Signal", native_id="VehSpdFL", source_id=other)
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    foreign_node = _project_node(kg, projection, "Signal", "VehSpdFL", other, other)
    own_node = _project_node(kg, projection, "Signal", "VehSpdFL", SOURCE, SOURCE)
    _project_node(kg, projection, "Item", ITEM, SOURCE, SOURCE)
    _project_node(kg, projection, "Requirement", ITEM, SOURCE, SOURCE)
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [statement])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            link_identifiers(tables, mentions, [own, foreign], decided_at=DECIDED_AT)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
    own_key = _key("Signal", "VehSpdFL")
    foreign_key = _key("Signal", "VehSpdFL", other)
    assert len(_rows(report, "about", own_key)) == 1
    assert _rows(report, "about", foreign_key) == []
    statement_node = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|0")
    assert kg.neighbors_by_relation(statement_node["engine_id"], "about", True) == [
        (own_node["engine_id"], 1.0),
    ]
    for prop, value in (
        ("about", True),
        ("refers_to", True),
        ("origin", "rule"),
        ("defines", True),
    ):
        assert kg.neighbors_by_relation(foreign_node["engine_id"], prop, value) == []
        assert kg.predecessors_by_relation(foreign_node["engine_id"], prop, value) == []


def test_absent_linked_key_counts_entity_missing(tmp_path):
    """A linked key that is not in the projection writes no edge.

    entity_missing is 1, printed with n_mentions. No entity node is
    created: the projected nodes other than the Statement are unchanged,
    and node_count grows only by that Statement.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges

    text = "VehSpdFL shall be fresh."
    statement = _statement(
        text,
        subject=_span(text, "VehSpdFL"),
        required_behaviour=_span(text, "shall be fresh."),
    )
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    _project_node(kg, projection, "Item", ITEM, SOURCE, SOURCE)
    _project_node(kg, projection, "Requirement", ITEM, SOURCE, SOURCE)
    before = kg.node_count()
    projected = [dict(node) for node in projection["nodes"]]
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [statement])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            link_identifiers(
                tables, mentions, [_entity("Signal", "VehSpdFL")], decided_at=DECIDED_AT,
            )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
        printed = buffer.getvalue()
    assert report["entity_missing"] == 1
    assert report["n_mentions"] == 1
    assert "entity_missing=1" in printed
    assert "n_mentions=1" in printed
    assert _rows(report, "about", _key("Signal", "VehSpdFL")) == []
    assert [node for node in projection["nodes"] if node["type"] != "Statement"] == projected
    assert kg.node_count() == before + 1
    assert not any(
        not kg.is_node_deleted(node_id) and kg.get_node_label(node_id) == "Signal"
        for node_id in range(kg.node_id_space())
    )


def test_same_call_twice_leaves_node_count_and_edge_count(tmp_path):
    """A second call with the same rows writes nothing.

    statements_unchanged equals n_statements. node_count() and edge_count()
    are both unchanged. remove_node is not used, so edge_count() staying
    put is meaningful here. The node text is the statement id and the
    digest of its edge rows.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges

    text = "The module shall start. Com_SendSignal shall stop."
    first = _statement(
        text,
        unit=_span(text, "The module shall start."),
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall start"),
    )
    second = _statement(
        text,
        unit=_span(text, "Com_SendSignal shall stop."),
        subject=_span(text, "Com_SendSignal"),
        required_behaviour=_span(text, "shall stop."),
    )
    entities = [_entity("Component", "The module"), _entity("Signal", "Com_SendSignal")]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    _seed(kg, projection, entities, "requirement")
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [first, second])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            link_identifiers(tables, mentions, entities, decided_at=DECIDED_AT)
        first_buffer = io.StringIO()
        with redirect_stdout(first_buffer):
            first_report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [first, second],
                kind_hint="requirement",
            )
        nodes = kg.node_count()
        edges = kg.edge_count()
        for index in (0, 1):
            statement_id = f"{SOURCE}|{ITEM}|{index}"
            rows = [
                row for row in first_report["edge_rows"]
                if row["statement_id"] == statement_id
            ]
            entry = _entry(projection, "Statement", statement_id)
            assert kg.get_node_text(entry["engine_id"]) == _statement_text(statement_id, rows)
        second_buffer = io.StringIO()
        with redirect_stdout(second_buffer):
            second_report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [first, second],
                kind_hint="requirement",
            )
        printed = second_buffer.getvalue()
    n = second_report["n_statements"]
    assert second_report["statements_unchanged"] == n
    assert n == 2
    assert second_report["statements_rewritten"] == 0
    assert second_report["statements_removed"] == 0
    assert kg.node_count() == nodes
    assert kg.edge_count() == edges
    assert f"statements_unchanged={n}" in printed
    assert f"n_statements={n}" in printed


def test_dropped_statement_is_removed_from_traversal(tmp_path):
    """Two statements, then one: the dropped node leaves traversal.

    node_count() drops by one and the removed node is_node_deleted.
    neighbors_by_relation and predecessors_by_relation from the
    Requirement and the Item no longer return it. edge_count() is not
    asserted: remove_node hides edges and does not lower that counter.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges

    text = "The module shall start. Com_SendSignal shall stop."
    first = _statement(
        text,
        unit=_span(text, "The module shall start."),
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall start"),
    )
    second = _statement(
        text,
        unit=_span(text, "Com_SendSignal shall stop."),
        subject=_span(text, "Com_SendSignal"),
        required_behaviour=_span(text, "shall stop."),
    )
    entities = [_entity("Component", "The module"), _entity("Signal", "Com_SendSignal")]
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    _seed(kg, projection, entities, "requirement")
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [first, second])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            link_identifiers(tables, mentions, entities, decided_at=DECIDED_AT)
        with redirect_stdout(io.StringIO()):
            write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [first, second],
                kind_hint="requirement",
            )
        kept = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|0")["engine_id"]
        removed = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|1")["engine_id"]
        requirement = _entry(projection, "Requirement", ITEM)["engine_id"]
        item = _entry(projection, "Item", ITEM)["engine_id"]
        before = kg.node_count()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [first], kind_hint="requirement",
            )
        printed = buffer.getvalue()
    assert kg.node_count() == before - 1
    assert kg.is_node_deleted(removed)
    assert not kg.is_node_deleted(kept)
    assert report["statements_removed"] == 1
    assert report["n_statements"] == 1
    assert "statements_removed=1" in printed
    assert "n_statements=1" in printed
    assert all(
        node.get("engine_id") != removed
        for node in projection["nodes"]
    )
    for node in (requirement, item):
        for prop, value in (("has_statement", True), ("stated_in", True)):
            assert removed not in _ids(kg.neighbors_by_relation(node, prop, value))
            assert removed not in _ids(kg.predecessors_by_relation(node, prop, value))
    assert kept in _ids(kg.neighbors_by_relation(requirement, "has_statement", True))
    assert kept in _ids(kg.predecessors_by_relation(item, "stated_in", True))


def test_changed_link_rewrites_the_statement_node(tmp_path):
    """The same statement with one link changed is rewritten.

    statements_rewritten is 1. The old node is deleted. The new node
    carries the new ``about`` edge and not the old one.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges

    text = "The module shall start."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall start."),
    )
    original = _entity("Component", "The module")
    replacement = _entity("Component", "Other")
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    _seed(kg, projection, [original, replacement], "requirement")
    original_id = _entry(projection, "Component", "The module")["engine_id"]
    replacement_id = _entry(projection, "Component", "Other")["engine_id"]
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [statement])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        quiet = io.StringIO()
        with redirect_stdout(quiet):
            link_identifiers(tables, mentions, [original], decided_at=DECIDED_AT)
        with redirect_stdout(io.StringIO()):
            write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
        old = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|0")["engine_id"]
        subject = next(mention for mention in mentions if mention.text == "The module")
        tables.append_decision(
            subject.mention_id,
            _key("Component", "The module"),
            "not_same",
            method="review",
            score=None,
            decider="human",
            decided_at="2026-11-15T00:00:00Z",
        )
        tables.append_decision(
            subject.mention_id,
            _key("Component", "Other"),
            "same",
            method="review",
            score=None,
            decider="human",
            decided_at="2026-11-15T00:00:00Z",
        )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
        printed = buffer.getvalue()
    assert report["statements_rewritten"] == 1
    assert report["n_statements"] == 1
    assert "statements_rewritten=1" in printed
    assert "n_statements=1" in printed
    assert kg.is_node_deleted(old)
    new = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|0")["engine_id"]
    assert new != old
    assert kg.neighbors_by_relation(new, "about", True) == [(replacement_id, 1.0)]
    assert original_id not in _ids(kg.neighbors_by_relation(new, "about", True))


def test_later_process_sees_saved_statement_entries(tmp_path):
    """save_projection and save() make the Statement entries visible later.

    A fresh projection object and a reopened graph run the replace path.
    The kept statement is unchanged. The dropped one is removed.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges
    from grap_mcp.schema.projection_store import load_projection, save_projection

    text = "The module shall start. Com_SendSignal shall stop."
    first = _statement(
        text,
        unit=_span(text, "The module shall start."),
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall start"),
    )
    second = _statement(
        text,
        unit=_span(text, "Com_SendSignal shall stop."),
        subject=_span(text, "Com_SendSignal"),
        required_behaviour=_span(text, "shall stop."),
    )
    entities = [_entity("Component", "The module"), _entity("Signal", "Com_SendSignal")]
    graph_dir = tmp_path / "kg"
    kg = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    projection = _projection()
    _seed(kg, projection, entities, "requirement")
    db_path = tmp_path / "graph_store.db"
    with GraphStore(str(db_path)) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [first, second])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        with redirect_stdout(io.StringIO()):
            link_identifiers(tables, mentions, entities, decided_at=DECIDED_AT)
            write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [first, second],
                kind_hint="requirement",
            )
    kept = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|0")["engine_id"]
    removed = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|1")["engine_id"]
    kg.save()
    path = tmp_path / "projection.json"
    save_projection(path, projection)
    loaded = load_projection(path)
    assert loaded is not projection
    assert _entry(loaded, "Statement", f"{SOURCE}|{ITEM}|0")["engine_id"] == kept
    assert _entry(loaded, "Statement", f"{SOURCE}|{ITEM}|1")["engine_id"] == removed
    reopened = pygrap.IndexedKnowledgeGraph(str(graph_dir))
    assert reopened is not kg
    before = reopened.node_count()
    with GraphStore(str(db_path)) as graph:
        tables = ResolutionTables(graph)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, reopened, loaded, SOURCE, ITEM, [first], kind_hint="requirement",
            )
    assert report["statements_unchanged"] == 1
    assert report["statements_removed"] == 1
    assert report["n_statements"] == 1
    assert reopened.node_count() == before - 1
    assert reopened.is_node_deleted(removed)
    assert not reopened.is_node_deleted(kept)
    assert _entry(loaded, "Statement", f"{SOURCE}|{ITEM}|0")["engine_id"] == kept
    assert all(node.get("engine_id") != removed for node in loaded["nodes"])


def test_replaced_entity_node_rewrites_the_statement(tmp_path):
    """A replaced entity is a new endpoint, so the statement is rewritten.

    Write the edges, remove_node the target entity, and give its key a new
    node in the projection. The second call reports statements_rewritten=1
    and the about edge reaches the new node.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges

    text = "VehSpdFL shall be fresh."
    statement = _statement(
        text,
        subject=_span(text, "VehSpdFL"),
        required_behaviour=_span(text, "shall be fresh."),
    )
    entity = _entity("Signal", "VehSpdFL")
    signal_key = _key("Signal", "VehSpdFL")
    statement_id = f"{SOURCE}|{ITEM}|0"
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    _seed(kg, projection, [entity], "requirement")
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [statement])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        with redirect_stdout(io.StringIO()):
            link_identifiers(tables, mentions, [entity], decided_at=DECIDED_AT)
            write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
        old_entity = _entry(projection, "Signal", "VehSpdFL")["engine_id"]
        old_statement = _entry(projection, "Statement", statement_id)["engine_id"]
        kg.remove_node(old_entity)
        new_entity = kg.add_node(text="VehSpdFL", label="Signal")
        for node in projection["nodes"]:
            if node["key"] == signal_key:
                node["engine_id"] = new_entity
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
        printed = buffer.getvalue()
    assert report["statements_rewritten"] == 1
    assert report["n_statements"] == 1
    assert "statements_rewritten=1" in printed
    assert "n_statements=1" in printed
    new_statement = _entry(projection, "Statement", statement_id)["engine_id"]
    assert new_statement != old_statement
    assert kg.is_node_deleted(old_statement)
    about = _rows(report, "about", signal_key)
    assert len(about) == 1
    assert about[0]["to_engine_id"] == new_entity
    assert kg.neighbors_by_relation(new_statement, "about", True) == [(new_entity, 1.0)]
    assert old_entity not in _ids(kg.neighbors_by_relation(new_statement, "about", True))
    rows = [row for row in report["edge_rows"] if row["statement_id"] == statement_id]
    assert kg.get_node_text(new_statement) == _statement_text(statement_id, rows)


def test_absent_item_counts_structural_missing(tmp_path):
    """An Item that is not a live node writes no stated_in edge.

    structural_missing is 1, printed with n_statements. The Item key is in
    structural_missing_keys and is not in the printed line. The call does
    not raise.
    """
    from grap_mcp.kee.statements.edges import write_statement_edges

    text = "The module shall start."
    statement = _statement(
        text,
        subject=_span(text, "The module"),
        required_behaviour=_span(text, "shall start."),
    )
    item_key = _key("Item", ITEM)
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    _project_node(kg, projection, "Requirement", ITEM, SOURCE, SOURCE)
    _project_node(kg, projection, "Component", "The module", SOURCE, SOURCE)
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [statement])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        with redirect_stdout(io.StringIO()):
            link_identifiers(
                tables, mentions, [_entity("Component", "The module")], decided_at=DECIDED_AT,
            )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
        printed = buffer.getvalue()
    assert report["structural_missing"] == 1
    assert report["n_statements"] == 1
    assert report["structural_missing_keys"] == (item_key,)
    assert item_key not in report["entity_missing_keys"]
    assert "structural_missing=1" in printed
    assert "n_statements=1" in printed
    assert item_key not in printed
    assert _rows(report, "stated_in", item_key) == []
    statement_node = _entry(projection, "Statement", f"{SOURCE}|{ITEM}|0")["engine_id"]
    assert kg.neighbors_by_relation(statement_node, "stated_in", True) == []


def test_absent_entity_key_is_returned_and_not_printed(tmp_path, capsys):
    """An absent entity key is returned and is not written to stdout."""
    from grap_mcp.kee.statements.edges import write_statement_edges

    text = "VehSpdFL shall be fresh."
    statement = _statement(
        text,
        subject=_span(text, "VehSpdFL"),
        required_behaviour=_span(text, "shall be fresh."),
    )
    signal_key = _key("Signal", "VehSpdFL")
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    projection = _projection()
    _project_node(kg, projection, "Item", ITEM, SOURCE, SOURCE)
    _project_node(kg, projection, "Requirement", ITEM, SOURCE, SOURCE)
    with GraphStore(str(tmp_path / "graph_store.db")) as graph:
        tables = ResolutionTables(graph)
        write_statement_mentions(tables, text, SOURCE, ITEM, [statement])
        mentions = tables.mentions_for_item(SOURCE, ITEM)
        with redirect_stdout(io.StringIO()):
            link_identifiers(
                tables, mentions, [_entity("Signal", "VehSpdFL")], decided_at=DECIDED_AT,
            )
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            report = write_statement_edges(
                tables, kg, projection, SOURCE, ITEM, [statement], kind_hint="requirement",
            )
        printed = buffer.getvalue()
    captured = capsys.readouterr()
    assert report["entity_missing"] == 1
    assert report["entity_missing_keys"] == (signal_key,)
    assert signal_key not in printed
    assert signal_key not in captured.out
    assert signal_key not in captured.err
    assert _rows(report, "about", signal_key) == []
