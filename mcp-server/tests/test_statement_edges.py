"""ST-05: statement edges from the §6 table (spec-statements.md).

One fixture per row. A mention is linked only by a Stage 1 decision (E-14
identifier match, or E-15 self-reference). The first linked subject mention
is ``about`` unless the statement is a definition of a ``Term``, which is
``defines``. A statement whose subject links to nothing is still written,
with no ``about`` edge, and counted as ``subject_unlinked``.
"""

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


def _run(tmp_path, text, statements, entities, *, kind_hint, defined=None, repeat=False):
    from grap_mcp.kee.statements.edges import write_statement_edges

    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
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
                    tables, kg, SOURCE, ITEM, statements, kind_hint=kind_hint,
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
                tables, kg, SOURCE, ITEM, [], kind_hint="text",
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
