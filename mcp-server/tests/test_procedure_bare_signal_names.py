"""RUN-07: a bare signal name is accepted when it is unambiguous.

The requirements and the signals are synthetic. HIGH_BEAM is the part
after the last '.' of LIGHT_STALK.HIGH_BEAM. Nothing here is from a
customer.
"""

from grap_mcp.procedure_validate import validate_output


def _requirement(item_id):
    return {
        "key": f"Requirement | synth | {item_id}",
        "source_id": "synth",
        "item_id": item_id,
    }


def _signal(native_id):
    key = f"Signal | synth | {native_id}"
    node = {
        "key": key,
        "type": "Signal",
        "native_id": native_id,
        "kind": "i_signal",
        "source_id": "synth",
    }
    return key, node


def _group(req, node_keys):
    return {
        "key": req["key"],
        "source_id": req["source_id"],
        "item_id": req["item_id"],
        "nodes": list(node_keys),
    }


def _case(verifies, uses, stimulus, case_id="TC-1"):
    return {
        "id": case_id,
        "verifies": list(verifies),
        "preconditions": "ready",
        "stimulus": stimulus,
        "expected_observable_result": "the lamp is on",
        "pass_criterion": "the lamp stays on",
        "kind": "positive",
        "uses": list(uses),
    }


def _validate(cases, requirements, groups, nodes):
    return validate_output(
        {"cases": cases},
        requirements,
        {"requirements": groups},
        projection={"nodes": nodes, "edges": []},
    )


def _invented(check, value, case="TC-1"):
    return {"case": case, "check": check, "value": value}


def test_a_high_beam_accepted_when_one_context_signal_ends_with_it():
    """HIGH_BEAM is accepted when LIGHT_STALK.HIGH_BEAM is in the case's context."""
    req = _requirement("A")
    key, node = _signal("LIGHT_STALK.HIGH_BEAM")
    result = _validate(
        [_case(["A"], [key], "set HIGH_BEAM")],
        [req],
        [_group(req, [key])],
        [node],
    )
    assert result == {"issues": [], "passed": True}


def test_b_high_beam_rejected_when_two_context_signals_end_with_it():
    """Two context signals ending in .HIGH_BEAM leave the bare name invented."""
    req = _requirement("A")
    left_key, left = _signal("LIGHT_STALK.HIGH_BEAM")
    right_key, right = _signal("WIPER_STALK.HIGH_BEAM")
    result = _validate(
        [_case(["A"], [left_key, right_key], "set HIGH_BEAM")],
        [req],
        [_group(req, [left_key, right_key])],
        [left, right],
    )
    assert result == {
        "passed": False,
        "issues": [_invented("no_invented_names", "HIGH_BEAM")],
    }


def test_c_high_beam_rejected_when_the_signal_is_another_requirements_context():
    """The only HIGH_BEAM signal belongs to a requirement this case does not verify."""
    own = _requirement("A")
    other = _requirement("B")
    own_key, own_node = _signal("LIGHT_STALK.LOW_BEAM")
    other_key, other_node = _signal("LIGHT_STALK.HIGH_BEAM")
    case = _case(["A"], [own_key], "set HIGH_BEAM")
    result = _validate(
        [case],
        [own, other],
        [_group(own, [own_key]), _group(other, [other_key])],
        [own_node, other_node],
    )
    invented = [issue for issue in result["issues"] if issue["check"] == "no_invented_names"]
    assert invented == [_invented("no_invented_names", "HIGH_BEAM")]


def test_d_wrong_case_high_beam_is_rejected():
    """high_beam does not match HIGH_BEAM. The comparison stays case-sensitive."""
    req = _requirement("A")
    key, node = _signal("LIGHT_STALK.HIGH_BEAM")
    result = _validate(
        [_case(["A"], [key], "set high_beam")],
        [req],
        [_group(req, [key])],
        [node],
    )
    assert result == {
        "passed": False,
        "issues": [_invented("no_invented_names", "high_beam")],
    }
