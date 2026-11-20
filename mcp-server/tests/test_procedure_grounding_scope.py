"""RUN-06: grounding and id resolution are per requirement.

The requirements and the signal are synthetic. Nothing here is from a
customer. A names signal S. B names no signal.
"""

from grap_mcp.procedure_model import _resolve_case_ids
from grap_mcp.procedure_validate import format_summary, validate_output

S_NATIVE = "S.native"
S_KEY = f"Signal | synth | {S_NATIVE}"
_A = {"key": "Requirement | synth | A", "source_id": "synth", "item_id": "A"}
_B = {"key": "Requirement | synth | B", "source_id": "synth", "item_id": "B"}
_CONTEXT = {
    "requirements": [
        {"key": _A["key"], "source_id": "synth", "item_id": "A", "nodes": [S_KEY]},
        {"key": _B["key"], "source_id": "synth", "item_id": "B", "nodes": []},
    ],
}
_PROJECTION = {
    "nodes": [
        {
            "key": S_KEY,
            "type": "Signal",
            "native_id": S_NATIVE,
            "kind": "i_signal",
            "source_id": "synth",
        },
        {"key": _A["key"], "type": "Requirement", "native_id": "A", "source_id": "synth"},
        {"key": _B["key"], "type": "Requirement", "native_id": "B", "source_id": "synth"},
    ],
    "edges": [],
}


def _case(verifies, uses, **overrides):
    case = {
        "id": "TC-1",
        "verifies": list(verifies),
        "preconditions": "ready",
        "stimulus": "act",
        "expected_observable_result": "seen",
        "pass_criterion": "holds",
        "kind": "positive",
        "uses": list(uses),
    }
    case.update(overrides)
    checks = case.get("checks")
    if (
        (isinstance(checks, list) and len(checks) > 0)
        or "no_check_reason" in case
    ):
        return case
    # S.native is a DBC Signal. A use that is not that node is not a Signal.
    if S_KEY in case["uses"]:
        condition = {
            "signal": {"message": "S", "signal": "native"},
            "op": "==",
            "value": 1,
        }
        case["checks"] = [{
            "id": "c1",
            "when": condition,
            "expect": {
                "signal": {"message": "S", "signal": "native"},
                "op": "==",
                "value": 1,
            },
            "mode": "eventually",
            "within_ms": [0, 100],
        }]
    else:
        case["no_check_reason"] = "not_on_bus"
    return case


def _grounding(verifies, uses):
    draft = {"cases": [_case(verifies, uses)]}
    result = validate_output(draft, [_A, _B], _CONTEXT, projection=_PROJECTION)
    return [issue for issue in result["issues"] if issue["check"] == "grounding"]


def _grounding_issue(value):
    return {"case": "TC-1", "check": "grounding", "value": value}


def test_a_case_verifying_only_b_cannot_borrow_as_signal():
    """A case that verifies only B may not use S. The same case verifying A may."""
    borrowed = _grounding_issue(S_KEY)
    assert {
        "item_id_b": _grounding(["B"], [S_KEY]),
        "key_b": _grounding([_B["key"]], [S_KEY]),
        "item_id_a": _grounding(["A"], [S_KEY]),
        "key_a": _grounding([_A["key"]], [S_KEY]),
    } == {
        "item_id_b": [borrowed],
        "key_b": [borrowed],
        "item_id_a": [],
        "key_a": [],
    }


def test_b_case_verifying_both_requirements_may_use_s():
    """The allowed nodes are the union, so a case that verifies A and B may use S."""
    assert _grounding(["A", "B"], [S_KEY]) == []
    assert _grounding([_A["key"], _B["key"]], [S_KEY]) == []
    draft = {"cases": [_case(["A", "B"], [S_KEY])]}
    result = validate_output(draft, [_A, _B], _CONTEXT, projection=_PROJECTION)
    assert result == {"issues": [], "passed": True}


def test_c_native_id_on_bs_case_is_not_resolved_and_fails_grounding():
    """S.native in a case that verifies only B stays written. resolved_ids uses=0."""
    draft = {"cases": [_case(["B"], [S_NATIVE])]}
    uses_n, verifies_n = _resolve_case_ids(draft, _CONTEXT, _PROJECTION)
    result = validate_output(draft, [_A, _B], _CONTEXT, projection=_PROJECTION)
    grounding = [issue for issue in result["issues"] if issue["check"] == "grounding"]
    assert (uses_n, verifies_n, draft["cases"][0]["uses"], grounding) == (
        0,
        0,
        [S_NATIVE],
        [_grounding_issue(S_NATIVE)],
    )


def _window_case(within_ms, pass_criterion):
    signal = "Msg.Sig"
    key = f"Signal | synth | {signal}"
    req = {"key": "Requirement | synth | R", "source_id": "synth", "item_id": "R"}
    condition = {
        "signal": {"message": "Msg", "signal": "Sig"},
        "op": "==",
        "value": 1,
    }
    case = _case(
        ["R"],
        [key],
        pass_criterion=pass_criterion,
        checks=[{
            "id": "c1",
            "given": [],
            "when": condition,
            "expect": condition,
            "mode": "eventually",
            "within_ms": within_ms,
        }],
    )
    draft = {"cases": [case]}
    context = {"requirements": [{
        "key": req["key"],
        "source_id": "synth",
        "item_id": "R",
        "nodes": [key],
    }]}
    projection = {"nodes": [{
        "key": key,
        "type": "Signal",
        "native_id": signal,
        "kind": "i_signal",
        "source_id": "synth",
    }]}
    result = validate_output(draft, [req], context, projection=projection)
    summary = format_summary(draft, [req], context, result, projection=projection)
    assumed = [issue for issue in result["issues"] if issue["check"] == "assumed_window"]
    return assumed, summary


def test_d_default_one_second_window_needs_the_assumed_note():
    """[0, 1000] without the note is assumed_window. The note, or another window, is not."""
    bare, bare_summary = _window_case([0, 1000], "holds")
    noted, noted_summary = _window_case(
        [0, 1000], "holds in the assumed 1 s window",
    )
    short, short_summary = _window_case([0, 100], "holds")
    assert (bare, noted, short) == (
        [{"case": "TC-1", "check": "assumed_window", "value": "c1"}],
        [],
        [],
    )
    assert "checks=1 (n=1 cases, documents=1)" in bare_summary
    assert "checks=0 (n=1 cases, documents=1)" in noted_summary
    assert "checks=0 (n=1 cases, documents=1)" in short_summary
