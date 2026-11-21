"""RUN-11: refuse an impossible inside_ecu.

Synthetic stubs only. The keys are made up. Nothing here is from a corpus.
"""

from grap_mcp.procedure_validate import format_summary, validate_output

_REQ_KEY = "Requirement | synth | R1"
_SIG_A = "Signal | synth | Msg.Alpha"
_SIG_B = "Signal | synth | Msg.Beta"
_MSG = "Message | synth | Msg"
_CASE_ID = "CASE-IE"


def _req():
    return {"key": _REQ_KEY, "source_id": "synth", "item_id": "R1"}


def _node(key, type_name, native_id):
    return {
        "key": key,
        "type": type_name,
        "native_id": native_id,
        "kind": "i_signal" if type_name == "Signal" else "",
        "source_id": "synth",
    }


def _context(node_keys):
    return {
        "requirements": [{
            "key": _REQ_KEY,
            "source_id": "synth",
            "item_id": "R1",
            "nodes": list(node_keys),
        }],
    }


def _projection(nodes):
    return {"nodes": list(nodes), "edges": []}


def _case(**overrides):
    case = {
        "id": _CASE_ID,
        "verifies": ["R1"],
        "preconditions": "ready",
        "stimulus": "act",
        "expected_observable_result": "seen",
        "pass_criterion": "holds",
        "kind": "positive",
        "uses": [_SIG_A, _SIG_B],
        "checks": [],
        "no_check_reason": "inside_ecu",
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


def _signal_nodes():
    return [
        _node(_SIG_A, "Signal", "Msg.Alpha"),
        _node(_SIG_B, "Signal", "Msg.Beta"),
    ]


def test_a_inside_ecu_on_two_signals_is_one_no_check_reason_issue():
    """inside_ecu, two uses, both projection Signals: one no_check_reason issue."""
    result, summary = _run(
        _case(),
        nodes=_signal_nodes(),
        context_nodes=[_SIG_A, _SIG_B],
        projection=_projection(_signal_nodes()),
    )
    assert result == {
        "passed": False,
        "issues": [{
            "case": _CASE_ID,
            "check": "no_check_reason",
            "value": _CASE_ID,
        }],
    }
    assert "no_check_reason inside_ecu=1 signal_compare=0 not_on_bus=0" in summary
    assert "checkable=0 (n=1 requirements with signals, documents=1)" in summary


def test_b_inside_ecu_with_a_non_signal_use_adds_no_issue():
    """One of two uses is not a Signal node: the new rule does not fire."""
    nodes = [
        _node(_SIG_A, "Signal", "Msg.Alpha"),
        _node(_MSG, "Message", "Msg"),
    ]
    result, _summary = _run(
        _case(uses=[_SIG_A, _MSG]),
        nodes=nodes,
        context_nodes=[_SIG_A, _MSG],
        projection=_projection(nodes),
    )
    assert result == {"issues": [], "passed": True}


def test_c_inside_ecu_with_one_signal_use_adds_no_issue():
    """One Signal use is not enough for the new rule."""
    nodes = [_node(_SIG_A, "Signal", "Msg.Alpha")]
    result, _summary = _run(
        _case(uses=[_SIG_A]),
        nodes=nodes,
        context_nodes=[_SIG_A],
        projection=_projection(nodes),
    )
    assert result == {"issues": [], "passed": True}


def test_d_not_on_bus_with_two_signal_uses_adds_no_issue():
    """not_on_bus is not touched, even when both uses are Signals."""
    result, summary = _run(
        _case(no_check_reason="not_on_bus"),
        nodes=_signal_nodes(),
        context_nodes=[_SIG_A, _SIG_B],
        projection=_projection(_signal_nodes()),
    )
    assert result == {"issues": [], "passed": True}
    assert "no_check_reason inside_ecu=0 signal_compare=0 not_on_bus=1" in summary


def test_e_projection_none_adds_no_issue():
    """projection None: inside_ecu with two uses adds nothing."""
    result, summary = _run(
        _case(),
        nodes=[],
        context_nodes=[_SIG_A, _SIG_B],
        projection=None,
    )
    assert result == {"issues": [], "passed": True}
    assert "checkable=n/a (n=0 requirements with signals, documents=0)" in summary
