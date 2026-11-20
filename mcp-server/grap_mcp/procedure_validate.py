"""validate_output (P-13).

The six checks of §7 of docs/spec-procedure-runtime.md. No model. Each
failure is an issue naming the case, the check and the offending value.
Coverage and negative cases name no case; the value is the requirement key.

Judgements this card had to make, and the tests lock in:

- A ``verifies`` entry covers a selected requirement when it equals that
  requirement's key, its item id, or a statement id of one of its
  statements. One item id covers every selected requirement that carries
  it. A key covers that node only.
- ``uses`` holds context node keys of the requirements the case verifies.
  A verifies entry matches a context group when it equals that group's
  requirement key or its item id. A use outside that union is not grounded.
  A case whose verifies matches no group has an empty union. A native id
  in ``uses`` is not grounded.
- Identifiers are the R-09 rule, read from ``working_graph``. Inside a
  sentence only a bracketed id, an underscore, a leading slash or a
  CamelCase hump counts. A field with no whitespace uses the whole-name
  rule, so a bare number is an identifier. Edge punctuation is not part
  of the identifier. The text is matched exactly, brackets included,
  against a context node's key or, when a projection is supplied, that
  node's native id.
- The scanned text is ``preconditions``, ``stimulus``,
  ``expected_observable_result``, ``pass_criterion`` and ``variant``.
  An empty ``variant`` is no variant and is not scanned.
- A negative case counts for a requirement only when that case verifies
  it. ``error_handling`` is the statement kind. An exception is a
  non-empty ``exception``. An explicit ``statements`` list is the whole
  set, including an empty one. Otherwise kinds come from ``has_statement``
  edges. A projection node does not carry an exception span.
- An empty ``verifies`` is a traceability failure. It also fails coverage
  when a requirement is selected. The fixture that fails only
  traceability still verifies the selection and names one id outside it.
- Completeness is a missing or blank ``stimulus``,
  ``expected_observable_result`` or ``pass_criterion``.
- A case's ``checks`` may be missing or ``[]``. Each check is judged
  on its own. For every condition in ``given``, ``when`` and
  ``expect``, some key in that case's ``uses`` must be a projection
  node of type ``Signal`` whose ``native_id`` is exactly
  ``message.signal``. ``op`` is ``== != > >= < <=``. ``mode`` is
  ``eventually always never``. ``within_ms`` is two integers,
  ``0 <= min <= max <= 3600000``. ``tolerance`` null is absent; a
  number must be ``>= 0`` and only on ``==`` or ``!=``. ``channel``
  null is absent; otherwise it is an integer from 0 to 255. Check
  ids are unique within the case. ``value`` is a number or a string,
  and is not compared with a DBC label. A check that is not an object
  is the rule ``check shape`` and the value ``#<index>``. One failure
  is one issue: ``check`` is the rule, ``value`` is the check id.
  The summary's ``checks`` line counts these issues over ``n_cases``.
  A check whose ``within_ms`` is exactly ``[0, 1000]`` while the case's
  ``pass_criterion`` does not contain ``assumed 1 s window`` is the rule
  ``assumed_window``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from grap_mcp.kee.working_graph import (
    _BRACKETED_ID,
    _name_is_identifier,
    _token_is_strong_identifier,
)

_CHECKS = (
    "coverage",
    "grounding",
    "no_invented_names",
    "traceability",
    "completeness",
    "negative_cases",
)
_TEXT_FIELDS = (
    "preconditions",
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
    "variant",
)
_REQUIRED_TEXT = (
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
)
_EDGE_PUNCT = ".,;:!?()\"'`<>"
_CHECK_OPS = ("==", "!=", ">", ">=", "<", "<=")
_CHECK_EQUALITY = ("==", "!=")
_CHECK_MODES = ("eventually", "always", "never")
# §3.2 writes this upper bound as 3 600 000. Channel is 0 to 255.
_CHECK_WINDOW_MAX_MS = 3600000
_CHECK_CHANNEL_MAX = 255
_CHECK_RULES = frozenset({
    "check signal not in uses",
    "op",
    "mode",
    "within_ms",
    "tolerance",
    "channel",
    "check id",
    "value",
    "check shape",
    "assumed_window",
})


class ValidationError(Exception):
    """The draft, the requirements or the context is not a shape §7 can check."""


class ValidationOperations:
    """``validate_output`` as a callable the interpreter can run.

    Any other operation, including ``invoke_model``, is refused. The
    denominators are printed. The return value is exactly the operation's
    two outputs.
    """

    def __init__(self, projection=None, statements=None):
        self.projection = projection
        self.statements = statements

    def __call__(self, operation: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        if operation != "validate_output":
            raise ValidationError(f"operation {operation!r} is not validate_output")
        result = validate_output(
            inputs["draft"],
            inputs["requirements"],
            inputs["context"],
            projection=self.projection,
            statements=self.statements,
        )
        print(format_summary(
            inputs["draft"],
            inputs["requirements"],
            inputs["context"],
            result,
            projection=self.projection,
            statements=self.statements,
        ))
        return result


def validate_output(
    draft,
    requirements,
    context,
    *,
    projection=None,
    statements=None,
) -> dict[str, Any]:
    """Run the §7 checks and the §5b check rules.

    ``passed`` is true only when ``issues`` is empty.
    """
    cases = _cases(draft)
    reqs = _requirements(requirements)
    groups = _context_groups(context)
    rows = _statement_rows(reqs, projection, statements)
    attached = _attach(reqs, rows)
    names = _known_names(groups, projection)
    issues: list[dict] = []
    issues.extend(_coverage(reqs, cases, attached))
    issues.extend(_grounding(cases, context))
    issues.extend(_invented(cases, names))
    issues.extend(_traceability(reqs, cases, attached))
    issues.extend(_completeness(cases))
    issues.extend(_negative(reqs, cases, attached))
    issues.extend(_case_checks(cases, projection))
    return {"issues": issues, "passed": not issues}


def format_summary(
    draft,
    requirements,
    context,
    result: Mapping[str, Any],
    *,
    projection=None,
    statements=None,
) -> str:
    """One line of denominators, then one line per check. Nothing to measure is ``n/a``."""
    cases = _cases(draft)
    reqs = _requirements(requirements)
    _context_groups(context)
    n_cases = len(cases)
    n_requirements = len(reqs)
    n_docs = len({req["source_id"] for req in reqs})
    n_statements = len(_statement_rows(reqs, projection, statements))
    counts = {name: 0 for name in _CHECKS}
    n_check_issues = 0
    for issue in result["issues"]:
        check = issue.get("check")
        if check in counts:
            counts[check] += 1
        if check in _CHECK_RULES:
            n_check_issues += 1
    passed = "true" if result.get("passed") is True else "false"
    lines = [
        f"passed={passed} n_issues={len(result['issues'])} n_cases={n_cases} "
        f"n_requirements={n_requirements} n_docs={n_docs} n_statements={n_statements}",
        _check_line("coverage", counts["coverage"], n_requirements, "requirements", n_docs),
        _check_line("grounding", counts["grounding"], n_cases, "cases", n_docs),
        _check_line(
            "no_invented_names", counts["no_invented_names"], n_cases, "cases", n_docs,
        ),
        _check_line("traceability", counts["traceability"], n_cases, "cases", n_docs),
        _check_line("completeness", counts["completeness"], n_cases, "cases", n_docs),
        _negative_line(counts["negative_cases"], n_requirements, n_statements, n_docs),
        _check_line("checks", n_check_issues, n_cases, "cases", n_docs),
    ]
    return "\n".join(lines)


def report_validation(
    draft,
    requirements,
    context,
    *,
    projection=None,
    statements=None,
) -> dict[str, Any]:
    """Validate and print the denominators."""
    result = validate_output(
        draft, requirements, context, projection=projection, statements=statements,
    )
    print(format_summary(
        draft, requirements, context, result,
        projection=projection, statements=statements,
    ))
    return result


def _check_line(name: str, count: int, total: int, unit: str, documents: int) -> str:
    shown = "n/a" if total == 0 else str(count)
    return f"{name}={shown} (n={total} {unit}, documents={documents})"


def _negative_line(count: int, requirements: int, statements: int, documents: int) -> str:
    shown = "n/a" if requirements == 0 else str(count)
    return (
        f"negative_cases={shown} "
        f"(n={requirements} requirements, n_statements={statements}, documents={documents})"
    )


def _cases(draft: Any) -> list:
    if isinstance(draft, list):
        cases = draft
    elif isinstance(draft, Mapping) and "cases" in draft:
        cases = draft["cases"]
        if isinstance(cases, (str, bytes)) or not isinstance(cases, list):
            raise ValidationError("draft cases must be a list")
    else:
        raise ValidationError(
            "draft must be a list of test cases or a mapping with cases"
        )
    for index, case in enumerate(cases):
        if not isinstance(case, Mapping):
            raise ValidationError(f"draft case {index} is not a mapping")
    return list(cases)


def _requirements(requirements: Any) -> list:
    if isinstance(requirements, (str, bytes)) or not isinstance(requirements, list):
        raise ValidationError("requirements must be a list of requirement records")
    for index, req in enumerate(requirements):
        if not isinstance(req, Mapping):
            raise ValidationError(f"requirements entry {index} is not a mapping")
        for field in ("key", "source_id", "item_id"):
            value = req.get(field)
            if not isinstance(value, str) or value == "":
                raise ValidationError(
                    f"requirements entry {index} lacks {field}"
                )
    return list(requirements)


def _context_groups(context: Any) -> list[list]:
    if not isinstance(context, Mapping) or not isinstance(context.get("requirements"), list):
        raise ValidationError("context must be a mapping with a requirements list")
    groups = []
    for index, group in enumerate(context["requirements"]):
        if not isinstance(group, Mapping) or not isinstance(group.get("nodes"), list):
            raise ValidationError(f"context group {index} must list its nodes")
        groups.append(list(group["nodes"]))
    return groups


def _statement_rows(requirements: list, projection, statements) -> list[dict]:
    if statements is not None:
        if isinstance(statements, (str, bytes)) or not isinstance(statements, list):
            raise ValidationError("statements must be a list")
        return [_normalize_statement(item) for item in statements]
    return _projection_statements(requirements, projection)


def _projection_statements(requirements: list, projection) -> list[dict]:
    if not isinstance(projection, Mapping):
        return []
    keys = {req["key"] for req in requirements}
    nodes = {
        node["key"]: node
        for node in projection.get("nodes", ())
        if isinstance(node, Mapping) and isinstance(node.get("key"), str)
    }
    found = []
    for edge in projection.get("edges", ()):
        if not isinstance(edge, Mapping) or edge.get("relation") != "has_statement":
            continue
        if edge.get("from_key") not in keys:
            continue
        node = nodes.get(edge.get("to_key"))
        if node is None:
            continue
        found.append({
            "requirement": edge.get("from_key"),
            "source_id": None,
            "item_id": None,
            "id": node.get("native_id") if isinstance(node.get("native_id"), str) else None,
            "kind": node.get("kind") if isinstance(node.get("kind"), str) else "",
            "exception": node.get("exception"),
        })
    return found


def _normalize_statement(item: Any) -> dict:
    if isinstance(item, Mapping):
        source = item
        statement_id = source.get("id")
        if not isinstance(statement_id, str) or statement_id == "":
            statement_id = source.get("statement_id")
    else:
        source = {
            "requirement": getattr(item, "requirement", None),
            "source_id": getattr(item, "source_id", None),
            "item_id": getattr(item, "item_id", None),
            "kind": getattr(item, "kind", None),
            "exception": getattr(item, "exception", None),
        }
        statement_id = getattr(item, "statement_id", None)
        if not isinstance(statement_id, str) or statement_id == "":
            statement_id = getattr(item, "id", None)
    if not isinstance(source.get("kind"), str):
        raise ValidationError("a statement must name its kind")
    if not isinstance(statement_id, str) or statement_id == "":
        statement_id = None
    return {
        "requirement": source.get("requirement"),
        "source_id": source.get("source_id"),
        "item_id": source.get("item_id"),
        "id": statement_id,
        "kind": source.get("kind"),
        "exception": source.get("exception"),
    }


def _attach(requirements: list, rows: list[dict]) -> dict[str, dict]:
    attached = {
        req["key"]: {"ids": set(), "negative": False}
        for req in requirements
    }
    for row in rows:
        for req in _owners(row, requirements):
            slot = attached[req["key"]]
            if isinstance(row.get("id"), str):
                slot["ids"].add(row["id"])
            if row.get("kind") == "error_handling" or _exception_present(row.get("exception")):
                slot["negative"] = True
    return attached


def _owners(row: Mapping[str, Any], requirements: list) -> list:
    named = row.get("requirement")
    if isinstance(named, str) and named != "":
        return [
            req for req in requirements
            if named == req["key"] or named == req["item_id"]
        ]
    source_id = row.get("source_id")
    item_id = row.get("item_id")
    if isinstance(source_id, str) and isinstance(item_id, str):
        return [
            req for req in requirements
            if req["source_id"] == source_id and req["item_id"] == item_id
        ]
    return []


def _exception_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return False
        if (
            len(value) == 2
            and all(isinstance(end, int) and not isinstance(end, bool) for end in value)
        ):
            return value[1] > value[0]
        return True
    return True


def _known_names(groups: list[list], projection) -> set[str]:
    keys = {node for group in groups for node in group if isinstance(node, str)}
    names = set(keys)
    if not isinstance(projection, Mapping):
        return names
    for node in projection.get("nodes", ()):
        if not isinstance(node, Mapping) or node.get("key") not in keys:
            continue
        native = node.get("native_id")
        if isinstance(native, str) and native != "":
            names.add(native)
    return names


def _coverage(requirements, cases, attached) -> list[dict]:
    issues = []
    for req in requirements:
        if any(_verifies(case, req, attached) for case in cases):
            continue
        issues.append(_issue(None, "coverage", req["key"]))
    return issues


def _grounding(cases, context) -> list[dict]:
    groups = context["requirements"] if isinstance(context, Mapping) else []
    issues = []
    for index, case in enumerate(cases):
        allowed = _allowed_nodes(case, groups)
        seen: list = []
        for used in _uses(case):
            if used in allowed or used in seen:
                continue
            seen.append(used)
            issues.append(_issue(_case_id(case, index), "grounding", used))
    return issues


def _allowed_nodes(case, groups) -> set:
    """Context nodes of the groups this case verifies, by key or item id."""
    allowed: set = set()
    for entry in _entries(case.get("verifies")):
        for group in groups:
            if not _verifies_group(entry, group):
                continue
            for node in group.get("nodes", ()):
                if isinstance(node, str):
                    allowed.add(node)
    return allowed


def _verifies_group(entry, group) -> bool:
    if not isinstance(entry, str) or not isinstance(group, Mapping):
        return False
    key = group.get("key")
    item_id = group.get("item_id")
    if isinstance(key, str) and entry == key:
        return True
    return isinstance(item_id, str) and entry == item_id


def _invented(cases, names: set[str]) -> list[dict]:
    issues = []
    for index, case in enumerate(cases):
        seen: list[str] = []
        for text in _texts(case):
            for identifier in _identifiers(text):
                if identifier in names or identifier in seen:
                    continue
                seen.append(identifier)
                issues.append(_issue(
                    _case_id(case, index), "no_invented_names", identifier,
                ))
    return issues


def _traceability(requirements, cases, attached) -> list[dict]:
    issues = []
    for index, case in enumerate(cases):
        entries = _entries(case.get("verifies"))
        case_id = _case_id(case, index)
        if not entries:
            issues.append(_issue(case_id, "traceability", []))
            continue
        seen: list = []
        for entry in entries:
            if _traceable(entry, requirements, attached) or entry in seen:
                continue
            seen.append(entry)
            issues.append(_issue(case_id, "traceability", entry))
    return issues


def _completeness(cases) -> list[dict]:
    issues = []
    for index, case in enumerate(cases):
        case_id = _case_id(case, index)
        for field in _REQUIRED_TEXT:
            if _blank(case.get(field)):
                issues.append(_issue(case_id, "completeness", field))
    return issues


def _case_checks(cases, projection) -> list[dict]:
    """The §5b rules. A missing ``checks`` and ``[]`` add nothing."""
    nodes = _projection_nodes(projection)
    issues = []
    for index, case in enumerate(cases):
        issues.extend(_one_case_checks(case, _case_id(case, index), nodes))
    return issues


def _projection_nodes(projection) -> dict:
    if not isinstance(projection, Mapping):
        return {}
    nodes = {}
    for node in projection.get("nodes", ()):
        if isinstance(node, Mapping) and isinstance(node.get("key"), str):
            nodes[node["key"]] = node
    return nodes


def _one_case_checks(case, case_id: str, nodes: dict) -> list[dict]:
    raw = case.get("checks")
    if not isinstance(raw, list) or len(raw) == 0:
        return []
    issues = []
    seen: list[str] = []
    for index, check in enumerate(raw):
        if not isinstance(check, Mapping):
            issues.append(_issue(case_id, "check shape", f"#{index}"))
            continue
        check_id = _check_id(check, index)
        conditions = _check_conditions(check)
        if any(not _signal_in_uses(case, nodes, item) for item in conditions):
            issues.append(_issue(case_id, "check signal not in uses", check_id))
        if any(not _op_ok(item) for item in conditions):
            issues.append(_issue(case_id, "op", check_id))
        if check.get("mode") not in _CHECK_MODES:
            issues.append(_issue(case_id, "mode", check_id))
        if not _window_ok(check.get("within_ms")):
            issues.append(_issue(case_id, "within_ms", check_id))
        if _assumed_window(check, case):
            issues.append(_issue(case_id, "assumed_window", check_id))
        if any(not _tolerance_ok(item) for item in conditions):
            issues.append(_issue(case_id, "tolerance", check_id))
        if any(not _channel_ok(item) for item in conditions):
            issues.append(_issue(case_id, "channel", check_id))
        ident = check.get("id")
        if isinstance(ident, str):
            if ident in seen:
                issues.append(_issue(case_id, "check id", check_id))
            else:
                seen.append(ident)
        if any(not _value_ok(item) for item in conditions):
            issues.append(_issue(case_id, "value", check_id))
    return issues


def _check_id(check: Mapping[str, Any], index: int) -> str:
    ident = check.get("id")
    if isinstance(ident, str) and ident.strip() != "":
        return ident
    return f"#{index}"


def _check_conditions(check: Mapping[str, Any]) -> list:
    found = []
    given = check.get("given")
    if isinstance(given, list):
        found.extend(given)
    for name in ("when", "expect"):
        item = check.get(name)
        if item is not None:
            found.append(item)
    return found


def _signal_in_uses(case, nodes: dict, condition) -> bool:
    if not isinstance(condition, Mapping):
        return False
    signal = condition.get("signal")
    if not isinstance(signal, Mapping):
        return False
    message = signal.get("message")
    name = signal.get("signal")
    if not isinstance(message, str) or not isinstance(name, str):
        return False
    native = f"{message}.{name}"
    for key in _uses(case):
        if not isinstance(key, str):
            continue
        node = nodes.get(key)
        if not isinstance(node, Mapping):
            continue
        if node.get("type") == "Signal" and node.get("native_id") == native:
            return True
    return False


def _op_ok(condition) -> bool:
    if not isinstance(condition, Mapping):
        return False
    return condition.get("op") in _CHECK_OPS


def _assumed_window(check, case) -> bool:
    """``[0, 1000]`` with no ``assumed 1 s window`` note in ``pass_criterion``."""
    window = check.get("within_ms")
    if (
        not isinstance(window, list)
        or len(window) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in window)
        or window[0] != 0
        or window[1] != 1000
    ):
        return False
    criterion = case.get("pass_criterion")
    return not (isinstance(criterion, str) and "assumed 1 s window" in criterion)


def _window_ok(value) -> bool:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        return False
    start, end = value
    return 0 <= start <= end <= _CHECK_WINDOW_MAX_MS


def _tolerance_ok(condition) -> bool:
    if not isinstance(condition, Mapping) or "tolerance" not in condition:
        return True
    tolerance = condition.get("tolerance")
    if tolerance is None:
        return True
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        return False
    if tolerance < 0:
        return False
    return condition.get("op") in _CHECK_EQUALITY


def _channel_ok(condition) -> bool:
    if not isinstance(condition, Mapping):
        return True
    signal = condition.get("signal")
    if not isinstance(signal, Mapping) or "channel" not in signal:
        return True
    channel = signal.get("channel")
    if channel is None:
        return True
    if isinstance(channel, bool) or not isinstance(channel, int):
        return False
    return 0 <= channel <= _CHECK_CHANNEL_MAX


def _value_ok(condition) -> bool:
    if not isinstance(condition, Mapping):
        return False
    value = condition.get("value")
    if isinstance(value, str):
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return True


def _negative(requirements, cases, attached) -> list[dict]:
    issues = []
    for req in requirements:
        if not attached[req["key"]]["negative"]:
            continue
        covered = any(
            case.get("kind") == "negative" and _verifies(case, req, attached)
            for case in cases
        )
        if not covered:
            issues.append(_issue(None, "negative_cases", req["key"]))
    return issues


def _verifies(case, req, attached) -> bool:
    ids = attached[req["key"]]["ids"]
    for entry in _entries(case.get("verifies")):
        if entry == req["key"] or entry == req["item_id"] or entry in ids:
            return True
    return False


def _traceable(entry, requirements, attached) -> bool:
    return any(_verifies({"verifies": [entry]}, req, attached) for req in requirements)


def _issue(case, check: str, value) -> dict:
    return {"case": case, "check": check, "value": value}


def _case_id(case: Mapping[str, Any], index: int) -> str:
    ident = case.get("id")
    if isinstance(ident, str) and ident.strip() != "":
        return ident
    return f"#{index}"


def _entries(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [] if value.strip() == "" else [value]
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        return list(value)
    return [value]


def _uses(case: Mapping[str, Any]) -> list:
    return _entries(case.get("uses"))


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        return len(value) == 0
    return False


def _texts(case: Mapping[str, Any]) -> list[str]:
    found = []
    for field in _TEXT_FIELDS:
        value = case.get(field)
        if field == "variant" and (value is None or value == ""):
            continue
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(item for item in value if isinstance(item, str))
    return found


def _identifiers(text: str) -> list[str]:
    phrase = any(ch.isspace() for ch in text)
    found: list[str] = []
    for match in re.finditer(r"\S+", text):
        core = _core(match.group(0))
        if core == "" or core in found:
            continue
        if _BRACKETED_ID.fullmatch(core):
            keep = True
        elif phrase:
            keep = _token_is_strong_identifier(core)
        else:
            keep = _name_is_identifier(core)
        if keep:
            found.append(core)
    return found


def _core(raw: str) -> str:
    start = 0
    end = len(raw)
    while start < end and raw[start] in _EDGE_PUNCT:
        start += 1
    while end > start and raw[end - 1] in _EDGE_PUNCT:
        end -= 1
    return raw[start:end]
