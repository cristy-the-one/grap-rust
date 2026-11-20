"""RUN-09: a requirement that names a Signal must have a check or a reason.

Synthetic stubs only. Nothing here is from a customer corpus.
"""

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from export_judge_request import export_request  # noqa: E402
from grap_mcp.kee.llm import StubProvider  # noqa: E402
from grap_mcp.procedure_model import DRAFT_SCHEMA, ModelError, invoke_model  # noqa: E402
from grap_mcp.procedure_template import emit  # noqa: E402
from grap_mcp.procedure_validate import format_summary, validate_output  # noqa: E402

_REQ_KEY = "Requirement | synth | R"
_SIG_KEY = "Signal | synth | Msg.Sig"
_OTHER_KEY = "Message | synth | Msg"
_TEMPLATE = {
    "id": "default",
    "name": "Default test specification",
    "level": "software",
    "body": "# {{title}}\n{{#cases}}\n## {{id}}\n{{/cases}}\n",
}


def _req():
    return {"key": _REQ_KEY, "source_id": "synth", "item_id": "R"}


def _signal_node():
    return {
        "key": _SIG_KEY,
        "type": "Signal",
        "native_id": "Msg.Sig",
        "kind": "i_signal",
        "source_id": "synth",
    }


def _other_node():
    return {
        "key": _OTHER_KEY,
        "type": "Message",
        "native_id": "Msg",
        "kind": "",
        "source_id": "synth",
    }


def _context(node_keys):
    return {
        "requirements": [{
            "key": _REQ_KEY,
            "source_id": "synth",
            "item_id": "R",
            "nodes": list(node_keys),
        }],
    }


def _projection(nodes):
    return {"nodes": list(nodes), "edges": []}


def _condition():
    return {
        "signal": {"message": "Msg", "signal": "Sig"},
        "op": "==",
        "value": 1,
    }


def _valid_check():
    return {
        "id": "c1",
        "when": _condition(),
        "expect": _condition(),
        "mode": "eventually",
        "within_ms": [0, 100],
    }


def _case(**overrides):
    case = {
        "id": "TC-1",
        "verifies": ["R"],
        "preconditions": "ready",
        "stimulus": "act",
        "expected_observable_result": "seen",
        "pass_criterion": "holds",
        "kind": "positive",
        "uses": [_SIG_KEY],
        "checks": [],
    }
    case.update(overrides)
    return case


def _run(case, *, nodes, context_nodes, projection):
    draft = {"cases": [case]}
    requirements = [_req()]
    context = _context(context_nodes)
    result = validate_output(
        draft, requirements, context, projection=projection,
    )
    summary = format_summary(
        draft, requirements, context, result, projection=projection,
    )
    return result, summary


def _signal_run(case):
    return _run(
        case,
        nodes=[_signal_node()],
        context_nodes=[_SIG_KEY],
        projection=_projection([_signal_node()]),
    )


def test_a_signal_requirement_with_no_check_and_no_reason_fails():
    """A Signal in context, the only case checks=[], no reason: one checkable issue."""
    result, _summary = _signal_run(_case())
    assert result == {
        "passed": False,
        "issues": [{"case": None, "check": "checkable", "value": _REQ_KEY}],
    }


def test_b_one_valid_check_satisfies_checkable():
    """The same requirement with one valid check has no checkable issue."""
    result, _summary = _signal_run(_case(checks=[_valid_check()]))
    assert not any(issue["check"] == "checkable" for issue in result["issues"])
    assert result == {"issues": [], "passed": True}


def test_c_signal_compare_reason_satisfies_checkable_and_is_counted():
    """checks=[] and no_check_reason signal_compare: no issue, and the count is 1."""
    result, summary = _signal_run(_case(no_check_reason="signal_compare"))
    assert result == {"issues": [], "passed": True}
    assert "no_check_reason inside_ecu=0 signal_compare=1 not_on_bus=0" in summary
    assert "checkable=0 (n=1 requirements with signals, documents=1)" in summary


def test_d_a_reason_outside_the_three_is_an_issue():
    """no_check_reason "because" is one no_check_reason issue. The value is the case id."""
    result, _summary = _run(
        _case(uses=[], no_check_reason="because"),
        nodes=[_other_node()],
        context_nodes=[_OTHER_KEY],
        projection=_projection([_other_node()]),
    )
    assert result == {
        "passed": False,
        "issues": [{"case": "TC-1", "check": "no_check_reason", "value": "TC-1"}],
    }


def test_e_a_reason_with_a_non_empty_checks_list_is_an_issue():
    """A reason set on a case whose checks is non-empty is one no_check_reason issue."""
    result, _summary = _signal_run(
        _case(checks=[_valid_check()], no_check_reason="inside_ecu"),
    )
    assert result == {
        "passed": False,
        "issues": [{"case": "TC-1", "check": "no_check_reason", "value": "TC-1"}],
    }


def test_f_a_context_without_a_signal_node_is_not_checkable():
    """A requirement whose context has no Signal node gets no checkable issue."""
    result, _summary = _run(
        _case(uses=[_OTHER_KEY]),
        nodes=[_other_node()],
        context_nodes=[_OTHER_KEY],
        projection=_projection([_other_node()]),
    )
    assert not any(issue["check"] == "checkable" for issue in result["issues"])
    assert result == {"issues": [], "passed": True}


def test_g_a_missing_projection_adds_no_checkable_issue():
    """projection None adds nothing, and the checkable line is n/a."""
    case = _case(uses=[])
    draft = {"cases": [case]}
    requirements = [_req()]
    context = _context([_SIG_KEY])
    result = validate_output(draft, requirements, context, projection=None)
    summary = format_summary(
        draft, requirements, context, result, projection=None,
    )
    assert result == {"issues": [], "passed": True}
    assert "checkable=n/a (n=0 requirements with signals, documents=0)" in summary


def test_i_parse_refuses_a_non_string_no_check_reason():
    """no_check_reason = 5 is refused. A string is stored. null is absent."""
    case = _case(uses=[], checks=[])
    refused = StubProvider(responses=[json.dumps({
        "title": "T",
        "cases": [{**case, "variant": "", "no_check_reason": 5}],
    })])
    with pytest.raises(ModelError, match="no_check_reason must be a string or null"):
        invoke_model("write", refused, _TEMPLATE)
    assert len(refused.calls) == 1

    kept = StubProvider(responses=[json.dumps({
        "title": "T",
        "cases": [{**case, "variant": "", "no_check_reason": "not_on_bus"}],
    })])
    draft = invoke_model("write", kept, _TEMPLATE)
    assert draft["cases"][0]["no_check_reason"] == "not_on_bus"

    absent = StubProvider(responses=[json.dumps({
        "title": "T",
        "cases": [{**case, "variant": "", "no_check_reason": None}],
    })])
    parsed = invoke_model("write", absent, _TEMPLATE)
    assert "no_check_reason" not in parsed["cases"][0]
    assert "no_check_reason" in DRAFT_SCHEMA["$defs"]["case"]["properties"]
    assert "no_check_reason" not in DRAFT_SCHEMA["$defs"]["case"]["required"]


def test_unverified_signal_requirement_is_coverage_only():
    """A Signal requirement that no case verifies is coverage, not checkable."""
    draft = {"cases": []}
    requirements = [_req()]
    context = _context([_SIG_KEY])
    projection = _projection([_signal_node()])
    result = validate_output(
        draft, requirements, context, projection=projection,
    )
    assert result == {
        "passed": False,
        "issues": [{"case": None, "check": "coverage", "value": _REQ_KEY}],
    }
    assert not any(issue["check"] == "checkable" for issue in result["issues"])


def test_emit_keeps_no_check_reason_and_the_judge_export_ignores_it(tmp_path):
    """The stored specification keeps the reason. The judge request does not copy it."""
    artifact = emit(
        {"title": "Lamp", "cases": [_case(no_check_reason="not_on_bus", uses=[])]},
        _TEMPLATE,
        True,
        store=tmp_path,
    )
    stored = artifact["specification"]["cases"][0]
    assert stored["no_check_reason"] == "not_on_bus"
    request = export_request(artifact["specification"], "spec-1")
    assert "no_check_reason" not in request["cases"][0]
    assert request["cases"][0]["checks"] == []
