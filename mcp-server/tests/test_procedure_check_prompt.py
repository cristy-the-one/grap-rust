"""RUN-05: the prompt's check example is one the parser and the check rules accept.

The example line is read from FIXED_TEXT. Pedal.Pressed and Lamp.On are the
synthetic names in that example. They are not a corpus.
"""

import json

import pytest

from grap_mcp.procedure_model import ModelError, _parse_checks
from grap_mcp.procedure_template import FIXED_TEXT
from grap_mcp.procedure_validate import _one_case_checks


def _example_from_prompt():
    """The check object on the line that starts with {"id": "c1"."""
    lines = [
        line for line in FIXED_TEXT.splitlines()
        if line.startswith('{"id": "c1"')
    ]
    assert len(lines) == 1, lines
    return json.loads(lines[0])


def _check_issues(example):
    """Parse one check, then run the validator's check rules."""
    parsed = _parse_checks(0, [example])
    pedal = "Signal | fix | Pedal.Pressed"
    lamp = "Signal | fix | Lamp.On"
    nodes = {
        pedal: {"key": pedal, "type": "Signal", "native_id": "Pedal.Pressed"},
        lamp: {"key": lamp, "type": "Signal", "native_id": "Lamp.On"},
    }
    case = {"id": "TC-1", "uses": [pedal, lamp], "checks": parsed}
    return _one_case_checks(case, "TC-1", nodes)


def test_prompt_example_check_is_accepted_with_no_check_issues():
    """The example in the prompt parses and has 0 check issues."""
    issues = _check_issues(_example_from_prompt())
    assert issues == []


def test_prompt_example_with_mode_sometimes_is_refused():
    """mode sometimes is not a check mode. The example path can fail."""
    example = _example_from_prompt()
    example["mode"] = "sometimes"
    with pytest.raises(ModelError, match="mode"):
        _check_issues(example)
