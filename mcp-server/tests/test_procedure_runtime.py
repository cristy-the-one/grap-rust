"""P-10: the operation registry and the static check (§3, §4).

A procedure file is refused at load, with every problem listed. The audit's
reproduction — an operation that does not exist, and a false guard — is
refused, and the refusal names the operation. No step is executed.
"""

import inspect
import textwrap

import pytest

from grap_mcp.procedure_runtime import (
    GUARD_OPS,
    OPERATIONS,
    ProcedureRefused,
    load_procedure,
    load_procedure_file,
)

# The September audit's file, as the architecture probe writes it.
AUDIT = (
    "name: architecture-probe\n"
    "version: '1'\n"
    "steps:\n"
    "  - id: impossible\n"
    "    operation: operation_that_does_not_exist\n"
    "    guards: ['false']\n"
)

_EIGHT = (
    "select_requirements",
    "fetch_context",
    "choose_template",
    "compose_instructions",
    "invoke_model",
    "validate_output",
    "refine",
    "emit",
)


def _pg(body: str) -> str:
    return textwrap.dedent(body).strip() + "\n"


def _refuse(body: str) -> ProcedureRefused:
    with pytest.raises(ProcedureRefused) as caught:
        load_procedure(_pg(body))
    return caught.value


def test_audit_reproduction_is_refused_at_load_and_names_the_operation():
    with pytest.raises(ProcedureRefused, match="operation_that_does_not_exist") as caught:
        load_procedure(AUDIT)
    refused = caught.value
    assert [p.code for p in refused.problems] == ["unknown_operation", "invalid_guard"]
    assert all(p.step_id == "impossible" for p in refused.problems)
    assert "operation_that_does_not_exist" in refused.problems[0].message
    assert "false" in refused.problems[1].message


def test_unknown_operation_is_refused():
    refused = _refuse(
        """
        steps:
          - id: impossible
            operation: operation_that_does_not_exist
        """
    )
    assert [p.code for p in refused.problems] == ["unknown_operation"]
    assert "operation_that_does_not_exist" in refused.problems[0].message
    assert refused.problems[0].step_id == "impossible"


def test_missing_required_input_is_refused():
    refused = _refuse(
        """
        steps:
          - id: fetch
            operation: fetch_context
        """
    )
    assert [p.code for p in refused.problems] == ["missing_input"]
    assert "requirements" in refused.problems[0].message
    assert refused.problems[0].step_id == "fetch"

    # Every required name is listed, not only the first.
    refused = _refuse(
        """
        steps:
          - id: compose
            operation: compose_instructions
        """
    )
    assert [p.code for p in refused.problems] == ["missing_input"] * 3
    assert "requirements" in refused.problems[0].message
    assert "context" in refused.problems[1].message
    assert "template" in refused.problems[2].message


def test_unexpected_input_is_refused():
    refused = _refuse(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
              extra: 1
        """
    )
    assert [p.code for p in refused.problems] == ["unexpected_input"]
    assert "extra" in refused.problems[0].message
    assert refused.problems[0].step_id == "select"


def test_unexpected_input_and_its_bad_reference_are_both_listed():
    refused = _refuse(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
              extra: ghost.outputs.x
        """
    )
    assert [p.code for p in refused.problems] == [
        "unexpected_input",
        "output_not_produced",
    ]
    assert "extra" in refused.problems[0].message
    assert "ghost.outputs.x" in refused.problems[1].message


@pytest.mark.parametrize(
    ("body", "ref"),
    [
        (
            """
            steps:
              - id: fetch
                operation: fetch_context
                inputs:
                  requirements: select.outputs.requirements
              - id: select
                operation: select_requirements
                inputs:
                  scope: all
            """,
            "select.outputs.requirements",
        ),
        (
            """
            steps:
              - id: fetch
                operation: fetch_context
                inputs:
                  requirements: ghost.outputs.requirements
            """,
            "ghost.outputs.requirements",
        ),
        (
            """
            steps:
              - id: select
                operation: select_requirements
                inputs:
                  scope: all
              - id: fetch
                operation: fetch_context
                inputs:
                  requirements: select.outputs.context
            """,
            "select.outputs.context",
        ),
        (
            """
            steps:
              - id: select
                operation: select_requirements
                inputs:
                  scope: select.outputs.requirements
            """,
            "select.outputs.requirements",
        ),
    ],
)
def test_input_ref_no_earlier_step_produces_is_refused(body, ref):
    refused = _refuse(body)
    assert [p.code for p in refused.problems] == ["output_not_produced"]
    assert ref in refused.problems[0].message


def test_a_later_step_with_the_same_id_does_not_satisfy_an_earlier_ref():
    """Sharing an id is its own problem. A later step still is not earlier."""
    refused = _refuse(
        """
        steps:
          - id: a
            operation: fetch_context
            inputs:
              requirements: a.outputs.requirements
          - id: a
            operation: select_requirements
            inputs:
              scope: all
        """
    )
    assert [p.code for p in refused.problems] == [
        "output_not_produced",
        "duplicate_step_id",
    ]
    assert "a.outputs.requirements" in refused.problems[0].message


def test_an_earlier_duplicate_does_produce_its_outputs():
    refused = _refuse(
        """
        steps:
          - id: a
            operation: select_requirements
            inputs:
              scope: all
          - id: a
            operation: fetch_context
            inputs:
              requirements: a.outputs.requirements
          - id: use
            operation: fetch_context
            inputs:
              requirements: a.outputs.context
        """
    )
    assert [p.code for p in refused.problems] == ["duplicate_step_id"]


def test_yaml_output_declarations_are_not_what_a_step_produces():
    refused = _refuse(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            outputs:
              bogus: list
          - id: fetch
            operation: fetch_context
            inputs:
              requirements: select.outputs.bogus
        """
    )
    assert [p.code for p in refused.problems] == ["output_not_produced"]
    assert "select.outputs.bogus" in refused.problems[0].message


@pytest.mark.parametrize(
    "body",
    [
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            guards: ['false']
        """,
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            guards: false
        """,
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            guards:
              - {ref: select.outputs.requirements, op: contains, value: x}
        """,
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            guards:
              - {ref: select.outputs.requirements, op: eq}
        """,
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            guards:
              - {ref: '', op: eq, value: 1}
        """,
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            guards:
              - {ref: select.outputs.requirements, op: eq, value: 1, extra: 1}
        """,
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
            guards:
              - false
        """,
    ],
)
def test_guard_not_of_the_form_is_refused(body):
    refused = _refuse(body)
    assert [p.code for p in refused.problems] == ["invalid_guard"]
    assert refused.problems[0].step_id == "select"


def test_duplicate_step_id_is_refused():
    refused = _refuse(
        """
        steps:
          - id: select
            operation: select_requirements
            inputs:
              scope: all
          - id: select
            operation: select_requirements
            inputs:
              scope: other
        """
    )
    assert [p.code for p in refused.problems] == ["duplicate_step_id"]
    assert "select" in refused.problems[0].message
    assert refused.problems[0].step_id == "select"


def test_every_rule_is_listed_together():
    refused = _refuse(
        """
        steps:
          - id: a
            operation: select_requirements
            inputs:
              scope:
                - REQ-1
              extra: 1
          - id: a
            operation: not_an_operation
            inputs:
              requirements: later.outputs.nope
            guards: ['false']
          - id: fetch
            operation: fetch_context
        """
    )
    assert [p.code for p in refused.problems] == [
        "unexpected_input",
        "duplicate_step_id",
        "unknown_operation",
        "output_not_produced",
        "invalid_guard",
        "missing_input",
    ]
    assert "not_an_operation" in str(refused)
    assert "later.outputs.nope" in refused.problems[3].message
    assert len(refused.problems) == 6


def test_valid_procedure_loads_and_does_not_run():
    procedure = load_procedure(
        _pg(
            """
            name: generate-test-spec
            description: one procedure
            version: "0.1"
            steps:
              - id: select
                operation: select_requirements
                inputs:
                  scope:
                    - REQ-1
                notes: descriptive
                references:
                  - kg:requirement.REQ-1
                outputs:
                  requirements: list
                  bogus: list
              - id: context
                operation: fetch_context
                inputs:
                  requirements: select.outputs.requirements
              - id: template
                operation: choose_template
                inputs:
                  requirements: select.outputs.requirements
                  template_id: default
              - id: compose
                operation: compose_instructions
                inputs:
                  requirements: select.outputs.requirements
                  context: context.outputs.context
                  template: template.outputs.template
              - id: model
                operation: invoke_model
                inputs:
                  instructions: compose.outputs.instructions
              - id: check
                operation: validate_output
                inputs:
                  draft: model.outputs.draft
                  requirements: select.outputs.requirements
                  context: context.outputs.context
                guards:
                  - {ref: select.outputs.requirements, op: not_empty, value: null}
                  - {ref: select.outputs.requirements, op: eq, value: false}
                  - {ref: select.outputs.requirements, op: ne, value: ""}
                  - {ref: select.outputs.requirements, op: gt, value: 0}
                  - {ref: select.outputs.requirements, op: lt, value: 10}
                  - {ref: select.outputs.requirements, op: empty, value: false}
              - id: refine
                operation: refine
                inputs:
                  issues: check.outputs.issues
                max_rounds: 2
              - id: compose-again
                operation: compose_instructions
                inputs:
                  requirements: select.outputs.requirements
                  context: context.outputs.context
                  template: template.outputs.template
                  issues: refine.outputs.issues
              - id: emit
                operation: emit
                inputs:
                  draft: model.outputs.draft
                  template: template.outputs.template
                  passed: check.outputs.passed
            """
        )
    )
    assert procedure.name == "generate-test-spec"
    assert procedure.description == "one procedure"
    assert procedure.version == "0.1"
    assert procedure.source_path is None
    assert [step.operation for step in procedure.steps] == [
        "select_requirements",
        "fetch_context",
        "choose_template",
        "compose_instructions",
        "invoke_model",
        "validate_output",
        "refine",
        "compose_instructions",
        "emit",
    ]
    assert procedure.steps[0].inputs["scope"] == ["REQ-1"]
    assert procedure.steps[2].inputs["template_id"] == "default"
    assert procedure.steps[5].guards[0].ref == "select.outputs.requirements"
    assert procedure.steps[5].guards[0].op == "not_empty"
    assert procedure.steps[5].guards[0].value is None
    assert [guard.op for guard in procedure.steps[5].guards] == [
        "not_empty",
        "eq",
        "ne",
        "gt",
        "lt",
        "empty",
    ]
    assert procedure.steps[5].guards[1].value is False
    assert procedure.steps[6].max_rounds == 2
    assert procedure.steps[0].max_rounds is None
    assert not hasattr(procedure.steps[4], "draft")


def test_optional_inputs_may_be_omitted_when_refs_are_earlier():
    procedure = load_procedure(
        _pg(
            """
            steps:
              - id: select
                operation: select_requirements
                inputs:
                  scope: all
              - id: context
                operation: fetch_context
                inputs:
                  requirements: select.outputs.requirements
              - id: template
                operation: choose_template
                inputs:
                  requirements: select.outputs.requirements
              - id: compose
                operation: compose_instructions
                inputs:
                  requirements: select.outputs.requirements
                  context: context.outputs.context
                  template: template.outputs.template
            """
        )
    )
    assert "template_id" not in procedure.steps[2].inputs
    assert "issues" not in procedure.steps[3].inputs


@pytest.mark.parametrize(
    ("name", "required", "optional", "outputs"),
    [
        ("select_requirements", ("scope",), (), ("requirements",)),
        ("fetch_context", ("requirements",), (), ("context",)),
        ("choose_template", ("requirements",), ("template_id",), ("template",)),
        (
            "compose_instructions",
            ("requirements", "context", "template"),
            ("issues",),
            ("instructions",),
        ),
        ("invoke_model", ("instructions",), (), ("draft",)),
        (
            "validate_output",
            ("draft", "requirements", "context"),
            (),
            ("issues", "passed"),
        ),
        ("refine", ("issues",), (), ("issues",)),
        ("emit", ("draft", "template", "passed"), (), ("artifact",)),
    ],
)
def test_signatures_match_section_4(name, required, optional, outputs):
    operation = OPERATIONS[name]
    assert operation.name == name
    assert operation.required == required
    assert operation.optional == optional
    assert operation.outputs == outputs
    assert not callable(operation)


def test_registry_is_the_eight_operations_in_section_4_order():
    assert tuple(OPERATIONS) == _EIGHT
    assert GUARD_OPS == ("eq", "ne", "gt", "lt", "empty", "not_empty")


def test_registry_does_not_call_a_model():
    source = inspect.getsource(inspect.getmodule(load_procedure))
    for banned in ("openai", "httpx", "urllib", "requests", "ollama", "http.client"):
        assert banned not in source


def test_load_procedure_file_refuses_the_audit_file(tmp_path):
    path = tmp_path / "audit.pg.yaml"
    path.write_text(AUDIT, encoding="utf-8")
    with pytest.raises(ProcedureRefused, match="operation_that_does_not_exist"):
        load_procedure_file(path)


def test_load_procedure_file_keeps_the_source_path(tmp_path):
    path = tmp_path / "ok.pg.yaml"
    path.write_text(
        _pg(
            """
            name: ok
            steps:
              - id: select
                operation: select_requirements
                inputs:
                  scope: all
            """
        ),
        encoding="utf-8",
    )
    procedure = load_procedure_file(path)
    assert procedure.source_path == str(path)
    assert procedure.steps[0].id == "select"


def test_a_non_mapping_file_is_refused():
    with pytest.raises(ProcedureRefused) as caught:
        load_procedure("[]\n")
    assert [p.code for p in caught.value.problems] == ["invalid_procedure"]


def test_steps_that_are_not_a_list_are_refused():
    refused = _refuse(
        """
        steps: false
        """
    )
    assert [p.code for p in refused.problems] == ["invalid_procedure"]


def test_a_step_that_is_not_a_mapping_is_refused():
    refused = _refuse(
        """
        steps:
          - false
          - id: select
            operation: select_requirements
            inputs:
              scope: all
        """
    )
    assert [p.code for p in refused.problems] == ["invalid_step"]
