"""P-12: select_requirements and fetch_context against the projected graph.

No model. fetch_context is schema §9 question 4: node keys within two steps
of each requirement's statements. The written JSON is the scorer's
testpoints document. An id that matches no Requirement is an error.
"""

import csv
import json
import sys
import textwrap
from pathlib import Path

import pygrap
import pytest

from grap_mcp.procedure_context import (
    GraphOperations,
    ScopeError,
    as_testpoints,
    fetch_context,
    format_summary,
    select_requirements,
    write_testpoints,
)
from grap_mcp.procedure_interpreter import interpret
from grap_mcp.procedure_runtime import load_procedure
from grap_mcp.readers import SourceItem, SourceLink
from grap_mcp.schema.project import project_items

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import score_gold  # noqa: E402


def _link(kind, target):
    return SourceLink(type=kind, target_id=target, role="", context=[])


def _item(item_id, links=(), *, kind_hint="text", attributes=None, release=None, source_id="fix"):
    return SourceItem(
        source_id=source_id,
        release=release,
        item_id=item_id,
        kind_hint=kind_hint,
        text=item_id,
        heading_path=[],
        attributes={} if attributes is None else dict(attributes),
        links=list(links),
        file=f"{source_id}.txt",
        sha256="0" * 64,
    )


def _tag(name):
    return {"tag": name}


def _items():
    """One projected graph for selection and for the two-step walk.

    REQ-LOWER refines REQ-PARENT, which refines REQ-UPPER.
    REQ-GRAND refines REQ-LOWER. REQ-CHILD is part_of REQ-PARENT and does
    not trace to it. REQ-REL is the same source at another release.
    """
    return [
        _item("REQ-PARENT", [_link("traces_to", "REQ-UPPER")], kind_hint="requirement"),
        _item(
            "REQ-LOWER",
            [_link("traces_to", "REQ-PARENT"), _link("has_statement", "ST-L")],
            kind_hint="requirement",
        ),
        _item("REQ-GRAND", [_link("traces_to", "REQ-LOWER")], kind_hint="requirement"),
        _item("REQ-CHILD", [_link("part_of", "REQ-PARENT")], kind_hint="requirement"),
        _item("REQ-UPPER", kind_hint="requirement"),
        _item("REQ-REL", release="2", kind_hint="requirement"),
        _item(
            "ST-L",
            [_link("about", "SIG"), _link("refers_to", "NOTE")],
            kind_hint="statement",
        ),
        _item(
            "SIG",
            [_link("part_of", "PDU"), _link("has_type", "SCALE")],
            attributes=_tag("I-SIGNAL"),
        ),
        _item("PDU", [_link("part_of", "FR")], attributes=_tag("I-SIGNAL-I-PDU")),
        _item("FR", attributes=_tag("CAN-FRAME")),
        _item("SCALE", attributes=_tag("COMPU-METHOD")),
        _item("NOTE"),
        _item("SIG-BUS", source_id="bus", attributes=_tag("I-SIGNAL")),
        _item("REQ-X", source_id="a", kind_hint="requirement"),
        _item("REQ-X", source_id="b", kind_hint="requirement"),
        _item("REQ-OTHER", source_id="other", kind_hint="requirement"),
    ]


def _project(tmp_path):
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    return kg, project_items(_items(), kg)


def _key(projection, type_name, native_id, source_id="fix"):
    found = [
        node["key"] for node in projection["nodes"]
        if node["type"] == type_name
        and node["native_id"] == native_id
        and node["source_id"] == source_id
    ]
    assert len(found) == 1, (type_name, native_id, source_id, found)
    return found[0]


def _pairs(records):
    return [(row["source_id"], row["item_id"], row["key"]) for row in records]


def _lower_nodes(projection):
    return sorted([
        _key(projection, "DataType", "SCALE"),
        _key(projection, "Element", "NOTE"),
        _key(projection, "Pdu", "PDU"),
        _key(projection, "Signal", "SIG"),
    ])


def test_ids_resolve_to_requirement_nodes_and_a_miss_is_an_error(tmp_path):
    """A list of ids, or {ids}, resolves to Requirement nodes.

    The same id twice is one record. Request order is kept. Two sources
    that share a native id are both returned, key order inside that id.
    A full node key selects that one node. An unknown id, and a signal
    id, raise and name the miss. A known id mixed with misses raises
    before a partial list is returned.
    """
    kg, projection = _project(tmp_path)
    upper = _key(projection, "Requirement", "REQ-UPPER")
    child = _key(projection, "Requirement", "REQ-CHILD")
    one = select_requirements(kg, projection, ["REQ-UPPER", "REQ-CHILD"])
    assert one == select_requirements(kg, projection, {"ids": ["REQ-UPPER", "REQ-CHILD"]})
    assert _pairs(one) == [("fix", "REQ-UPPER", upper), ("fix", "REQ-CHILD", child)]
    assert select_requirements(kg, projection, ["REQ-UPPER", "REQ-UPPER"]) == one[:1]

    both = select_requirements(kg, projection, ["REQ-X"])
    key_a = _key(projection, "Requirement", "REQ-X", "a")
    key_b = _key(projection, "Requirement", "REQ-X", "b")
    assert _pairs(both) == [("a", "REQ-X", key_a), ("b", "REQ-X", key_b)]
    assert _pairs(select_requirements(kg, projection, [key_a])) == [("a", "REQ-X", key_a)]

    with pytest.raises(ScopeError, match="NOPE"):
        select_requirements(kg, projection, ["NOPE"])
    with pytest.raises(ScopeError, match="SIG"):
        select_requirements(kg, projection, ["SIG"])
    with pytest.raises(ScopeError, match="NO-1") as caught:
        select_requirements(kg, projection, ["REQ-UPPER", "NO-1", "NO-2"])
    assert "NO-2" in str(caught.value)
    assert "REQ-UPPER" not in str(caught.value)


def test_source_selects_that_sources_requirements(tmp_path):
    """source is a source_id, or a scope (source_id@release) when it is not.

    A known source with no Requirement returns an empty list. An unknown
    source is an error, not that empty list.
    """
    kg, projection = _project(tmp_path)
    selected = select_requirements(kg, projection, {"source": "fix"})
    assert [row["item_id"] for row in selected] == [
        "REQ-CHILD", "REQ-GRAND", "REQ-LOWER", "REQ-PARENT", "REQ-UPPER", "REQ-REL",
    ]
    assert {row["source_id"] for row in selected} == {"fix"}
    assert _pairs(select_requirements(kg, projection, {"source": "fix@2"})) == [
        ("fix", "REQ-REL", _key(projection, "Requirement", "REQ-REL")),
    ]
    assert select_requirements(kg, projection, {"source": "bus"}) == []
    with pytest.raises(ScopeError, match="missing"):
        select_requirements(kg, projection, {"source": "missing"})


def test_parent_is_the_requirement_and_what_traces_to_it(tmp_path):
    """A parent is the upper requirement of traces_to (lower → upper).

    The walk repeats. The parent itself is included. The requirement it
    traces up to is not. A part_of child is not. The same native id in
    two sources is an error until the caller names a node key.
    """
    kg, projection = _project(tmp_path)
    selected = select_requirements(kg, projection, {"parent": "REQ-PARENT"})
    assert [row["item_id"] for row in selected] == ["REQ-GRAND", "REQ-LOWER", "REQ-PARENT"]
    assert "REQ-UPPER" not in [row["item_id"] for row in selected]
    assert "REQ-CHILD" not in [row["item_id"] for row in selected]

    key_a = _key(projection, "Requirement", "REQ-X", "a")
    assert _pairs(select_requirements(kg, projection, {"parent": key_a})) == [
        ("a", "REQ-X", key_a),
    ]
    with pytest.raises(ScopeError, match="matches nothing"):
        select_requirements(kg, projection, {"parent": "NOPE"})
    with pytest.raises(ScopeError, match="matches 2") as caught:
        select_requirements(kg, projection, {"parent": "REQ-X"})
    text = str(caught.value)
    assert key_a in text
    assert _key(projection, "Requirement", "REQ-X", "b") in text


def test_scope_must_be_one_of_the_three_forms(tmp_path):
    kg, projection = _project(tmp_path)
    with pytest.raises(ScopeError, match="list of ids"):
        select_requirements(kg, projection, "all")
    with pytest.raises(ScopeError, match="exactly one"):
        select_requirements(kg, projection, {"ids": ["REQ-UPPER"], "source": "fix"})
    with pytest.raises(ScopeError, match="empty"):
        select_requirements(kg, projection, [])
    with pytest.raises(ScopeError, match="empty"):
        select_requirements(kg, projection, {"ids": []})


def test_fetch_context_is_two_steps_from_statements_grouped_by_requirement(tmp_path):
    """Question 4. Statements and the third step are not returned.

    REQ-LOWER's statement reaches SIG and NOTE in one step, and PDU and
    SCALE in two. The frame beyond PDU is the third step. REQ-GRAND has
    no statement, so its group is empty. Group order follows the
    requirement list. A key that is not a Requirement is an error.
    """
    kg, projection = _project(tmp_path)
    requirements = select_requirements(
        kg, projection, ["REQ-GRAND", "REQ-LOWER"],
    )
    context = fetch_context(kg, projection, requirements)
    assert [group["item_id"] for group in context["requirements"]] == ["REQ-GRAND", "REQ-LOWER"]
    assert context["requirements"][0]["nodes"] == []
    assert context["requirements"][1]["nodes"] == _lower_nodes(projection)
    frame = _key(projection, "Frame", "FR")
    statement = _key(projection, "Statement", "ST-L")
    requirement = _key(projection, "Requirement", "REQ-LOWER")
    assert frame not in context["requirements"][1]["nodes"]
    assert statement not in context["requirements"][1]["nodes"]
    assert requirement not in context["requirements"][1]["nodes"]
    again = fetch_context(kg, projection, requirements)
    assert again == context

    with pytest.raises(ScopeError, match="matches nothing"):
        fetch_context(kg, projection, [{"key": frame, "source_id": "fix", "item_id": "FR"}])


def test_testpoints_document_is_what_the_scorer_reads(tmp_path, capsys):
    """needs are the context nodes' native ids. testable is absent.

    Gold asks for SIG and bracketed NOTE. The walk also fetches PDU and
    SCALE. Found 2 of 2, fetched 4. REQ-GRAND is none and fetches nothing.
    """
    kg, projection = _project(tmp_path)
    requirements = select_requirements(kg, projection, ["REQ-LOWER", "REQ-GRAND"])
    context = fetch_context(kg, projection, requirements)
    document = as_testpoints(projection, context)
    assert document == {"testpoints": [
        {
            "source_id": "fix",
            "item_id": "REQ-LOWER",
            "needs": ["SCALE", "NOTE", "PDU", "SIG"],
        },
        {"source_id": "fix", "item_id": "REQ-GRAND", "needs": []},
    ]}
    assert "testable" not in document["testpoints"][0]
    assert format_summary(document) == (
        "testpoints n_requirements=2 n_fetched=4 n_docs=1"
    )

    path = tmp_path / "system.json"
    written = write_testpoints(path, projection, context)
    assert written == document
    assert path.read_text(encoding="utf-8") == json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    assert format_summary(document) in capsys.readouterr().out

    empty = fetch_context(kg, projection, select_requirements(kg, projection, {"source": "bus"}))
    empty_doc = as_testpoints(projection, empty)
    assert empty_doc == {"testpoints": []}
    assert format_summary(empty_doc) == "testpoints=n/a n_requirements=0 n_fetched=0 n_docs=0"
    grand_only = fetch_context(
        kg, projection, select_requirements(kg, projection, ["REQ-GRAND"]),
    )
    assert format_summary(as_testpoints(projection, grand_only)) == (
        "testpoints n_requirements=1 n_fetched=0 n_docs=1"
    )

    folder = tmp_path / "packet"
    _write_gold(folder / "open" / "testpoints.csv", [
        {
            "source_id": "fix",
            "item_id": "REQ-LOWER",
            "needs_from_elsewhere": "SIG; [NOTE]",
            "testable_as_written": "yes",
        },
        {
            "source_id": "fix",
            "item_id": "REQ-GRAND",
            "needs_from_elsewhere": "none",
            "testable_as_written": "yes",
        },
    ])
    result = score_gold.score(folder, document)
    assert result["testpoints_needs_recall"] == 1.0
    assert result["testpoints_needs_precision"] == 0.5
    assert result["testpoints_n_requirements"] == 2
    assert result["testpoints_n_needed"] == 2
    assert result["testpoints_n_fetched"] == 4
    assert result["testpoints_n_docs"] == 1
    assert result["testpoints_testable_agreement"] == "n/a"
    assert result["testpoints_testable_n_gold"] == 2
    assert result["testpoints_testable_n_pred"] == 0
    report = score_gold.format_report(result)
    assert (
        "n_requirements=2 n_needed=2 n_fetched=4 n_docs=1"
    ) in report
    assert "testpoints_needs_recall=1.0000000000" in report
    assert "testpoints_needs_precision=0.5000000000" in report
    assert "testpoints_testable_agreement=n/a" in report


def test_interpreter_passes_selected_requirements_into_fetch_context(tmp_path):
    kg, projection = _project(tmp_path)
    procedure = load_procedure(textwrap.dedent(
        """
        name: generate-test-spec
        version: "0.1"
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope:
                ids:
                  - REQ-LOWER
          - id: context
            operation: fetch_context
            inputs:
              requirements: select.outputs.requirements
        """
    ).strip() + "\n")
    run = interpret(procedure, GraphOperations(kg, projection))
    assert run.status == "ok"
    requirements = run.outputs["select"]["requirements"]
    assert run.outputs["context"]["context"] == fetch_context(kg, projection, requirements)
    assert requirements[0]["item_id"] == "REQ-LOWER"
    assert run.outputs["context"]["context"]["requirements"][0]["nodes"] == _lower_nodes(projection)


def test_graph_operations_do_not_run_the_model(tmp_path):
    kg, projection = _project(tmp_path)
    with pytest.raises(ScopeError, match="invoke_model"):
        GraphOperations(kg, projection)("invoke_model", {"instructions": "write a test"})


def _write_gold(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "source_id", "item_id", "needs_from_elsewhere", "testable_as_written",
        ])
        writer.writeheader()
        writer.writerows(rows)
