"""validate_output (P-13).

The six checks of §7 of docs/spec-procedure-runtime.md. No model. Each
failure is an issue naming the case, the check and the offending value.
Coverage and negative cases name no case; the value is the requirement key.

Judgements this card had to make, and the tests lock in:

- A ``verifies`` entry covers a selected requirement when it equals that
  requirement's key, its item id, or a statement id of one of its
  statements. One item id covers every selected requirement that carries
  it. A key covers that node only.
- ``uses`` holds context node keys. A native id in ``uses`` is not grounded.
- Identifiers are the R-09 rule, read from ``working_graph``. Inside a
  sentence only a bracketed id, an underscore, a leading slash or a
  CamelCase hump counts. A field with no whitespace uses the whole-name
  rule, so a bare number is an identifier. Edge punctuation is not part
  of the identifier. The text is matched exactly, brackets included,
  against a context node's key or, when a projection is supplied, that
  node's native id.
- The scanned text is ``preconditions``, ``stimulus``,
  ``expected_observable_result``, ``pass_criterion`` and ``variant``.
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
    """Run the six checks. ``passed`` is true only when ``issues`` is empty."""
    cases = _cases(draft)
    reqs = _requirements(requirements)
    groups = _context_groups(context)
    rows = _statement_rows(reqs, projection, statements)
    attached = _attach(reqs, rows)
    names = _known_names(groups, projection)
    issues: list[dict] = []
    issues.extend(_coverage(reqs, cases, attached))
    issues.extend(_grounding(cases, groups))
    issues.extend(_invented(cases, names))
    issues.extend(_traceability(reqs, cases, attached))
    issues.extend(_completeness(cases))
    issues.extend(_negative(reqs, cases, attached))
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
    for issue in result["issues"]:
        check = issue.get("check")
        if check in counts:
            counts[check] += 1
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


def _grounding(cases, groups) -> list[dict]:
    present = {node for group in groups for node in group if isinstance(node, str)}
    issues = []
    for index, case in enumerate(cases):
        seen: list = []
        for used in _uses(case):
            if used in present or used in seen:
                continue
            seen.append(used)
            issues.append(_issue(_case_id(case, index), "grounding", used))
    return issues


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
