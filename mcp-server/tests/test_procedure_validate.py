"""P-13: validate_output, the six checks of §7.

No model. One fixture fails only one check. A requirement is covered when
a case verifies its key, its item id, or a statement id of one of its
statements. Identifiers follow the R-09 rule and must match a context
node exactly.
"""

import textwrap

import pytest

from grap_mcp.kee.statements.tables import Statement
from grap_mcp.procedure_interpreter import interpret
from grap_mcp.procedure_runtime import Procedure, Step
from grap_mcp.procedure_validate import (
    ValidationError,
    ValidationOperations,
    format_summary,
    report_validation,
    validate_output,
)

SIG_KEY = "Signal | fix | VehSpdFL"


def _req(item_id="REQ-1", source_id="fix", key=None):
    if key is None:
        key = f"Requirement | {source_id} | {item_id}"
    return {"key": key, "source_id": source_id, "item_id": item_id}


def _context(requirements, nodes):
    return {
        "requirements": [
            {
                "key": req["key"],
                "source_id": req["source_id"],
                "item_id": req["item_id"],
                "nodes": list(nodes),
            }
            for req in requirements
        ],
    }


def _node(key, type_name, native_id, source_id="fix", kind=""):
    return {
        "key": key,
        "type": type_name,
        "native_id": native_id,
        "kind": kind,
        "source_id": source_id,
    }


def _projection(requirements, extra_nodes=(), edges=()):
    nodes = [_node(req["key"], "Requirement", req["item_id"], req["source_id"], "requirement")
             for req in requirements]
    nodes.append(_node(SIG_KEY, "Signal", "VehSpdFL", kind="i_signal"))
    nodes.extend(extra_nodes)
    return {"nodes": nodes, "edges": list(edges)}


def _case(**overrides):
    case = {
        "id": "TC-1",
        "verifies": ["REQ-1"],
        "preconditions": "ignition on",
        "stimulus": "set VehSpdFL to 1",
        "expected_observable_result": "VehSpdFL reads 1",
        "pass_criterion": "the value equals 1",
        "kind": "positive",
        "uses": [SIG_KEY],
    }
    case.update(overrides)
    return case


def _draft(*cases, **overrides):
    if not cases:
        cases = (_case(**overrides),)
    return {"cases": list(cases)}


def _run(draft, requirements, *, statements=None, projection=None, context_nodes=None):
    if projection is None:
        projection = _projection(requirements)
    if context_nodes is None:
        context_nodes = [SIG_KEY]
    context = _context(requirements, context_nodes)
    result = validate_output(
        draft, requirements, context, projection=projection, statements=statements,
    )
    return result, format_summary(
        draft, requirements, context, result,
        projection=projection, statements=statements,
    )


def _issue(check, value, case="TC-1"):
    return {"case": case, "check": check, "value": value}


def test_a_complete_draft_passes():
    """Every selected requirement is verified, every use is a context key,
    and every identifier in the text is that key's native id."""
    req = _req()
    draft = _draft()
    result, summary = _run(draft, [req])
    assert result == {"issues": [], "passed": True}
    bare = validate_output(
        draft["cases"], [req], _context([req], [SIG_KEY]), projection=_projection([req]),
    )
    assert bare == result
    assert summary == textwrap.dedent(
        """\
        passed=true n_issues=0 n_cases=1 n_requirements=1 n_docs=1 n_statements=0
        coverage=0 (n=1 requirements, documents=1)
        grounding=0 (n=1 cases, documents=1)
        no_invented_names=0 (n=1 cases, documents=1)
        traceability=0 (n=1 cases, documents=1)
        completeness=0 (n=1 cases, documents=1)
        negative_cases=0 (n=1 requirements, n_statements=0, documents=1)"""
    )


def test_coverage_fails_only_for_requirements_no_case_verifies():
    """The middle requirement is verified. The other two are the only issues,
    in selection order. A statement id covers its requirement."""
    first, middle, last = _req("REQ-1"), _req("REQ-2"), _req("REQ-3")
    result, summary = _run(_draft(verifies=["REQ-2"]), [first, middle, last])
    assert result == {
        "passed": False,
        "issues": [
            _issue("coverage", first["key"], case=None),
            _issue("coverage", last["key"], case=None),
        ],
    }
    assert summary.startswith(
        "passed=false n_issues=2 n_cases=1 n_requirements=3 n_docs=1 n_statements=0"
    )
    assert "coverage=2 (n=3 requirements, documents=1)" in summary
    assert "grounding=0 (n=1 cases, documents=1)" in summary

    covered = _req()
    statement_id = "fix|REQ-1|0"
    statements = [{
        "requirement": covered["key"],
        "id": statement_id,
        "kind": "requirement",
        "exception": None,
    }]
    by_statement, _summary = _run(
        _draft(verifies=[statement_id]), [covered], statements=statements,
    )
    assert by_statement == {"issues": [], "passed": True}


def test_the_same_item_id_covers_every_selected_node_and_a_key_covers_one():
    left = _req("REQ-X", source_id="a")
    right = _req("REQ-X", source_id="b")
    both, summary = _run(_draft(verifies=["REQ-X"]), [left, right])
    assert both == {"issues": [], "passed": True}
    assert "n_docs=2" in summary

    one, _summary = _run(_draft(verifies=[left["key"]]), [left, right])
    assert one == {
        "passed": False,
        "issues": [_issue("coverage", right["key"], case=None)],
    }


def test_grounding_fails_only_for_a_use_that_is_not_a_context_key():
    """A native id is not a node key. An unknown key is the offending value."""
    req = _req()
    native, _summary = _run(_draft(uses=["VehSpdFL"]), [req])
    assert native == {
        "passed": False,
        "issues": [_issue("grounding", "VehSpdFL")],
    }

    other = "Signal | fix | OTHER"
    unknown, _summary = _run(_draft(uses=[SIG_KEY, other, other]), [req])
    assert unknown == {
        "passed": False,
        "issues": [_issue("grounding", other)],
    }


def test_invented_name_fails_only_when_the_identifier_is_not_in_the_context():
    """R-09 inside a sentence: a CamelCase token must match a native id.

    A bracketed id is matched with its brackets. Digits and dots in a
    sentence are not identifiers. A bare number, as the whole field, is.
    """
    req = _req()
    missed, _summary = _run(
        _draft(stimulus="set VehSpdFR to 1"), [req],
    )
    assert missed == {
        "passed": False,
        "issues": [_issue("no_invented_names", "VehSpdFR")],
    }

    prose, _summary = _run(
        _draft(stimulus="art. 5 applies while VehSpdFL holds"), [req],
    )
    assert prose == {"issues": [], "passed": True}

    bracket, _summary = _run(
        _draft(stimulus="see [SWS_Com_00123]"), [req],
    )
    assert bracket == {
        "passed": False,
        "issues": [_issue("no_invented_names", "[SWS_Com_00123]")],
    }

    bracket_key = "Signal | fix | [SWS_Com_00123]"
    projection = _projection([req], extra_nodes=[
        _node(bracket_key, "Signal", "[SWS_Com_00123]", kind="i_signal"),
    ])
    exact, _summary = _run(
        _draft(
            stimulus="see [SWS_Com_00123]",
            expected_observable_result="the signal reads 1",
            uses=[bracket_key],
        ),
        [req],
        projection=projection,
        context_nodes=[bracket_key],
    )
    assert exact == {"issues": [], "passed": True}

    phrase, _summary = _run(
        _draft(stimulus="the Com_SendSignal function"), [req],
    )
    assert phrase["issues"] == [_issue("no_invented_names", "Com_SendSignal")]

    bare, _summary = _run(_draft(pass_criterion="100"), [req])
    assert bare["issues"] == [_issue("no_invented_names", "100")]


def test_traceability_fails_only_when_verifies_names_something_outside():
    """The selected requirement is still verified, so coverage stays quiet.

    An empty verifies is also traceability, and then coverage fails too:
    that case is not the single-check fixture.
    """
    req = _req()
    outside, _summary = _run(_draft(verifies=["REQ-1", "REQ-OTHER"]), [req])
    assert outside == {
        "passed": False,
        "issues": [_issue("traceability", "REQ-OTHER")],
    }

    empty, _summary = _run(_draft(verifies=[]), [req])
    assert empty["issues"] == [
        _issue("coverage", req["key"], case=None),
        _issue("traceability", []),
    ]


@pytest.mark.parametrize("field", [
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
])
def test_completeness_fails_only_for_a_missing_or_blank_field(field):
    req = _req()
    missing = _case()
    del missing[field]
    result, _summary = _run(_draft(missing), [req])
    assert result == {
        "passed": False,
        "issues": [_issue("completeness", field)],
    }

    blank = _case(**{field: "  \n"})
    blank_result, _summary = _run(_draft(blank), [req])
    assert blank_result == result


def test_two_missing_fields_are_named_in_section_7_order():
    req = _req()
    case = _case()
    del case["pass_criterion"]
    del case["stimulus"]
    result, _summary = _run(_draft(case), [req])
    assert result["issues"] == [
        _issue("completeness", "stimulus"),
        _issue("completeness", "pass_criterion"),
    ]


def test_negative_cases_fail_only_when_error_handling_has_no_negative_case():
    """A negative case for a different requirement does not cover this one."""
    needs = _req("REQ-1")
    other = _req("REQ-2")
    statements = [{
        "requirement": needs["key"],
        "id": "fix|REQ-1|0",
        "kind": "error_handling",
        "exception": None,
    }]
    positive = _case(verifies=["REQ-1"])
    elsewhere = _case(id="TC-2", verifies=["REQ-2"], kind="negative")
    result, summary = _run(
        _draft(positive, elsewhere), [needs, other], statements=statements,
    )
    assert result == {
        "passed": False,
        "issues": [_issue("negative_cases", needs["key"], case=None)],
    }
    assert "negative_cases=1 (n=2 requirements, n_statements=1, documents=1)" in summary
    assert "coverage=0 (n=2 requirements, documents=1)" in summary

    negative = _case(id="TC-2", verifies=["REQ-1"], kind="negative")
    passed, _summary = _run(
        _draft(positive, negative), [needs], statements=statements,
    )
    assert passed == {"issues": [], "passed": True}


def test_an_exception_requires_a_negative_case_and_the_projection_kind_does_too():
    req = _req()
    statement = Statement(
        source_id="fix",
        item_id="REQ-1",
        unit=(0, 10),
        kind="requirement",
        subject=(0, 4),
        condition=None,
        required_behaviour=(4, 8),
        values_and_timing=(),
        exception=(8, 10),
        origin="rule",
        extractor="test",
        statement_id="fix|REQ-1|0",
    )
    from_table, _summary = _run(_draft(), [req], statements=[statement])
    assert from_table == {
        "passed": False,
        "issues": [_issue("negative_cases", req["key"], case=None)],
    }

    statement_key = "Statement | fix | fix|REQ-1|0"
    projection = _projection(
        [req],
        extra_nodes=[_node(
            statement_key, "Statement", "fix|REQ-1|0", kind="error_handling",
        )],
        edges=[{
            "relation": "has_statement",
            "from_key": req["key"],
            "to_key": statement_key,
        }],
    )
    from_graph, _summary = _run(_draft(), [req], projection=projection)
    assert from_graph == from_table

    explicit_empty, _summary = _run(
        _draft(), [req], projection=projection, statements=[],
    )
    assert explicit_empty == {"issues": [], "passed": True}

    no_exception = Statement(
        source_id="fix",
        item_id="REQ-1",
        unit=(0, 8),
        kind="requirement",
        subject=(0, 4),
        condition=None,
        required_behaviour=(4, 8),
        values_and_timing=(),
        exception=None,
        origin="rule",
        extractor="test",
    )
    quiet, _summary = _run(_draft(), [req], statements=[no_exception])
    assert quiet == {"issues": [], "passed": True}


def test_nothing_to_measure_prints_na_and_a_bad_draft_is_refused():
    draft = {"cases": []}
    result = validate_output(draft, [], {"requirements": []})
    assert result == {"issues": [], "passed": True}
    summary = format_summary(draft, [], {"requirements": []}, result)
    assert summary == textwrap.dedent(
        """\
        passed=true n_issues=0 n_cases=0 n_requirements=0 n_docs=0 n_statements=0
        coverage=n/a (n=0 requirements, documents=0)
        grounding=n/a (n=0 cases, documents=0)
        no_invented_names=n/a (n=0 cases, documents=0)
        traceability=n/a (n=0 cases, documents=0)
        completeness=n/a (n=0 cases, documents=0)
        negative_cases=n/a (n=0 requirements, n_statements=0, documents=0)"""
    )
    with pytest.raises(ValidationError, match="draft"):
        validate_output("not a specification", [], {"requirements": []})


def test_report_validation_prints_the_denominators(capsys):
    req = _req()
    draft = _draft(stimulus="set VehSpdFR to 1")
    context = _context([req], [SIG_KEY])
    projection = _projection([req])
    result = report_validation(draft, [req], context, projection=projection)
    assert result["passed"] is False
    printed = capsys.readouterr().out
    assert "n_cases=1" in printed
    assert "n_requirements=1" in printed
    assert "n_docs=1" in printed
    assert "documents=1" in printed
    assert "no_invented_names=1" in printed


def test_the_interpreter_keeps_a_failed_validation_and_emits_nothing():
    req = _req()
    draft = _draft(verifies=["REQ-1", "REQ-OTHER"])
    requirements = [req]
    context = _context(requirements, [SIG_KEY])
    procedure = Procedure(
        name="check",
        description="",
        version="0.1",
        steps=(
            Step(
                id="check",
                operation="validate_output",
                inputs={
                    "draft": draft,
                    "requirements": requirements,
                    "context": context,
                },
                guards=(),
            ),
        ),
    )
    run = interpret(procedure, ValidationOperations(projection=_projection(requirements)))
    assert run.status == "failed-validation"
    assert run.artifact is None
    assert run.outputs["check"]["passed"] is False
    assert run.outputs["check"]["issues"] == [_issue("traceability", "REQ-OTHER")]
    assert set(run.outputs["check"]) == {"issues", "passed"}


def test_validation_does_not_run_another_operation():
    with pytest.raises(ValidationError, match="invoke_model"):
        ValidationOperations()("invoke_model", {"instructions": "write a test"})
