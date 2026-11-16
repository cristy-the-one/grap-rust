"""P-15: choose_template, compose_instructions, emit.

Template rendering from §5 and §5a of docs/spec-procedure-runtime.md.
No model. The stored artifact is the specification structure; the
Markdown is the rendering of that structure. level comes from the
template header and is copied onto every case.

No automotive corpus is chosen (P-17). These tests use repo fixtures:
the shipped templates/test-spec/default.md and SourceItems built here.
"""

import hashlib
import json
import textwrap
from pathlib import Path

import pygrap
import pytest

from grap_mcp.procedure_interpreter import interpret
from grap_mcp.procedure_runtime import load_procedure
from grap_mcp.procedure_template import (
    FIXED_TEXT,
    TemplateError,
    TemplateOperations,
    choose_template,
    compose_instructions,
    emit,
    load_template,
    render,
)
from grap_mcp.readers import SourceItem
from grap_mcp.schema.project import project_items

_SHIPPED = (
    Path(__file__).resolve().parents[2] / "templates" / "test-spec" / "default.md"
)

_TINY = textwrap.dedent(
    """\
    ---
    id: tiny
    name: Tiny
    level: software
    ---
    Head {{title}}
    {{#cases}}[{{id}}|{{verifies}}|{{variant}}]{{/cases}}
    Tail
    """
)

_DEFAULT_RENDER = (
    "# Test specification: Lamp\n"
    "\n"
    "\n"
    "## TC-1 (positive)\n"
    "Verifies: REQ-1, REQ-2\n"
    "Preconditions: power on\n"
    "Stimulus: press\n"
    "Expected result: lamp off\n"
    "Pass criterion: lamp is off\n"
    "\n"
)


def _item(item_id, text, *, kind_hint="requirement", source_id="fix", attributes=None, links=()):
    return SourceItem(
        source_id=source_id,
        release=None,
        item_id=item_id,
        kind_hint=kind_hint,
        text=text,
        heading_path=[],
        attributes={} if attributes is None else dict(attributes),
        links=list(links),
        file=f"{source_id}.txt",
        sha256="0" * 64,
    )


def _write(directory, text, name="tiny.md"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _template(tmp_path, text=_TINY, name="tiny.md"):
    return load_template(_write(tmp_path, text, name))


def _node(key, type_name, native_id, source_id, engine_id):
    return {
        "key": key,
        "type": type_name,
        "native_id": native_id,
        "source_id": source_id,
        "engine_id": engine_id,
        "scope": source_id,
        "kind": "",
        "label": type_name,
    }


def _edge(relation, source, target):
    return {
        "relation": relation,
        "from_key": source["key"],
        "to_key": target["key"],
        "from_type": source["type"],
        "to_type": target["type"],
    }


def _req(item_id="REQ-1", source_id="fix"):
    return {
        "key": f"Requirement | {source_id} | {item_id}",
        "source_id": source_id,
        "item_id": item_id,
    }


def _project(tmp_path):
    kg = pygrap.IndexedKnowledgeGraph(str(tmp_path / "kg"))
    items = [
        _item("REQ-B", "Second shall stay closed."),
        _item("REQ-1", "The lamp shall be off.\nSee [SWS_Com_00123]."),
        _item("SIG", "Vehicle speed.", kind_hint="text", attributes={"tag": "I-SIGNAL"}),
        _item("BUS", "Bus timeout.", kind_hint="text", source_id="bus", attributes={"tag": "I-SIGNAL"}),
    ]
    projection = project_items(items, kg)
    return kg, projection


def _key(projection, type_name, native_id, source_id="fix"):
    found = [
        node["key"] for node in projection["nodes"]
        if node["type"] == type_name
        and node["native_id"] == native_id
        and node["source_id"] == source_id
    ]
    assert len(found) == 1, (type_name, native_id, source_id, found)
    return found[0]


def _draft():
    return {
        "title": "Lamp",
        "cases": [{
            "id": "TC-1",
            "verifies": ["REQ-1", "REQ-2"],
            "preconditions": "power on",
            "stimulus": "press",
            "expected_observable_result": "lamp off",
            "pass_criterion": "lamp is off",
            "kind": "positive",
            "uses": ["Signal | fix | SIG"],
            "level": "system",
            "priority": "high",
        }],
    }


def _spec():
    return {
        "cases": [{
            "expected_observable_result": "lamp off",
            "id": "TC-1",
            "kind": "positive",
            "level": "software",
            "pass_criterion": "lamp is off",
            "preconditions": "power on",
            "stimulus": "press",
            "uses": ["Signal | fix | SIG"],
            "verifies": ["REQ-1", "REQ-2"],
        }],
        "level": "software",
        "title": "Lamp",
    }


def test_shipped_default_renders_one_case_and_joins_lists():
    """The file shipped at templates/test-spec/default.md is the §5a example.

    Lists join with a comma and a space. The cases block is the text
    between the markers, so the newline after the opener is inside the
    case and the newline before the opener stays outside.
    """
    template = load_template(_SHIPPED)
    assert template["id"] == "default"
    assert template["name"] == "Default test specification"
    assert template["level"] == "software"
    assert template["text"] == _SHIPPED.read_text(encoding="utf-8")
    assert render(template, _spec()) == _DEFAULT_RENDER


def test_render_repeats_the_block_and_leaves_missing_optionals_empty(tmp_path):
    """The block repeats once per case. No cases leaves the block out.

    A missing variant renders as nothing. A one-element list has no
    comma. A value that already contains a comma is not quoted.
    """
    template = _template(tmp_path)
    specification = {
        "title": "Hello",
        "level": "software",
        "cases": [
            {"id": "TC-1", "verifies": ["a, b", "c"], "variant": "left"},
            {"id": "TC-2", "verifies": ["C"]},
        ],
    }
    assert render(template, specification) == (
        "Head Hello\n"
        "[TC-1|a, b, c|left][TC-2|C|]\n"
        "Tail\n"
    )
    assert render(template, {"title": "Hello", "level": "software", "cases": []}) == (
        "Head Hello\n"
        "\n"
        "Tail\n"
    )


def test_a_placeholder_that_names_no_field_is_a_load_error(tmp_path):
    """Load refuses the template. It does not wait for a draft.

    Outside the block only title and level are fields. Inside it, only
    the case fields. No conditions, no nesting, no second block, no
    expressions. Header values stay strings.
    """
    def load(body, header="id: tiny\nname: Tiny\nlevel: software\n"):
        text = f"---\n{header}---\n{body}"
        return load_template(_write(tmp_path, text, "bad.md"))

    with pytest.raises(TemplateError, match="not_a_field"):
        load("{{title}}\n{{#cases}}{{id}}{{/cases}}\n{{not_a_field}}\n")
    with pytest.raises(TemplateError, match="id"):
        load("{{id}}\n{{#cases}}{{kind}}{{/cases}}\n")
    with pytest.raises(TemplateError, match="title"):
        load("{{title}}\n{{#cases}}{{title}}{{/cases}}\n")
    with pytest.raises(TemplateError, match="nested"):
        load("{{#cases}}{{#cases}}{{id}}{{/cases}}{{/cases}}\n")
    with pytest.raises(TemplateError, match="more than one"):
        load("{{#cases}}{{id}}{{/cases}}\n{{#cases}}{{id}}{{/cases}}\n")
    with pytest.raises(TemplateError, match="no {{#cases}}"):
        load("{{title}}\n")
    with pytest.raises(TemplateError, match="not closed"):
        load("{{#cases}}{{id}}\n")
    with pytest.raises(TemplateError, match="upper"):
        load("{{title | upper}}\n{{#cases}}{{id}}{{/cases}}\n")
    with pytest.raises(TemplateError, match="note"):
        load("{{title}}\n{{#cases}}{{id}}{{/cases}}\n", header="id: tiny\nname: Tiny\nlevel: software\nnote: hi\n")
    with pytest.raises(TemplateError, match="level"):
        load("{{title}}\n{{#cases}}{{id}}{{/cases}}\n", header="id: tiny\nname: Tiny\n")
    with pytest.raises(TemplateError, match="header"):
        load_template(_write(tmp_path, "# no header\n", "plain.md"))

    yes = load_template(_write(
        tmp_path,
        "---\nid: tiny\nname: Tiny\nlevel: yes\n---\n{{title}}\n{{#cases}}{{id}}{{/cases}}\n",
        "yes.md",
    ))
    assert yes["level"] == "yes"


def test_named_template_is_the_file_and_a_missing_id_is_an_error(tmp_path, capsys):
    """template_id selects the file whose header id matches. Nothing else.

    An unknown id is an error. The graph is not required. Zero
    requirements still names the template and prints the zero counts.
    """
    _write(tmp_path, _TINY)
    chosen = choose_template([], "tiny", templates_dir=tmp_path)
    assert chosen["id"] == "tiny"
    assert chosen["level"] == "software"
    assert "template id=tiny level=software n_requirements=0 n_docs=0" in capsys.readouterr().out

    with pytest.raises(TemplateError, match="missing"):
        choose_template([_req()], "missing", templates_dir=tmp_path)
    with pytest.raises(TemplateError, match="requirements"):
        choose_template("all", "tiny", templates_dir=tmp_path)


def test_uses_template_links_the_selected_requirements_to_one_file(tmp_path, capsys):
    """No template_id: the Template a selected requirement's test uses.

    The path is verifies into the requirement or one of its statements,
    then uses_template. traces_to, refers_to and a second template are
    not a guess. A node key is a name. A native id shared by two nodes
    is an error until the caller names a key.
    """
    requirement = _node("Requirement | fix | REQ-1", "Requirement", "REQ-1", "fix", 1)
    statement = _node("Statement | fix | ST-1", "Statement", "ST-1", "fix", 2)
    concept = _node("TestConcept | fix | CON", "TestConcept", "CON", "fix", 3)
    case = _node("TestCase | fix | CASE", "TestCase", "CASE", "fix", 4)
    parent = _node("Requirement | fix | PARENT", "Requirement", "PARENT", "fix", 5)
    other = _node("TestConcept | fix | OTHER", "TestConcept", "OTHER", "fix", 6)
    template = _node("Template | owner | tiny", "Template", "tiny", "owner", 7)
    wide = _node("Template | owner | wide", "Template", "wide", "owner", 8)
    parent_template = _node("Template | owner | parent", "Template", "parent", "owner", 9)
    _write(tmp_path, _TINY, "tiny.md")
    _write(tmp_path, _TINY.replace("id: tiny", "id: wide").replace("name: Tiny", "name: Wide"), "wide.md")
    _write(tmp_path, _TINY.replace("id: tiny", "id: parent").replace("name: Tiny", "name: Parent"), "parent.md")
    projection = {
        "nodes": [
            requirement, statement, concept, case, parent, other,
            template, wide, parent_template,
        ],
        "edges": [
            _edge("has_statement", requirement, statement),
            _edge("verifies", concept, statement),
            _edge("uses_template", concept, template),
            _edge("verifies", case, requirement),
            _edge("uses_template", case, template),
            _edge("traces_to", requirement, parent),
            _edge("verifies", other, parent),
            _edge("uses_template", other, parent_template),
            _edge("refers_to", concept, wide),
        ],
    }
    requirements = [_req()]
    chosen = choose_template(requirements, projection=projection, templates_dir=tmp_path)
    assert chosen["id"] == "tiny"
    by_key = choose_template(
        requirements, template["key"], projection=projection, templates_dir=tmp_path,
    )
    assert by_key["id"] == "tiny"
    named = choose_template(
        requirements, "wide", projection=projection, templates_dir=tmp_path,
    )
    assert named["id"] == "wide"
    out = capsys.readouterr().out
    assert out.count("template id=tiny level=software n_requirements=1 n_docs=1") == 2
    assert "template id=wide level=software n_requirements=1 n_docs=1" in out

    bare = {
        "nodes": projection["nodes"],
        "edges": [_edge("refers_to", concept, template)],
    }
    with pytest.raises(TemplateError, match="uses_template"):
        choose_template(requirements, projection=bare, templates_dir=tmp_path)
    with pytest.raises(TemplateError, match="projection"):
        choose_template(requirements, templates_dir=tmp_path)

    both = {
        "nodes": projection["nodes"],
        "edges": [
            _edge("verifies", concept, requirement),
            _edge("uses_template", concept, template),
            _edge("verifies", case, requirement),
            _edge("uses_template", case, wide),
        ],
    }
    with pytest.raises(TemplateError, match="2") as caught:
        choose_template(requirements, projection=both, templates_dir=tmp_path)
    text = str(caught.value)
    assert "tiny" in text and "wide" in text

    twin_a = _node("Template | a | custom", "Template", "custom", "a", 10)
    twin_b = _node("Template | b | custom", "Template", "custom", "b", 11)
    twins = {
        "nodes": [requirement, concept, twin_a, twin_b],
        "edges": [],
    }
    with pytest.raises(TemplateError, match="2") as caught:
        choose_template(requirements, "custom", projection=twins, templates_dir=tmp_path)
    assert twin_a["key"] in str(caught.value)
    assert twin_b["key"] in str(caught.value)
    with pytest.raises(TemplateError, match="file"):
        choose_template(
            requirements, twin_a["key"], projection=twins, templates_dir=tmp_path,
        )


def test_a_named_node_without_a_file_is_an_error(tmp_path):
    """A Template node is a name. The Markdown file is the template."""
    requirement = _node("Requirement | fix | REQ-1", "Requirement", "REQ-1", "fix", 1)
    template = _node("Template | owner | custom", "Template", "custom", "owner", 2)
    projection = {"nodes": [requirement, template], "edges": []}
    with pytest.raises(TemplateError, match="custom") as caught:
        choose_template([_req()], template["key"], projection=projection, templates_dir=tmp_path)
    assert "file" in str(caught.value)


def test_compose_instructions_copies_item_text_and_prints_denominators(tmp_path, capsys):
    """Fixed text, then the items' own text, then the template file. No model.

    Requirement order is the list order. Context order is the group order.
    The engine's native-id line is not part of the item's own text. Issues
    are included only when the caller supplied them. Nothing selected
    prints n/a.
    """
    kg, projection = _project(tmp_path)
    capsys.readouterr()
    template = _template(tmp_path)
    requirements = [
        {"key": _key(projection, "Requirement", "REQ-B"), "source_id": "fix", "item_id": "REQ-B"},
        {"key": _key(projection, "Requirement", "REQ-1"), "source_id": "fix", "item_id": "REQ-1"},
    ]
    context = {"requirements": [
        {
            "key": requirements[1]["key"],
            "source_id": "fix",
            "item_id": "REQ-1",
            "nodes": [
                _key(projection, "Signal", "SIG"),
                _key(projection, "Signal", "BUS", "bus"),
            ],
        },
    ]}
    text = compose_instructions(kg, projection, requirements, context, template)
    assert text == (
        FIXED_TEXT
        + "Requirements (n=2, documents=1):\n"
        + "REQ-B (fix)\n"
        + "Second shall stay closed.\n"
        + "REQ-1 (fix)\n"
        + "The lamp shall be off.\n"
        + "See [SWS_Com_00123].\n"
        + "\n"
        + "Context (n=2, documents=2):\n"
        + "REQ-1 (fix)\n"
        + "SIG (fix)\n"
        + "Vehicle speed.\n"
        + "BUS (bus)\n"
        + "Bus timeout.\n"
        + "\n"
        + "Template:\n"
        + template["text"]
    )
    assert "REQ-1\nThe lamp shall be off." not in text
    assert "[SWS_Com_00123]" in text
    assert "instructions n_requirements=2 n_context=2 n_docs=2" in capsys.readouterr().out

    issues = [{"case": "TC-1", "check": "coverage", "value": "REQ-9"}]
    with_issues = compose_instructions(
        kg, projection, requirements[:1], {"requirements": []}, template, issues,
    )
    rendered_issues = json.dumps(issues, ensure_ascii=False, sort_keys=True, indent=2)
    assert with_issues.endswith("Issues (n=1):\n" + rendered_issues + "\n")
    assert "Second shall stay closed." in with_issues
    assert "The lamp shall be off." not in with_issues

    empty = compose_instructions(kg, projection, [], {"requirements": []}, template)
    assert empty.startswith(FIXED_TEXT)
    assert "Requirements (n=0, documents=0):" in empty
    assert "instructions=n/a n_requirements=0 n_context=0 n_docs=0" in capsys.readouterr().out

    again = compose_instructions(kg, projection, requirements, context, template)
    assert again == text


def test_emit_stores_the_structure_and_renders_beside_it(tmp_path, capsys):
    """The version is the hash of the canonical specification, not the Markdown.

    level on a case is the template's level. A model-supplied level and
    any field outside §5 are not stored. passed must be True or nothing
    is written. The same specification is the same version in two stores.
    A different template body does not change the version.
    """
    template = _template(tmp_path)
    store = tmp_path / "versions"
    other = tmp_path / "other"
    artifact = emit(_draft(), template, True, store=store)
    canonical = json.dumps(_spec(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    version = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert artifact["version"] == version
    assert artifact["specification"] == _spec()
    assert artifact["rendering"] == "Head Lamp\n[TC-1|REQ-1, REQ-2|]\nTail\n"
    assert (store / f"{version}.json").read_text(encoding="utf-8") == canonical + "\n"
    assert (store / f"{version}.md").read_text(encoding="utf-8") == artifact["rendering"]
    assert "# " not in (store / f"{version}.json").read_text(encoding="utf-8")
    assert "priority" not in canonical
    assert "system" not in canonical
    summary = capsys.readouterr().out
    assert f"artifact version={version} n_cases=1 n_docs=n/a" in summary

    again = emit(_draft(), template, True, store=other)
    assert again["version"] == version
    assert (other / f"{version}.json").read_bytes() == (store / f"{version}.json").read_bytes()

    rewritten = dict(template)
    rewritten["body"] = "Other {{title}}\n{{#cases}}{{id}}{{/cases}}\n"
    rewritten["text"] = "---\nid: tiny\nname: Tiny\nlevel: software\n---\n" + rewritten["body"]
    moved = emit(_draft(), rewritten, True, store=store)
    assert moved["version"] == version
    assert moved["rendering"] != artifact["rendering"]

    untitled = {"cases": []}
    empty = emit(untitled, template, True, store=store)
    assert empty["specification"]["title"] == "Tiny"
    assert empty["specification"]["cases"] == []
    assert empty["specification"]["level"] == "software"
    assert f"artifact=n/a version={empty['version']} n_cases=0 n_docs=n/a" in capsys.readouterr().out

    refused = tmp_path / "refused"
    with pytest.raises(TemplateError, match="nothing was stored"):
        emit(_draft(), template, False, store=refused)
    with pytest.raises(TemplateError, match="nothing was stored"):
        emit(_draft(), template, "true", store=refused)
    assert not refused.exists()

    listed = emit([{"id": "TC-9", "kind": "negative", "verifies": ["REQ-1"]}], template, True, store=store)
    assert listed["specification"]["title"] == "Tiny"
    assert listed["specification"]["cases"][0]["id"] == "TC-9"
    assert listed["specification"]["cases"][0]["level"] == "software"
    assert "variant" not in listed["specification"]["cases"][0]


def test_emit_does_not_store_a_case_field_it_cannot_render(tmp_path):
    template = _template(tmp_path)
    store = tmp_path / "bad"
    draft = {"title": "Lamp", "cases": [{"id": "TC-1", "verifies": 1}]}
    with pytest.raises(TemplateError, match="verifies"):
        emit(draft, template, True, store=store)
    assert not store.exists()


def test_interpreter_runs_the_three_operations_and_refuses_the_model(tmp_path, capsys):
    kg, projection = _project(tmp_path)
    capsys.readouterr()
    _write(tmp_path, _TINY)
    procedure = load_procedure(textwrap.dedent(
        """
        name: generate-test-spec
        version: "0.1"
        steps:
          - id: choose
            operation: choose_template
            inputs:
              requirements:
                - key: REQ
                  source_id: fix
                  item_id: REQ-1
              template_id: tiny
          - id: compose
            operation: compose_instructions
            inputs:
              requirements:
                - key: KEY
                  source_id: fix
                  item_id: REQ-1
              context:
                requirements: []
              template: choose.outputs.template
          - id: emit
            operation: emit
            inputs:
              draft:
                title: Lamp
                cases:
                  - id: TC-1
                    verifies: [REQ-1]
                    kind: positive
              template: choose.outputs.template
              passed: true
        """
    ).strip() + "\n")
    # The requirement key is filled after projection so the compose step
    # reads the item's own text. choose only needs the id.
    key = _key(projection, "Requirement", "REQ-1")
    procedure.steps[1].inputs["requirements"][0]["key"] = key
    ops = TemplateOperations(kg, projection, tmp_path / "store", templates_dir=tmp_path)
    run = interpret(procedure, ops)
    assert run.status == "ok"
    assert run.rounds == 0
    assert run.artifact["specification"]["cases"][0]["level"] == "software"
    assert run.artifact["specification"]["cases"][0]["verifies"] == ["REQ-1"]
    assert "The lamp shall be off." in run.outputs["compose"]["instructions"]
    assert "[SWS_Com_00123]" in run.outputs["compose"]["instructions"]
    assert run.outputs["choose"]["template"]["id"] == "tiny"
    out = capsys.readouterr().out
    assert "template id=tiny level=software n_requirements=1 n_docs=1" in out
    assert "instructions n_requirements=1 n_context=0 n_docs=1" in out
    assert f"artifact version={run.artifact['version']} n_cases=1 n_docs=n/a" in out

    with pytest.raises(TemplateError, match="invoke_model"):
        ops("invoke_model", {"instructions": "write a test"})
    with pytest.raises(TemplateError, match="select_requirements"):
        ops("select_requirements", {"scope": ["REQ-1"]})
