"""choose_template, compose_instructions and emit (P-15).

Template rendering from §5 and §5a of docs/spec-procedure-runtime.md.
No model. A template is a Markdown file under ``templates/test-spec/``.
``default.md`` ships with the runtime.

The stored artifact is the specification structure. The Markdown written
beside it is the rendering. The version is the sha256 of the canonical
JSON of that structure.

Judgements this card had to make, and the tests lock in:

- A graph ``Template`` is a name for a file. ``uses_template`` is reached
  from a selected requirement, or from one of its statements, by an
  incoming ``verifies`` from a ``TestConcept`` or ``TestCase``.
  ``traces_to`` and ``refers_to`` are not followed. Two templates is an
  error. A native id on more than one node is an error until the caller
  names a node key.
- ``{{name}}`` is checked at load. Outside ``{{#cases}}`` the fields are
  ``title`` and ``level``. Inside, the §5a case fields plus ``level``.
  ``checks`` is stored and is not a placeholder. The block is the exact
  text between the markers. One block, no nesting.
- ``level`` on the specification and on every case is the template
  header. A level on the draft is replaced. Fields outside §5 are not
  stored. ``checks`` (§5b) is stored as given, including ``[]``. It is
  not a template placeholder. ``no_check_reason`` is stored when the case
  carries a string. Null is absent. A line whose only placeholder is
  ``{{variant}}`` is omitted when the case has no variant or the variant
  is ``""``. An empty variant is not stored.
- An item's own text is the engine text after the ``native_id`` line.
  The line is how the projection stores the id, not part of the text.
- ``passed`` must be ``True``. Otherwise nothing is written.
- Nothing to compose prints ``instructions=n/a``. An emit with no cases
  prints ``artifact=n/a``. Documents are not on the draft, so emit prints
  ``n_docs=n/a``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FIXED_TEXT = (
    "Fill a test specification as JSON. Use only the text in this message. "
    "Do not invent an identifier. The template sets level; do not set it.\n"
    "The JSON object has title and cases. Each case has id, verifies, "
    "preconditions, stimulus, expected_observable_result, pass_criterion, "
    "kind (positive or negative), uses, variant, and checks. "
    "An empty variant means no variant. "
    "checks is required and may be empty. "
    "When a requirement names DBC Signals, at least one case verifying it must have a check. "
    "If no check can be written, leave checks empty and set no_check_reason to inside_ecu, "
    "signal_compare or not_on_bus. "
    "Never write a check you cannot ground in the case's uses. "
    "signal_compare is only for an expected result that relates two signal values to each other, "
    "such as A equals B or A stays within 5 of B. "
    "A cause on one signal and its effect on another is not signal_compare: "
    "put the cause in when and the effect in expect. "
    "not_on_bus is for a cause or result that no DBC Signal carries, such as oncoming traffic. "
    "inside_ecu is for a result visible only inside the ECU. "
    "Every condition the requirement says must already hold "
    "(while ..., only when ..., when ... is ...) goes into given as a condition on its signal. "
    "inside_ecu is wrong when every signal the case uses is a DBC Signal. "
    "Example: the requirement says when M.A becomes 1, M.B shall become 1 within 0.2 s. "
    "The check is when M.A == 1, expect M.B == 1, mode eventually, within_ms [0, 200]. "
    "verifies and uses are lists.\n"
    "A check makes a case judgeable on a CAN recording. Write one when the "
    "expected result is a signal value that follows a cause, and both signals "
    "are DBC Signals in the case's uses. A check has exactly these keys: id "
    "(unique within the case, e.g. c1); given (conditions that must already "
    "hold, usually []); when (the condition whose change from false to true "
    "starts the window); expect (the condition judged in the window); mode "
    "(eventually: true at least once in the window; always: true throughout; "
    "never: true at no point); within_ms ([min, max] in milliseconds after "
    "when becomes true). A condition has exactly these keys: signal {message, "
    "signal, channel}, op (one of == != > >= < <=), value (a number: the "
    "signal's physical value, never a value-table label), tolerance (null, or "
    "a number >= 0 with == or != only). message and signal are the two halves "
    "of the DBC id Message.Signal. channel is null unless the requirement "
    "names a CAN channel. Write every key; use [] or null when empty. When "
    "the requirement states no time limit, use within_ms [0, 1000] and write "
    "'assumed 1 s window, not stated in the requirement' in pass_criterion.\n"
    "Example, with names that are not from this specification:\n"
    '{"id": "c1", "given": [], "when": {"signal": {"message": "Pedal", '
    '"signal": "Pressed", "channel": null}, "op": "==", "value": 1, '
    '"tolerance": null}, "expect": {"signal": {"message": "Lamp", "signal": '
    '"On", "channel": null}, "op": "==", "value": 1, "tolerance": null}, '
    '"mode": "eventually", "within_ms": [0, 100]}\n'
)

_SHIPPED_DIR = Path(__file__).resolve().parents[2] / "templates" / "test-spec"

_OPEN = "{{#cases}}"
_CLOSE = "{{/cases}}"
_FIELD = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_SPEC_FIELDS = frozenset({"title", "level"})
_CASE_FIELDS = (
    "id",
    "verifies",
    "preconditions",
    "stimulus",
    "expected_observable_result",
    "pass_criterion",
    "variant",
    "kind",
    "uses",
)
_CASE_FIELD_SET = frozenset(_CASE_FIELDS)
_HEADER_KEYS = ("id", "name", "level")
_ABSENT = object()


class TemplateError(Exception):
    """The template, the draft, or the selection cannot be used. Nothing was stored."""


class TemplateOperations:
    """The three template operations, as a callable the interpreter can run.

    Any other operation, including ``invoke_model``, is refused.
    """

    def __init__(self, kg, projection, store, templates_dir=None):
        self.kg = kg
        self.projection = projection
        self.store = store
        self.templates_dir = templates_dir

    def __call__(self, operation: str, inputs: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "choose_template":
            return {"template": choose_template(
                inputs["requirements"],
                inputs.get("template_id"),
                projection=self.projection,
                templates_dir=self.templates_dir,
            )}
        if operation == "compose_instructions":
            issues = inputs["issues"] if "issues" in inputs else _ABSENT
            return {"instructions": compose_instructions(
                self.kg,
                self.projection,
                inputs["requirements"],
                inputs["context"],
                inputs["template"],
                issues,
            )}
        if operation == "emit":
            return {"artifact": emit(
                inputs["draft"],
                inputs["template"],
                inputs["passed"],
                store=self.store,
            )}
        raise TemplateError(
            f"operation {operation!r} is not one of "
            f"choose_template, compose_instructions or emit"
        )


def load_template(path: str | Path) -> dict:
    """Load one template file. A bad placeholder is a load error."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    header, body = _split_header(text, path.name)
    _parse_body(body)
    return {
        "id": header["id"],
        "name": header["name"],
        "level": header["level"],
        "body": body,
        "text": text,
        "source": str(path),
    }


def render(template: Mapping[str, Any], specification: Mapping[str, Any]) -> str:
    """Replace placeholders. Lists join with ``, ``. The block repeats per case."""
    prefix, inner, suffix = _parse_body(template["body"])
    rendered = _fill(prefix, specification, _SPEC_FIELDS)
    for case in specification.get("cases") or []:
        rendered += _fill(inner, case, _CASE_FIELD_SET)
    rendered += _fill(suffix, specification, _SPEC_FIELDS)
    return rendered


def choose_template(
    requirements: Any,
    template_id: Any = None,
    *,
    projection: Mapping[str, Any] | None = None,
    templates_dir: str | Path | None = None,
) -> dict:
    """The named template file, or the one linked by ``uses_template``.

    None found is an error. A named id is the file id, a Template node
    key, or a Template ``native_id``.
    """
    records = _requirement_records(requirements)
    directory = _SHIPPED_DIR if templates_dir is None else Path(templates_dir)
    if template_id is None or template_id == "":
        if not isinstance(projection, Mapping):
            raise TemplateError(
                "choosing a template by uses_template needs a projection"
            )
        template_id = _linked_template_id(records, projection)
    template = _resolve_named(template_id, projection, directory)
    _print_template(template, records)
    return template


def compose_instructions(
    kg,
    projection: Mapping[str, Any],
    requirements: Any,
    context: Any,
    template: Mapping[str, Any],
    issues: Any = _ABSENT,
) -> str:
    """Fixed text plus each item's own text. No model."""
    records = _requirement_records(requirements)
    groups = _context_groups(context)
    by_key = {node["key"]: node for node in projection["nodes"]}
    requirement_rows: list[str] = []
    requirement_docs: set[str] = set()
    for record in records:
        node = _node(by_key, record.get("key"), "requirement")
        if isinstance(record.get("source_id"), str) and record["source_id"]:
            requirement_docs.add(record["source_id"])
        requirement_rows.append(_label(record, node))
        requirement_rows.append(_own_text(kg, node))
    context_rows: list[str] = []
    context_docs: set[str] = set()
    n_context = 0
    for group in groups:
        context_rows.append(
            f"{group.get('item_id', '')} ({group.get('source_id', '')})"
        )
        for key in group["nodes"]:
            node = _node(by_key, key, "context node")
            n_context += 1
            if isinstance(node.get("source_id"), str) and node["source_id"]:
                context_docs.add(node["source_id"])
            context_rows.append(f"{node['native_id']} ({node['source_id']})")
            context_rows.append(_own_text(kg, node))
    text = (
        FIXED_TEXT
        + _section(
            f"Requirements (n={len(records)}, documents={len(requirement_docs)}):",
            requirement_rows,
        )
        + _section(
            f"Context (n={n_context}, documents={len(context_docs)}):",
            context_rows,
        )
        + "Template:\n"
        + template["text"]
    )
    if issues is not _ABSENT:
        if not isinstance(issues, list):
            raise TemplateError("issues must be a list")
        rendered = json.dumps(issues, ensure_ascii=False, sort_keys=True, indent=2)
        if not text.endswith("\n"):
            text += "\n"
        text += f"Issues (n={len(issues)}):\n{rendered}\n"
    n_docs = len(requirement_docs | context_docs)
    if len(records) == 0:
        print(
            f"instructions=n/a n_requirements=0 n_context={n_context} n_docs={n_docs}"
        )
    else:
        print(
            f"instructions n_requirements={len(records)} "
            f"n_context={n_context} n_docs={n_docs}"
        )
    return text


def emit(draft: Any, template: Mapping[str, Any], passed: Any, *, store: str | Path) -> dict:
    """Store the specification as a version and render it through the template.

    ``passed`` must be ``True``. The version file is the structure. The
    Markdown file is the rendering and is not part of the hash.
    """
    if passed is not True:
        raise TemplateError("passed is not true; nothing was stored")
    specification = _specification(draft, template)
    rendering = render(template, specification)
    canonical = json.dumps(
        specification, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    directory = Path(store)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{version}.json").write_text(canonical + "\n", encoding="utf-8")
    (directory / f"{version}.md").write_text(rendering, encoding="utf-8")
    n_cases = len(specification["cases"])
    if n_cases == 0:
        print(f"artifact=n/a version={version} n_cases=0 n_docs=n/a")
    else:
        print(f"artifact version={version} n_cases={n_cases} n_docs=n/a")
    return {
        "version": version,
        "specification": specification,
        "rendering": rendering,
    }


def _specification(draft: Any, template: Mapping[str, Any]) -> dict:
    if isinstance(draft, list):
        cases_in = draft
        title = template["name"]
    elif isinstance(draft, Mapping) and "cases" in draft:
        cases_in = draft["cases"]
        raw_title = draft.get("title")
        title = raw_title if isinstance(raw_title, str) and raw_title != "" else template["name"]
    else:
        raise TemplateError("draft must be a list of cases or a mapping with cases")
    if not isinstance(cases_in, list):
        raise TemplateError("draft cases must be a list")
    cases = []
    for case in cases_in:
        if not isinstance(case, Mapping):
            raise TemplateError("case is not a mapping")
        stored: dict[str, Any] = {}
        for field in _CASE_FIELDS:
            if field not in case or case[field] is None:
                continue
            if field == "variant" and case[field] == "":
                continue
            stored[field] = case[field]
        if "checks" in case:
            stored["checks"] = case["checks"]
        reason = case.get("no_check_reason")
        if isinstance(reason, str):
            stored["no_check_reason"] = reason
        stored["level"] = template["level"]
        cases.append(stored)
    return {"title": title, "level": template["level"], "cases": cases}


def _requirement_records(requirements: Any) -> list:
    if not isinstance(requirements, list):
        raise TemplateError("requirements must be a list of requirement records")
    for record in requirements:
        if not isinstance(record, Mapping):
            raise TemplateError("requirements entry must be a mapping")
    return list(requirements)


def _context_groups(context: Any) -> list:
    if not isinstance(context, Mapping) or not isinstance(context.get("requirements"), list):
        raise TemplateError(
            "context must be the fetch_context result, grouped by requirement"
        )
    groups = []
    for group in context["requirements"]:
        if not isinstance(group, Mapping) or not isinstance(group.get("nodes"), list):
            raise TemplateError("context group must list nodes")
        groups.append(group)
    return groups


def _linked_template_id(requirements: list, projection: Mapping[str, Any]) -> str:
    edges = list(projection.get("edges") or [])
    nodes = list(projection.get("nodes") or [])
    by_key = {node["key"]: node for node in nodes if isinstance(node, Mapping)}
    requirement_keys: set[str] = set()
    for record in requirements:
        key = record.get("key")
        if not isinstance(key, str) or key == "":
            raise TemplateError("requirements entry must name a requirement key")
        requirement_keys.add(key)
    statement_keys = {
        edge["to_key"]
        for edge in edges
        if edge.get("relation") == "has_statement"
        and edge.get("from_key") in requirement_keys
        and edge.get("to_type") == "Statement"
    }
    covered = requirement_keys | statement_keys
    verifiers: list[str] = []
    seen: set[str] = set()
    for edge in edges:
        if edge.get("relation") != "verifies" or edge.get("to_key") not in covered:
            continue
        if edge.get("from_type") not in ("TestConcept", "TestCase"):
            continue
        key = edge.get("from_key")
        if isinstance(key, str) and key not in seen:
            seen.add(key)
            verifiers.append(key)
    verifier_keys = set(verifiers)
    found: list[str] = []
    seen_ids: set[str] = set()
    for edge in edges:
        if edge.get("relation") != "uses_template" or edge.get("from_key") not in verifier_keys:
            continue
        if edge.get("to_type") != "Template":
            continue
        node = by_key.get(edge.get("to_key"))
        if node is None or node.get("type") != "Template":
            raise TemplateError(
                f"uses_template target {edge.get('to_key')!r} is not a Template node"
            )
        native = node.get("native_id")
        if not isinstance(native, str) or native == "":
            raise TemplateError(
                f"template {node.get('key')!r} has no native id"
            )
        if native not in seen_ids:
            seen_ids.add(native)
            found.append(native)
    if not found:
        raise TemplateError(
            f"no template linked by uses_template (n_requirements={len(requirements)})"
        )
    if len(found) > 1:
        names = ", ".join(sorted(found))
        raise TemplateError(
            f"requirements link {len(found)} templates ({names}); name a template_id"
        )
    return found[0]


def _resolve_named(template_id: Any, projection: Mapping[str, Any] | None, directory: Path) -> dict:
    if not isinstance(template_id, str) or template_id == "":
        raise TemplateError(f"template id {template_id!r} matches nothing")
    by_id = _index(directory)
    if template_id in by_id:
        return by_id[template_id]
    nodes = _template_nodes(projection)
    by_key = [node for node in nodes if node.get("key") == template_id]
    if len(by_key) == 1:
        return _file_for_node(by_key[0], by_id)
    matches = [node for node in nodes if node.get("native_id") == template_id]
    if len(matches) > 1:
        keys = ", ".join(sorted(str(node.get("key")) for node in matches))
        raise TemplateError(
            f"template id {template_id!r} matches {len(matches)} nodes ({keys}); name a node key"
        )
    if len(matches) == 1:
        return _file_for_node(matches[0], by_id)
    raise TemplateError(f"template id {template_id!r} matches nothing")


def _template_nodes(projection: Mapping[str, Any] | None) -> list:
    if not isinstance(projection, Mapping):
        return []
    return [
        node for node in projection.get("nodes") or []
        if isinstance(node, Mapping) and node.get("type") == "Template"
    ]


def _file_for_node(node: Mapping[str, Any], by_id: Mapping[str, dict]) -> dict:
    native = node.get("native_id")
    found = by_id.get(native) if isinstance(native, str) else None
    if found is None:
        raise TemplateError(
            f"template {native!r} matches a node but no file has that id"
        )
    return found


def _index(directory: Path) -> dict[str, dict]:
    if not directory.is_dir():
        raise TemplateError(f"template directory {directory} does not exist")
    by_id: dict[str, dict] = {}
    for path in sorted(directory.glob("*.md")):
        template = load_template(path)
        previous = by_id.get(template["id"])
        if previous is not None:
            raise TemplateError(
                f"template id {template['id']!r} matches 2 files "
                f"({previous['source']}, {template['source']})"
            )
        by_id[template["id"]] = template
    return by_id


def _print_template(template: Mapping[str, Any], requirements: list) -> None:
    documents = {
        record["source_id"]
        for record in requirements
        if isinstance(record.get("source_id"), str) and record["source_id"]
    }
    print(
        f"template id={template['id']} level={template['level']} "
        f"n_requirements={len(requirements)} n_docs={len(documents)}"
    )


def _node(by_key: Mapping[str, Mapping[str, Any]], key: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(key, str) or key not in by_key:
        raise TemplateError(f"{what} {key!r} matches nothing")
    return by_key[key]


def _label(record: Mapping[str, Any], node: Mapping[str, Any]) -> str:
    item_id = record["item_id"] if isinstance(record.get("item_id"), str) else node["native_id"]
    source_id = record["source_id"] if isinstance(record.get("source_id"), str) else node["source_id"]
    return f"{item_id} ({source_id})"


def _own_text(kg, node: Mapping[str, Any]) -> str:
    """The item's own text. The projection's leading native-id line is not part of it."""
    raw = kg.get_node_text(node["engine_id"]) or ""
    prefix = f"{node['native_id']}\n"
    if raw.startswith(prefix):
        return raw[len(prefix):]
    return raw


def _section(heading: str, rows: list[str]) -> str:
    body = "\n".join(rows)
    if body:
        body += "\n"
    return heading + "\n" + body + "\n"


def _split_header(text: str, name: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise TemplateError(f"template file {name} has no header")
    rest = text[4:]
    fence = rest.find("\n---\n")
    if fence < 0:
        raise TemplateError(f"template file {name} has no header")
    header = _header_fields(rest[:fence], name)
    body = rest[fence + len("\n---\n"):]
    return header, body


def _header_fields(header: str, name: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in header.split("\n"):
        if line == "":
            continue
        if ":" not in line:
            raise TemplateError(f"template file {name} header line {line!r} is not key: value")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in _HEADER_KEYS:
            raise TemplateError(f"template header has unknown key {key!r}")
        if key in fields:
            raise TemplateError(f"template header repeats {key}")
        if value == "":
            raise TemplateError(f"template header lacks {key}")
        fields[key] = value
    for key in _HEADER_KEYS:
        if key not in fields:
            raise TemplateError(f"template header lacks {key}")
    return fields


def _parse_body(body: str) -> tuple[str, str, str]:
    opens: list[int] = []
    closes: list[int] = []
    index = 0
    while True:
        start = body.find("{{", index)
        if start < 0:
            break
        end = body.find("}}", start + 2)
        if end < 0:
            raise TemplateError("template placeholder is not closed")
        token = body[start:end + 2]
        inner = body[start + 2:end]
        if token == _OPEN:
            opens.append(start)
        elif token == _CLOSE:
            closes.append(start)
        elif not _NAME.fullmatch(inner):
            raise TemplateError(f"placeholder {token} names no field")
        index = end + 2
    if not opens:
        raise TemplateError("template has no {{#cases}} block")
    if len(opens) > 1 or len(closes) > 1:
        if len(opens) >= 2 and (not closes or opens[1] < closes[0]):
            raise TemplateError("template block {{#cases}} is nested")
        raise TemplateError("template has more than one {{#cases}} block")
    if len(closes) != 1 or closes[0] < opens[0]:
        raise TemplateError("template block {{#cases}} is not closed")
    prefix = body[:opens[0]]
    inner = body[opens[0] + len(_OPEN):closes[0]]
    suffix = body[closes[0] + len(_CLOSE):]
    _check_fields(prefix, _SPEC_FIELDS, "specification")
    _check_fields(suffix, _SPEC_FIELDS, "specification")
    _check_fields(inner, _CASE_FIELD_SET, "test case")
    return prefix, inner, suffix


def _check_fields(text: str, allowed: frozenset, where: str) -> None:
    index = 0
    while True:
        start = text.find("{{", index)
        if start < 0:
            return
        end = text.find("}}", start + 2)
        if end < 0:
            raise TemplateError("template placeholder is not closed")
        token = text[start:end + 2]
        name = text[start + 2:end]
        if not _NAME.fullmatch(name) or name not in allowed:
            raise TemplateError(f"placeholder {token} names no field of the {where}")
        index = end + 2


def _fill(text: str, record: Mapping[str, Any], allowed: frozenset) -> str:
    if _variant_absent(record):
        text = _without_variant_lines(text)

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in allowed:
            raise TemplateError(f"placeholder {{{{{name}}}}} names no field")
        value = record[name] if isinstance(record, Mapping) and name in record else None
        return _render_value(name, value)

    return _FIELD.sub(replace, text)


def _variant_absent(record: Mapping[str, Any]) -> bool:
    """``""`` is no variant. A missing variant is the same."""
    if not isinstance(record, Mapping) or "variant" not in record:
        return True
    return record.get("variant") == "" or record.get("variant") is None


def _without_variant_lines(text: str) -> str:
    """Drop a line whose only placeholder is ``{{variant}}``."""
    kept = []
    for line in text.split("\n"):
        if "{{variant}}" in line and "{{" not in line.replace("{{variant}}", ""):
            continue
        kept.append(line)
    return "\n".join(kept)


def _render_value(name: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                raise TemplateError(f"case field {name} must be text or a list of text")
        return ", ".join(value)
    raise TemplateError(f"case field {name} must be text or a list of text")
