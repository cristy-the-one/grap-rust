"""Operation registry and static check for a procedure file (P-10).

Loading a procedure checks it completely and refuses it, listing every
problem, before anything runs (§3 of docs/spec-procedure-runtime.md).
The eight operations and their signatures are §4. This module does not
run a step and does not call a model.

Public API, for the interpreter that stacks on this branch:

- ``OPERATIONS`` — the eight operations, in §4 order. Each ``Operation``
  has ``name``, ``required``, ``optional`` and ``outputs``.
- ``GUARD_OPS`` — ``eq``, ``ne``, ``gt``, ``lt``, ``empty``, ``not_empty``.
- ``load_procedure(text, source_path=None) -> Procedure``
- ``load_procedure_file(path) -> Procedure``
- ``ProcedureRefused.problems`` — every problem, in file order.
  ``Problem`` has ``code``, ``message`` and ``step_id``.
- ``Procedure`` has ``name``, ``description``, ``version``, ``steps``,
  ``source_path``.
- ``Step`` has ``id``, ``operation``, ``inputs``, ``guards``, ``max_rounds``.
  ``max_rounds`` is whatever the file set, or ``None`` when the file does
  not set it. The interpreter applies §6's default of 2.
- ``Guard`` has ``ref``, ``op``, ``value``.

A step produces the outputs its operation gives. Output declarations in
the file are descriptive and are not part of the signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

GUARD_OPS = ("eq", "ne", "gt", "lt", "empty", "not_empty")

_GUARD_KEYS = frozenset({"ref", "op", "value"})


@dataclass(frozen=True)
class Operation:
    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    outputs: tuple[str, ...]


def _operation(
    name: str,
    required: tuple[str, ...],
    optional: tuple[str, ...],
    outputs: tuple[str, ...],
) -> Operation:
    return Operation(name, required, optional, outputs)


# §4, in table order. choose_template's optional input is the template ID
# (`template_id`); the operation gives `template`.
OPERATIONS = MappingProxyType({
    "select_requirements": _operation(
        "select_requirements", ("scope",), (), ("requirements",),
    ),
    "fetch_context": _operation(
        "fetch_context", ("requirements",), (), ("context",),
    ),
    "choose_template": _operation(
        "choose_template", ("requirements",), ("template_id",), ("template",),
    ),
    "compose_instructions": _operation(
        "compose_instructions",
        ("requirements", "context", "template"),
        ("issues",),
        ("instructions",),
    ),
    "invoke_model": _operation(
        "invoke_model", ("instructions",), (), ("draft",),
    ),
    "validate_output": _operation(
        "validate_output",
        ("draft", "requirements", "context"),
        (),
        ("issues", "passed"),
    ),
    "refine": _operation("refine", ("issues",), (), ("issues",)),
    "emit": _operation(
        "emit", ("draft", "template", "passed"), (), ("artifact",),
    ),
})


@dataclass(frozen=True)
class Guard:
    ref: str
    op: str
    value: Any


@dataclass(frozen=True)
class Step:
    id: str
    operation: str
    inputs: dict[str, Any]
    guards: tuple[Guard, ...]
    max_rounds: Any = None


@dataclass(frozen=True)
class Procedure:
    name: str
    description: str
    version: str
    steps: tuple[Step, ...]
    source_path: str | None = None


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    step_id: str | None = None


class ProcedureRefused(Exception):
    """The file was checked completely and refused. Nothing ran."""

    def __init__(self, problems: list[Problem]):
        self.problems = tuple(problems)
        super().__init__("\n".join(problem.message for problem in self.problems))


def load_procedure(text: str, source_path: str | None = None) -> Procedure:
    """Parse a procedure file and refuse it unless §3 is satisfied."""
    data = yaml.safe_load(text)
    problems = _problems(data)
    if problems:
        raise ProcedureRefused(problems)
    return _build(data, source_path)


def load_procedure_file(path: str | Path) -> Procedure:
    """Load a procedure file from disk. Refusal happens before return."""
    path = Path(path)
    return load_procedure(path.read_text(encoding="utf-8"), source_path=str(path))


def _problems(data: Any) -> list[Problem]:
    """Every §3 problem, in file order. Empty when the file may be loaded."""
    if not isinstance(data, dict):
        return [Problem("invalid_procedure", "procedure file is not a mapping")]
    raw_steps = data.get("steps", [])
    if raw_steps is None:
        raw_steps = []
    if not isinstance(raw_steps, list):
        return [Problem("invalid_procedure", "steps is not a list")]

    problems: list[Problem] = []
    seen: set[str] = set()
    produced: dict[str, set[str]] = {}
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, Mapping):
            problems.append(Problem("invalid_step", f"step {index} is not a mapping"))
            continue
        step_id = _text(raw.get("id"), "")
        if step_id in seen:
            problems.append(Problem(
                "duplicate_step_id",
                f"steps share id {step_id!r}",
                step_id,
            ))
        seen.add(step_id)

        operation_name = raw.get("operation")
        operation = OPERATIONS.get(operation_name) if isinstance(operation_name, str) else None
        if operation is None:
            label = operation_name if isinstance(operation_name, str) else repr(operation_name)
            problems.append(Problem(
                "unknown_operation",
                f"step {step_id!r} operation {label!r} is not one of the eight",
                step_id,
            ))

        inputs = raw.get("inputs")
        if inputs is None:
            inputs = {}
        if not isinstance(inputs, Mapping):
            problems.append(Problem(
                "invalid_inputs",
                f"step {step_id!r} inputs is not a mapping of input names",
                step_id,
            ))
            inputs = {}
        elif operation is not None:
            for name in operation.required:
                if name not in inputs:
                    problems.append(Problem(
                        "missing_input",
                        f"step {step_id!r} lacks required input {name!r}",
                        step_id,
                    ))
            for name in inputs:
                if name not in operation.required and name not in operation.optional:
                    problems.append(Problem(
                        "unexpected_input",
                        f"step {step_id!r} names input {name!r} which {operation.name} does not take",
                        step_id,
                    ))
        for name, value in inputs.items():
            if isinstance(value, str) and ".outputs." in value and not _produced(value, produced):
                problems.append(Problem(
                    "output_not_produced",
                    f"step {step_id!r} input {name!r} refers to {value!r} "
                    f"and no earlier step produces that output",
                    step_id,
                ))
        problems.extend(_guard_problems(step_id, raw.get("guards")))
        if operation is not None:
            produced.setdefault(step_id, set()).update(operation.outputs)
    return problems


def _produced(ref: str, produced: Mapping[str, set[str]]) -> bool:
    source, separator, output = ref.partition(".outputs.")
    if separator == "" or source == "" or output == "":
        return False
    return output in produced.get(source, ())


def _guard_problems(step_id: str, guards: Any) -> list[Problem]:
    if guards is None:
        return []
    if not isinstance(guards, list):
        return [Problem(
            "invalid_guard",
            f"step {step_id!r} guard is not {{ref, op, value}} (got {guards!r})",
            step_id,
        )]
    allowed = ", ".join(GUARD_OPS)
    problems: list[Problem] = []
    for index, guard in enumerate(guards):
        if not _guard_ok(guard):
            problems.append(Problem(
                "invalid_guard",
                f"step {step_id!r} guard {index} is not {{ref, op, value}} "
                f"with op one of {allowed} (got {guard!r})",
                step_id,
            ))
    return problems


def _guard_ok(guard: Any) -> bool:
    if not isinstance(guard, Mapping) or set(guard) != _GUARD_KEYS:
        return False
    ref = guard.get("ref")
    op = guard.get("op")
    return isinstance(ref, str) and ref != "" and op in GUARD_OPS


def _build(data: Mapping[str, Any], source_path: str | None) -> Procedure:
    steps: list[Step] = []
    raw_steps = data.get("steps") or []
    if isinstance(raw_steps, list):
        for raw in raw_steps:
            if not isinstance(raw, Mapping):
                continue
            steps.append(_build_step(raw))
    return Procedure(
        name=_text(data.get("name"), "Unnamed Procedure"),
        description=_text(data.get("description"), ""),
        version=_text(data.get("version"), "0.0.0"),
        steps=tuple(steps),
        source_path=source_path,
    )


def _build_step(raw: Mapping[str, Any]) -> Step:
    inputs = raw.get("inputs")
    if not isinstance(inputs, Mapping):
        inputs = {}
    guards: list[Guard] = []
    raw_guards = raw.get("guards")
    if isinstance(raw_guards, list):
        for guard in raw_guards:
            if isinstance(guard, Mapping) and _GUARD_KEYS <= set(guard):
                ref = guard.get("ref")
                op = guard.get("op")
                if isinstance(ref, str) and isinstance(op, str):
                    guards.append(Guard(ref, op, guard.get("value")))
    return Step(
        id=_text(raw.get("id"), ""),
        operation=_text(raw.get("operation"), ""),
        inputs=dict(inputs),
        guards=tuple(guards),
        max_rounds=raw.get("max_rounds") if "max_rounds" in raw else None,
    )


def _text(value: Any, default: str) -> str:
    if value is None:
        return default
    return str(value)
