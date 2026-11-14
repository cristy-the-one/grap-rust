"""P-11: the interpreter. Data flow, guards, and the one bounded loop (§6).

Operations are stubs. A failing validation ends as ``failed-validation``
after ``max_rounds`` send-backs (default 2), keeps the last draft, and
does not emit. No step is a model call.
"""

import inspect

import pytest

from grap_mcp.procedure_interpreter import (
    DEFAULT_MAX_ROUNDS,
    ProcedureRunError,
    StubOperations,
    interpret,
)
from grap_mcp.procedure_runtime import load_procedure

# §6: refine may send issues back this many times when the file omits max_rounds.
assert DEFAULT_MAX_ROUNDS == 2


def _pg(body: str) -> str:
    return inspect.cleandoc(body) + "\n"


def _loop(max_rounds: str = "", *, guard: bool = True) -> str:
    lines = [
        "name: generate-test-spec",
        'version: "0.1"',
        "steps:",
        "  - id: select",
        "    operation: select_requirements",
        "    inputs:",
        "      scope:",
        "        - REQ-1",
        "  - id: context",
        "    operation: fetch_context",
        "    inputs:",
        "      requirements: select.outputs.requirements",
        "  - id: template",
        "    operation: choose_template",
        "    inputs:",
        "      requirements: select.outputs.requirements",
        "  - id: compose",
        "    operation: compose_instructions",
        "    inputs:",
        "      requirements: select.outputs.requirements",
        "      context: context.outputs.context",
        "      template: template.outputs.template",
        "  - id: model",
        "    operation: invoke_model",
        "    inputs:",
        "      instructions: compose.outputs.instructions",
        "  - id: check",
        "    operation: validate_output",
        "    inputs:",
        "      draft: model.outputs.draft",
        "      requirements: select.outputs.requirements",
        "      context: context.outputs.context",
        "  - id: revise",
        "    operation: refine",
        "    inputs:",
        "      issues: check.outputs.issues",
    ]
    if max_rounds != "":
        lines.append(f"    max_rounds: {max_rounds}")
    if guard:
        lines.append("    guards:")
        lines.append("      - {ref: check.outputs.passed, op: eq, value: false}")
    lines.extend([
        "  - id: publish",
        "    operation: emit",
        "    inputs:",
        "      draft: model.outputs.draft",
        "      template: template.outputs.template",
        "      passed: check.outputs.passed",
    ])
    return "\n".join(lines) + "\n"


def _run(body: str, handlers=None):
    procedure = load_procedure(body if body.endswith("\n") else _pg(body))
    ops = StubOperations(handlers)
    return interpret(procedure, ops), ops, procedure


def _counts(ops: StubOperations) -> dict[str, int]:
    counts: dict[str, int] = {}
    for operation, _inputs in ops.calls:
        counts[operation] = counts.get(operation, 0) + 1
    return counts


def _always_fail(_inputs):
    return {"issues": ["missing stimulus"], "passed": False}


def _fail_then_pass(limit):
    seen = {"n": 0}

    def validate(_inputs):
        seen["n"] += 1
        if seen["n"] <= limit:
            return {"issues": [f"fail-{seen['n']}"], "passed": False}
        return {"issues": [], "passed": True}

    return validate


def test_failing_stub_sends_back_at_most_max_rounds_then_keeps_the_last_draft():
    """§6 and the §9 bounded-loop check.

    ``max_rounds`` is how many times refine may send issues back, not how
    many times validation may run. The attempt after the last send-back
    still happens. When it fails, the run is ``failed-validation``, the
    last draft is kept, and emit is not called.
    """
    run, ops, _procedure = _run(_loop(), {"validate_output": _always_fail})
    counts = _counts(ops)
    assert counts["select_requirements"] == 1
    assert counts["fetch_context"] == 1
    assert counts["choose_template"] == 1
    assert counts["compose_instructions"] == DEFAULT_MAX_ROUNDS + 1
    assert counts["invoke_model"] == DEFAULT_MAX_ROUNDS + 1
    assert counts["validate_output"] == DEFAULT_MAX_ROUNDS + 1
    assert counts["refine"] == DEFAULT_MAX_ROUNDS
    assert "emit" not in counts
    assert run.status == "failed-validation"
    assert run.rounds == DEFAULT_MAX_ROUNDS
    assert run.artifact is None
    drafts = [inputs["draft"] for operation, inputs in ops.calls if operation == "validate_output"]
    assert run.rejected == drafts[-1]
    assert run.rejected != drafts[0]
    assert run.outputs["check"]["passed"] is False
    validate_events = [event for event in run.events if event.step_id == "check"]
    assert [event.iteration for event in validate_events] == [1, 2, 3]
    refine_events = [event for event in run.events if event.step_id == "revise" and event.kind == "ran"]
    assert [event.iteration for event in refine_events] == [1, 2]
    assert not any(event.step_id == "publish" and event.kind == "ran" for event in run.events)


def test_explicit_max_rounds_two_matches_the_omitted_default():
    omitted, _ops_a, _proc_a = _run(_loop(), {"validate_output": _always_fail})
    explicit, _ops_b, _proc_b = _run(_loop("2"), {"validate_output": _always_fail})
    null_rounds, _ops_c, _proc_c = _run(_loop("null"), {"validate_output": _always_fail})
    assert omitted.rounds == explicit.rounds == null_rounds.rounds == 2
    assert omitted.status == explicit.status == null_rounds.status == "failed-validation"


def test_max_rounds_one_sends_back_once():
    run, ops, _procedure = _run(_loop("1"), {"validate_output": _always_fail})
    counts = _counts(ops)
    assert counts["validate_output"] == 2
    assert counts["refine"] == 1
    assert counts["compose_instructions"] == 2
    assert "emit" not in counts
    assert run.status == "failed-validation"
    assert run.rounds == 1
    assert run.artifact is None


def test_max_rounds_zero_does_not_send_back():
    run, ops, _procedure = _run(_loop("0"), {"validate_output": _always_fail})
    counts = _counts(ops)
    assert counts["validate_output"] == 1
    assert "refine" not in counts
    assert "emit" not in counts
    assert run.status == "failed-validation"
    assert run.rounds == 0
    assert run.rejected == ops.calls[-1][1]["draft"]


def test_refine_sends_its_own_issues_to_the_next_compose():
    def refine(_inputs):
        return {"issues": ["from-refine"]}

    _run_result, ops, _procedure = _run(
        _loop("1"),
        {"validate_output": _always_fail, "refine": refine},
    )
    composes = [inputs for operation, inputs in ops.calls if operation == "compose_instructions"]
    assert "issues" not in composes[0]
    assert composes[1]["issues"] == ["from-refine"]
    assert composes[1]["issues"] != ["missing stimulus"]


def test_a_later_pass_emits_and_does_not_keep_a_rejected_draft():
    run, ops, _procedure = _run(_loop(), {"validate_output": _fail_then_pass(1)})
    assert run.status == "ok"
    assert run.rounds == 1
    assert run.rejected is None
    assert run.artifact is not None
    assert run.artifact["passed"] is True
    assert _counts(ops)["emit"] == 1
    assert _counts(ops)["refine"] == 1
    skipped = [
        event for event in run.events
        if event.step_id == "revise" and event.kind == "skipped"
    ]
    assert len(skipped) == 1
    assert skipped[0].iteration == 2
    assert skipped[0].reason == "guard"
    assert skipped[0].guard_op == "eq"
    assert skipped[0].guard_ref == "check.outputs.passed"
    assert run.outputs["check"]["passed"] is True
    assert "revise" not in run.outputs


def test_passed_validation_does_not_loop_when_refine_has_no_guard():
    run, ops, _procedure = _run(_loop(guard=False))
    counts = _counts(ops)
    assert counts["compose_instructions"] == 1
    assert counts["validate_output"] == 1
    assert counts["refine"] == 1
    assert counts["emit"] == 1
    assert run.status == "ok"
    assert run.rounds == 0
    assert run.rejected is None
    assert any(event.step_id == "revise" and event.kind == "ran" for event in run.events)


def test_emit_max_rounds_does_not_change_the_refine_limit():
    text = _loop()
    text = text.replace(
        "  - id: publish\n    operation: emit\n",
        "  - id: publish\n    operation: emit\n    max_rounds: 9\n",
    )
    procedure = load_procedure(text)
    assert procedure.steps[-1].max_rounds == 9
    assert procedure.steps[-2].max_rounds is None
    ops = StubOperations({"validate_output": _always_fail})
    run = interpret(procedure, ops)
    assert run.rounds == DEFAULT_MAX_ROUNDS
    assert _counts(ops)["refine"] == DEFAULT_MAX_ROUNDS


def test_a_failed_validation_with_no_refine_step_emits_nothing():
    body = _pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope:
                - REQ-1
          - id: model
            operation: invoke_model
            inputs:
              instructions: literal
          - id: check
            operation: validate_output
            inputs:
              draft: model.outputs.draft
              requirements: select.outputs.requirements
              context: {}
          - id: publish
            operation: emit
            inputs:
              draft: model.outputs.draft
              template: template
              passed: check.outputs.passed
        """
    )
    run, ops, _procedure = _run(body, {"validate_output": _always_fail})
    assert run.status == "failed-validation"
    assert run.rounds == 0
    assert run.artifact is None
    assert run.rejected is not None
    assert "emit" not in _counts(ops)
    assert "refine" not in _counts(ops)


def test_data_flow_passes_values_not_reference_strings():
    body = _pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope:
                - REQ-1
                - REQ-2
          - id: context
            operation: fetch_context
            inputs:
              requirements: select.outputs.requirements
          - id: template
            operation: choose_template
            inputs:
              requirements: select.outputs.requirements
              template_id: default
        """
    )
    run, ops, procedure = _run(body)
    fetch = next(inputs for operation, inputs in ops.calls if operation == "fetch_context")
    choose = next(inputs for operation, inputs in ops.calls if operation == "choose_template")
    assert fetch["requirements"] == ["REQ-1", "REQ-2"]
    assert fetch["requirements"] != "select.outputs.requirements"
    assert choose["requirements"] == ["REQ-1", "REQ-2"]
    assert choose["template_id"] == "default"
    assert run.outputs["select"]["requirements"] == ["REQ-1", "REQ-2"]
    assert run.outputs["select"]["requirements"] is not procedure.steps[0].inputs["scope"]
    assert run.status == "ok"
    assert run.artifact is None
    assert run.rejected is None
    assert run.rounds == 0


def test_a_handler_cannot_mutate_the_procedure_or_an_earlier_output():
    def fetch(inputs):
        inputs["requirements"].append("REQ-9")
        return {"context": inputs["requirements"]}

    body = _pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope:
                - REQ-1
          - id: context
            operation: fetch_context
            inputs:
              requirements: select.outputs.requirements
        """
    )
    run, _ops, procedure = _run(body, {"fetch_context": fetch})
    assert procedure.steps[0].inputs["scope"] == ["REQ-1"]
    assert run.outputs["select"]["requirements"] == ["REQ-1"]
    assert run.outputs["context"]["context"] == ["REQ-1", "REQ-9"]


def test_two_default_runs_match_and_do_not_call_a_model():
    procedure = load_procedure(_loop())
    first = interpret(procedure)
    second = interpret(procedure)
    assert first == second
    assert first.status == "ok"
    assert first.artifact is not None
    assert first.rejected is None
    assert first.rounds == 0
    source = inspect.getsource(inspect.getmodule(interpret))
    for banned in ("openai", "httpx", "urllib", "requests", "ollama", "http.client"):
        assert banned not in source
    assert "load_procedure_graph" not in source


@pytest.mark.parametrize(
    ("scope", "op", "value", "runs"),
    [
        ("REQ-1", "eq", "REQ-1", True),
        ("REQ-1", "eq", "other", False),
        ("REQ-1", "ne", "other", True),
        ("REQ-1", "ne", "REQ-1", False),
        (5, "gt", 1, True),
        (5, "gt", 5, False),
        (5, "gt", 9, False),
        (5, "lt", 9, True),
        (5, "lt", 5, False),
        (5, "lt", 1, False),
        ("b", "gt", "a", True),
        ("a", "gt", "b", False),
        ([], "empty", None, True),
        (["REQ-1"], "empty", None, False),
        (["REQ-1"], "not_empty", None, True),
        ([], "not_empty", None, False),
        (0, "empty", None, False),
        (0, "not_empty", None, True),
        ("", "empty", None, True),
        (False, "empty", None, False),
    ],
)
def test_guard_ops_skip_when_false_and_record_the_skip(scope, op, value, runs):
    run, ops, _procedure = _run(_guard_file(scope, op, value))
    if runs:
        assert _counts(ops)["emit"] == 1
        assert run.artifact is not None
        assert not any(event.kind == "skipped" for event in run.events)
    else:
        assert "emit" not in _counts(ops)
        assert run.artifact is None
        assert run.status == "ok"
        skipped = [event for event in run.events if event.kind == "skipped"]
        assert len(skipped) == 1
        assert skipped[0].step_id == "publish"
        assert skipped[0].operation == "emit"
        assert skipped[0].reason == "guard"
        assert skipped[0].guard_op == op
        assert skipped[0].guard_ref == "select.outputs.requirements"
        assert "publish" not in run.outputs


def _yaml_value(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[" + ", ".join(_yaml_value(item) for item in value) + "]"
    return str(value)


def _guard_file(scope, op, value) -> str:
    return _pg(
        f"""
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: {_yaml_value(scope)}
          - id: publish
            operation: emit
            inputs:
              draft: select.outputs.requirements
              template: plain
              passed: true
            guards:
              - {{ref: select.outputs.requirements, op: {op}, value: {_yaml_value(value)}}}
        """
    )


def test_every_guard_must_hold():
    body = _pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: 5
          - id: publish
            operation: emit
            inputs:
              draft: select.outputs.requirements
              template: plain
              passed: true
            guards:
              - {ref: select.outputs.requirements, op: gt, value: 0}
              - {ref: select.outputs.requirements, op: lt, value: 3}
        """
    )
    run, ops, _procedure = _run(body)
    assert "emit" not in _counts(ops)
    skipped = [event for event in run.events if event.kind == "skipped"]
    assert len(skipped) == 1
    assert skipped[0].guard_op == "lt"

    held = _pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: 5
          - id: publish
            operation: emit
            inputs:
              draft: select.outputs.requirements
              template: plain
              passed: true
            guards:
              - {ref: select.outputs.requirements, op: gt, value: 0}
              - {ref: select.outputs.requirements, op: lt, value: 10}
        """
    )
    run, ops, _procedure = _run(held)
    assert _counts(ops)["emit"] == 1
    assert run.artifact["draft"] == 5


def test_a_broken_later_guard_is_not_hidden_by_an_earlier_false_one():
    body = _pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: 1
          - id: gone
            operation: select_requirements
            inputs:
              scope: 2
            guards:
              - {ref: select.outputs.requirements, op: eq, value: 0}
          - id: publish
            operation: emit
            inputs:
              draft: select.outputs.requirements
              template: plain
              passed: true
            guards:
              - {ref: select.outputs.requirements, op: eq, value: 0}
              - {ref: gone.outputs.requirements, op: eq, value: 1}
        """
    )
    # The load check (P-10a) accepts this file: an earlier step produces the
    # output. At run time that step is skipped, so the output is not there.
    procedure = load_procedure(body)
    with pytest.raises(ProcedureRunError, match="gone.outputs.requirements"):
        interpret(procedure)


def test_eq_does_not_treat_true_as_one():
    run, ops, _procedure = _run(_guard_file(True, "eq", 1))
    assert "emit" not in _counts(ops)
    assert run.events[-1].kind == "skipped"

    run, ops, _procedure = _run(_guard_file(True, "eq", True))
    assert _counts(ops)["emit"] == 1


def test_gt_does_not_order_booleans_or_mixed_types():
    procedure = load_procedure(_guard_file(True, "gt", 0))
    with pytest.raises(ProcedureRunError, match="publish") as caught:
        interpret(procedure)
    assert "gt" in str(caught.value)

    procedure = load_procedure(_guard_file("5", "gt", 1))
    with pytest.raises(ProcedureRunError, match="publish"):
        interpret(procedure)


def test_a_skipped_step_does_not_keep_an_output_for_a_later_reference():
    body = _pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: []
          - id: context
            operation: fetch_context
            inputs:
              requirements: select.outputs.requirements
            guards:
              - {ref: select.outputs.requirements, op: not_empty, value: null}
          - id: template
            operation: choose_template
            inputs:
              requirements: context.outputs.context
        """
    )
    procedure = load_procedure(body)
    with pytest.raises(ProcedureRunError, match="context.outputs.context") as caught:
        interpret(procedure)
    assert "template" in str(caught.value)


def test_a_skip_inside_the_loop_drops_the_previous_output():
    def compose(inputs):
        compose.n += 1
        return {"instructions": "go" if compose.n == 1 else ""}

    compose.n = 0

    def invoke(inputs):
        if inputs["instructions"] == "":
            raise AssertionError("invoke ran on empty instructions")
        return {"draft": {"text": inputs["instructions"]}}

    body = _loop("1")
    # Guard invoke so the second pass skips it, then validate must not see
    # the draft from the first pass.
    body = body.replace(
        "  - id: model\n    operation: invoke_model\n    inputs:\n"
        "      instructions: compose.outputs.instructions\n",
        "  - id: model\n    operation: invoke_model\n    inputs:\n"
        "      instructions: compose.outputs.instructions\n"
        "    guards:\n"
        "      - {ref: compose.outputs.instructions, op: not_empty, value: null}\n",
    )
    procedure = load_procedure(body)
    ops = StubOperations({
        "compose_instructions": compose,
        "invoke_model": invoke,
        "validate_output": _always_fail,
    })
    with pytest.raises(ProcedureRunError, match="model.outputs.draft"):
        interpret(procedure, ops)
    assert _counts(ops)["invoke_model"] == 1
    assert _counts(ops)["compose_instructions"] == 2


def test_validate_passed_must_be_a_boolean():
    def validate(_inputs):
        return {"issues": [], "passed": "false"}

    procedure = load_procedure(_loop())
    ops = StubOperations({"validate_output": validate})
    with pytest.raises(ProcedureRunError, match="passed") as caught:
        interpret(procedure, ops)
    assert "check" in str(caught.value)
    assert "emit" not in _counts(ops)


def test_a_stub_must_return_exactly_the_operation_outputs():
    def select(_inputs):
        return {"requirements": [], "extra": 1}

    procedure = load_procedure(_pg(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
        """
    ))
    with pytest.raises(ProcedureRunError, match="select") as caught:
        interpret(procedure, StubOperations({"select_requirements": select}))
    assert "requirements" in str(caught.value)


@pytest.mark.parametrize(
    "max_rounds",
    ["-1", "true", "'2'", "1.5"],
)
def test_a_bad_max_rounds_is_refused_before_any_step(max_rounds):
    procedure = load_procedure(_loop(max_rounds))
    ops = StubOperations()
    with pytest.raises(ProcedureRunError, match="max_rounds"):
        interpret(procedure, ops)
    assert ops.calls == []


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            """
            steps:
              - id: a
                operation: validate_output
                inputs:
                  draft: x
                  requirements: y
                  context: z
              - id: b
                operation: validate_output
                inputs:
                  draft: x
                  requirements: y
                  context: z
            """,
            "validate_output",
        ),
        (
            """
            steps:
              - id: a
                operation: refine
                inputs:
                  issues: []
              - id: b
                operation: refine
                inputs:
                  issues: []
            """,
            "refine",
        ),
        (
            """
            steps:
              - id: revise
                operation: refine
                inputs:
                  issues: []
              - id: check
                operation: validate_output
                inputs:
                  draft: x
                  requirements: y
                  context: z
            """,
            "revise",
        ),
        (
            """
            steps:
              - id: check
                operation: validate_output
                inputs:
                  draft: x
                  requirements: y
                  context: z
              - id: revise
                operation: refine
                inputs:
                  issues: check.outputs.issues
            """,
            "compose_instructions",
        ),
        (
            """
            steps:
              - id: publish
                operation: emit
                inputs:
                  draft: x
                  template: y
                  passed: true
              - id: check
                operation: validate_output
                inputs:
                  draft: x
                  requirements: y
                  context: z
            """,
            "publish",
        ),
        (
            """
            steps:
              - id: check
                operation: validate_output
                inputs:
                  draft: x
                  requirements: y
                  context: z
              - id: publish
                operation: emit
                inputs:
                  draft: x
                  template: y
                  passed: true
              - id: revise
                operation: refine
                inputs:
                  issues: check.outputs.issues
            """,
            "publish",
        ),
    ],
)
def test_a_loop_the_interpreter_cannot_run_honestly_is_refused(body, match):
    procedure = load_procedure(_pg(body))
    ops = StubOperations()
    with pytest.raises(ProcedureRunError, match=match):
        interpret(procedure, ops)
    assert ops.calls == []


def test_an_empty_procedure_runs_nothing():
    run, ops, _procedure = _run(_pg("name: empty\nsteps: []"))
    assert run.status == "ok"
    assert run.rounds == 0
    assert run.events == ()
    assert run.outputs == {}
    assert run.artifact is None
    assert run.rejected is None
    assert ops.calls == []


def test_a_step_between_validate_and_refine_runs_on_the_way_back():
    text = _loop("1")
    text = text.replace(
        "  - id: revise\n",
        "  - id: mid\n"
        "    operation: choose_template\n"
        "    inputs:\n"
        "      requirements: select.outputs.requirements\n"
        "  - id: revise\n",
    )
    run, ops, _procedure = _run(text, {"validate_output": _always_fail})
    assert _counts(ops)["choose_template"] == 2  # prefix template, plus mid once
    assert _counts(ops)["refine"] == 1
    assert _counts(ops)["validate_output"] == 2
    assert "emit" not in _counts(ops)
    assert run.status == "failed-validation"
    assert any(event.step_id == "mid" and event.kind == "ran" for event in run.events)
