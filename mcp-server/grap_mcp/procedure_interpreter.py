"""Interpreter for one loaded procedure (P-11).

Data flow, guards, and the one bounded loop from §6 of
docs/spec-procedure-runtime.md. Every operation here is a stub. This
module does not call a model, read a graph, render a template, or write
a tracked-run event.

``max_rounds`` (default 2, from the ``refine`` step, or 0 when that step
is absent) is how many times ``refine`` may send issues back to the
nearest earlier ``compose_instructions``. The validation after the last
send-back still runs. If that one fails too, the run status is
``failed-validation``, ``rejected`` keeps the last draft, and ``emit``
does not run.

Judgements this card had to make, and the tests lock in:

- A step runs only when every guard holds. The first guard that does
  not hold is the skip event. A guard whose reference cannot be resolved,
  or whose ``gt``/``lt`` values cannot be ordered, is an error even when
  an earlier guard is already false.
- ``empty`` is true for ``None`` and for a str, bytes, list, dict, tuple,
  or set of length 0. ``0`` and ``False`` are not empty.
- ``eq`` / ``ne`` do not treat ``True`` as ``1``.
- ``gt`` / ``lt`` compare two numbers, or two strings. Booleans are not
  numbers here.
- ``validate_output``'s ``passed`` value must be a boolean.
- One ``validate_output`` and one ``refine``. ``refine`` comes after
  validation, and only when a ``compose_instructions`` step comes before
  it. ``emit`` comes after both, so a failed round cannot emit on the
  way back.
- A skipped step drops whatever it produced on an earlier pass.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from grap_mcp.procedure_runtime import OPERATIONS, Guard, Procedure, Step

DEFAULT_MAX_ROUNDS = 2

_EMPTY_TYPES = (str, bytes, list, dict, tuple, set)


class ProcedureRunError(Exception):
    """The loaded procedure cannot be run honestly. Nothing was emitted."""

    def __init__(self, message: str, step_id: str | None = None):
        self.step_id = step_id
        super().__init__(message)


@dataclass(frozen=True)
class Event:
    kind: str
    step_id: str
    operation: str
    iteration: int
    reason: str | None = None
    guard_ref: str | None = None
    guard_op: str | None = None


@dataclass(frozen=True)
class Run:
    status: str
    rounds: int
    events: tuple[Event, ...]
    outputs: dict[str, dict[str, Any]]
    artifact: Any
    rejected: Any


class StubOperations:
    """Deterministic stand-ins for the eight operations.

    ``validate_output`` returns ``passed=True`` and an empty issue list.
    That is not the check in §7. A caller that wants the bounded loop
    passes a handler. Nothing in this class calls a model.
    """

    def __init__(self, handlers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None):
        supplied = dict(handlers or {})
        unknown = sorted(set(supplied) - set(OPERATIONS))
        if unknown:
            names = ", ".join(unknown)
            raise ProcedureRunError(
                f"stub handler names an operation that is not one of the eight: {names}"
            )
        self._handlers = supplied
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, operation: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        recorded = copy.deepcopy(dict(inputs))
        self.calls.append((operation, recorded))
        handler = self._handlers.get(operation)
        if handler is None:
            return _default_stub(operation, recorded)
        result = handler(recorded)
        if not isinstance(result, Mapping):
            raise ProcedureRunError(
                f"stub for {operation} returned {type(result).__name__}, not a mapping of outputs"
            )
        return dict(result)


def interpret(procedure: Procedure, operations: Callable[..., Any] | None = None) -> Run:
    """Run ``procedure``. ``operations`` defaults to :class:`StubOperations`."""
    if operations is None:
        operations = StubOperations()
    shape = _shape(procedure.steps)
    state = _State()
    index = 0
    steps = procedure.steps
    while index < len(steps):
        step = steps[index]
        ran = _execute(steps, index, operations, state, shape)
        if ran and step.operation == "validate_output" and state.passed is False:
            if state.rounds >= shape.limit or shape.refine_index is None:
                state.status = "failed-validation"
                state.rejected = state.last_draft
                break
            index += 1
            while index < len(steps) and steps[index].operation != "refine":
                _execute(steps, index, operations, state, shape)
                index += 1
            if index >= len(steps):
                state.status = "failed-validation"
                state.rejected = state.last_draft
                break
            if not _execute(steps, index, operations, state, shape):
                state.status = "failed-validation"
                state.rejected = state.last_draft
                break
            state.rounds += 1
            state.pending = copy.deepcopy(state.outputs[steps[index].id]["issues"])
            if shape.compose_index is None:
                raise ProcedureRunError(
                    "refine cannot send issues back: no compose_instructions step is the loop head"
                )
            index = shape.compose_index
            continue
        index += 1
    return Run(
        status=state.status,
        rounds=state.rounds,
        events=tuple(state.events),
        outputs=copy.deepcopy(state.outputs),
        artifact=copy.deepcopy(state.artifact),
        rejected=copy.deepcopy(state.rejected),
    )


class _State:
    def __init__(self) -> None:
        self.outputs: dict[str, dict[str, Any]] = {}
        self.events: list[Event] = []
        self.rounds = 0
        self.status = "ok"
        self.artifact: Any = None
        self.rejected: Any = None
        self.pending: Any = None
        self.last_draft: Any = None
        self.passed: bool | None = None

    def iteration(self) -> int:
        return self.rounds + 1


@dataclass(frozen=True)
class _Shape:
    refine_index: int | None
    compose_index: int | None
    limit: int


def _shape(steps: tuple[Step, ...]) -> _Shape:
    validate_at = _at(steps, "validate_output")
    refine_at = _at(steps, "refine")
    emit_at = _at(steps, "emit")
    problems: list[str] = []
    if len(validate_at) > 1:
        ids = ", ".join(repr(steps[i].id) for i in validate_at)
        problems.append(
            f"procedure has {len(validate_at)} validate_output steps ({ids}); "
            f"the interpreter runs one validation loop"
        )
    if len(refine_at) > 1:
        ids = ", ".join(repr(steps[i].id) for i in refine_at)
        problems.append(
            f"procedure has {len(refine_at)} refine steps ({ids}); "
            f"the interpreter runs one refine loop"
        )
    validate_index = validate_at[0] if len(validate_at) == 1 else None
    refine_index = refine_at[0] if len(refine_at) == 1 else None
    if validate_index is not None and refine_index is not None and refine_index < validate_index:
        problems.append(
            f"refine step {steps[refine_index].id!r} is before validate_output "
            f"step {steps[validate_index].id!r}; refine sends issues back after validation fails"
        )
    compose_index = None
    if validate_index is not None:
        earlier = [
            i for i in range(validate_index)
            if steps[i].operation == "compose_instructions"
        ]
        if earlier:
            compose_index = earlier[-1]
        elif refine_index is not None and refine_index > validate_index:
            problems.append(
                f"no compose_instructions step before validate_output "
                f"{steps[validate_index].id!r}; refine cannot send issues back"
            )
    for index in emit_at:
        if validate_index is not None and index < validate_index:
            problems.append(
                f"emit step {steps[index].id!r} is before validate_output "
                f"{steps[validate_index].id!r}; a failed validation must not already have emitted"
            )
        if refine_index is not None and index < refine_index:
            problems.append(
                f"emit step {steps[index].id!r} is before refine step "
                f"{steps[refine_index].id!r}; a failed round would emit on the way back"
            )
    if problems:
        raise ProcedureRunError("\n".join(problems))
    limit = _round_limit(steps[refine_index]) if refine_index is not None else 0
    return _Shape(refine_index, compose_index, limit)


def _at(steps: tuple[Step, ...], operation: str) -> list[int]:
    return [index for index, step in enumerate(steps) if step.operation == operation]


def _round_limit(step: Step) -> int:
    value = step.max_rounds
    if value is None:
        return DEFAULT_MAX_ROUNDS
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProcedureRunError(
            f"refine step {step.id!r} max_rounds must be a non-negative integer "
            f"or omitted (got {value!r})",
            step.id,
        )
    return value


def _execute(steps, index: int, operations, state: _State, shape: _Shape) -> bool:
    step = steps[index]
    failing = _failing_guard(step, state.outputs)
    if failing is not None:
        _skip(step, state, failing)
        if state.pending is not None and index == shape.compose_index:
            raise ProcedureRunError(
                f"step {step.id!r} was skipped, so refine cannot send issues back to it",
                step.id,
            )
        return False
    inputs = _resolve_inputs(step, state.outputs)
    if state.pending is not None and index == shape.compose_index:
        inputs["issues"] = copy.deepcopy(state.pending)
        state.pending = None
    result = operations(step.operation, copy.deepcopy(inputs))
    stored = _outputs(step, result)
    state.outputs[step.id] = stored
    state.events.append(Event(
        kind="ran",
        step_id=step.id,
        operation=step.operation,
        iteration=state.iteration(),
    ))
    if step.operation == "validate_output":
        state.last_draft = copy.deepcopy(inputs["draft"])
        state.passed = stored["passed"]
    if step.operation == "emit":
        state.artifact = stored["artifact"]
    return True


def _skip(step: Step, state: _State, guard: Guard) -> None:
    state.outputs.pop(step.id, None)
    state.events.append(Event(
        kind="skipped",
        step_id=step.id,
        operation=step.operation,
        iteration=state.iteration(),
        reason="guard",
        guard_ref=guard.ref,
        guard_op=guard.op,
    ))


def _outputs(step: Step, result: Any) -> dict[str, Any]:
    operation = OPERATIONS.get(step.operation)
    expected = operation.outputs if operation is not None else ()
    if not isinstance(result, Mapping):
        raise ProcedureRunError(
            f"step {step.id!r} operation {step.operation} must give {expected} "
            f"(got {type(result).__name__})",
            step.id,
        )
    if set(result) != set(expected):
        raise ProcedureRunError(
            f"step {step.id!r} operation {step.operation} must give {expected} "
            f"(got {sorted(result)})",
            step.id,
        )
    if step.operation == "validate_output" and not isinstance(result.get("passed"), bool):
        raise ProcedureRunError(
            f"step {step.id!r} validate_output gave passed={result.get('passed')!r}, "
            f"which is not a boolean",
            step.id,
        )
    try:
        return copy.deepcopy({name: result[name] for name in expected})
    except Exception as exc:
        raise ProcedureRunError(
            f"step {step.id!r} outputs could not be copied ({type(exc).__name__}: {exc})",
            step.id,
        ) from exc


def _resolve_inputs(step: Step, outputs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for name, value in step.inputs.items():
        if isinstance(value, str) and ".outputs." in value:
            found, produced = _lookup(value, outputs)
            if not found:
                raise ProcedureRunError(
                    f"step {step.id!r} input {name!r} refers to {value!r} "
                    f"and that output is not available",
                    step.id,
                )
            resolved[name] = produced
        else:
            resolved[name] = copy.deepcopy(value)
    return resolved


def _failing_guard(step: Step, outputs: Mapping[str, Mapping[str, Any]]) -> Guard | None:
    failed: Guard | None = None
    for guard in step.guards:
        found, actual = _lookup(guard.ref, outputs)
        if not found:
            raise ProcedureRunError(
                f"step {step.id!r} guard ref {guard.ref!r} is not available",
                step.id,
            )
        if not _holds(step, guard, actual) and failed is None:
            failed = guard
    return failed


def _lookup(ref: Any, outputs: Mapping[str, Mapping[str, Any]]) -> tuple[bool, Any]:
    if not isinstance(ref, str) or ".outputs." not in ref:
        return False, None
    source, _sep, name = ref.partition(".outputs.")
    slot = outputs.get(source)
    if source == "" or name == "" or not isinstance(slot, Mapping) or name not in slot:
        return False, None
    return True, copy.deepcopy(slot[name])


def _holds(step: Step, guard: Guard, actual: Any) -> bool:
    op = guard.op
    value = guard.value
    if op == "eq":
        return _equal(actual, value)
    if op == "ne":
        return not _equal(actual, value)
    if op == "empty":
        return _is_empty(actual)
    if op == "not_empty":
        return not _is_empty(actual)
    if op in ("gt", "lt"):
        if not _ordered(actual, value):
            raise ProcedureRunError(
                f"step {step.id!r} guard {guard.ref!r} {op} cannot compare "
                f"{actual!r} and {value!r}",
                step.id,
            )
        return actual > value if op == "gt" else actual < value
    raise ProcedureRunError(
        f"step {step.id!r} guard op {op!r} is not one of eq, ne, gt, lt, empty, not_empty",
        step.id,
    )


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    return left == right


def _ordered(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return True
    return isinstance(left, str) and isinstance(right, str)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, _EMPTY_TYPES):
        return len(value) == 0
    return False


def _default_stub(operation: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
    if operation == "select_requirements":
        return {"requirements": copy.deepcopy(inputs["scope"])}
    if operation == "fetch_context":
        return {"context": {"requirements": copy.deepcopy(inputs["requirements"])}}
    if operation == "choose_template":
        template_id = inputs.get("template_id", "default")
        return {"template": {"id": copy.deepcopy(template_id)}}
    if operation == "compose_instructions":
        payload = {
            "requirements": inputs["requirements"],
            "context": inputs["context"],
            "template": inputs["template"],
        }
        if "issues" in inputs:
            payload["issues"] = inputs["issues"]
        return {"instructions": json.dumps(payload, sort_keys=True)}
    if operation == "invoke_model":
        return {"draft": {"instructions": copy.deepcopy(inputs["instructions"])}}
    if operation == "validate_output":
        return {"issues": [], "passed": True}
    if operation == "refine":
        return {"issues": copy.deepcopy(inputs["issues"])}
    if operation == "emit":
        return {"artifact": {
            "draft": copy.deepcopy(inputs["draft"]),
            "template": copy.deepcopy(inputs["template"]),
            "passed": copy.deepcopy(inputs["passed"]),
        }}
    raise ProcedureRunError(f"operation {operation!r} is not one of the eight")
