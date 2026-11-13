"""S-02: a (type, relation, type) triple is allowed or refused, per §4.

One allowed example and one refused example for each relation that forbids a
pair of known types, in the order of docs/spec-automotive-schema.md §4.
refers_to allows every such pair, so it has only an allowed example. An
unknown type or relation raises. Expected answers are taken from that section.
"""

import pytest

from grap_mcp.schema import load_automotive_schema
from grap_mcp.schema import validate
from grap_mcp.schema.validate import relation_allowed

# (source type, relation, target type, allowed)
# Refused examples are two known types. refers_to has no such refusal.
_EXAMPLES = (
    ("Item", "in_source", "Source", True),
    ("Source", "in_source", "Item", False),
    ("Requirement", "stated_in", "Item", True),
    ("Source", "stated_in", "Item", False),
    ("Item", "mentions", "Signal", True),
    ("Item", "mentions", "Source", False),
    ("Signal", "part_of", "Pdu", True),
    ("Requirement", "part_of", "Component", False),
    ("Requirement", "has_statement", "Statement", True),
    ("Statement", "has_statement", "Requirement", False),
    ("Statement", "about", "Signal", True),
    ("Statement", "about", "Item", False),
    ("Item", "refers_to", "Signal", True),
    ("Requirement", "traces_to", "Requirement", True),
    ("Requirement", "traces_to", "Component", False),
    ("Requirement", "allocated_to", "Ecu", True),
    ("Component", "allocated_to", "Requirement", False),
    ("Component", "depends_on", "Component", True),
    ("Requirement", "depends_on", "Component", False),
    ("TestCase", "verifies", "Statement", True),
    ("Requirement", "verifies", "TestCase", False),
    ("TestCase", "instantiates", "TestConcept", True),
    ("TestConcept", "instantiates", "TestCase", False),
    ("TestConcept", "uses_template", "Template", True),
    ("Template", "uses_template", "TestConcept", False),
    ("Component", "provides", "DiagnosticService", True),
    ("Component", "provides", "Signal", False),
    ("Component", "requires", "Interface", True),
    ("Component", "requires", "DiagnosticService", False),
    ("Component", "connects_to", "Component", True),
    ("Ecu", "connects_to", "Component", False),
    ("Component", "runs_on", "Ecu", True),
    ("Ecu", "runs_on", "Component", False),
    ("Statement", "configured_by", "Parameter", True),
    ("Parameter", "configured_by", "Statement", False),
    ("Ecu", "attached_to", "Network", True),
    ("Network", "attached_to", "Ecu", False),
    ("Ecu", "sends", "Frame", True),
    ("Frame", "sends", "Ecu", False),
    ("Component", "receives", "Pdu", True),
    ("Pdu", "receives", "Component", False),
    ("Pdu", "transmitted_on", "Network", True),
    ("Signal", "transmitted_on", "Network", False),
    ("InterfaceElement", "realised_by", "Signal", True),
    ("Signal", "realised_by", "InterfaceElement", False),
    ("DataType", "has_type", "DataType", True),
    ("DataType", "has_type", "Signal", False),
    ("DiagnosticService", "applies_in", "State", True),
    ("State", "applies_in", "DiagnosticService", False),
    ("State", "transitions_to", "State", True),
    ("State", "transitions_to", "Fault", False),
    ("DiagnosticService", "raises", "Fault", True),
    ("Fault", "raises", "DiagnosticService", False),
    ("Statement", "defines", "Term", True),
    ("Term", "defines", "Statement", False),
    ("Term", "alias_of", "Signal", True),
    ("Term", "alias_of", "Item", False),
    ("Signal", "corresponds_to", "Signal", True),
    ("Signal", "corresponds_to", "Frame", False),
)


def test_each_relation_has_one_allowed_and_one_refused_example():
    schema = load_automotive_schema()
    relation_names = [relation.name for relation in schema.relations]
    n_types = len(schema.node_types)
    # A relation whose pairs are the full type × type product has no refused
    # example of two known types. n_types is the loader's node-type count.
    open_relations = [
        relation.name
        for relation in schema.relations
        if len(relation.pairs) == n_types * n_types
    ]
    allowed: dict[str, tuple[str, str]] = {}
    refused: dict[str, tuple[str, str]] = {}
    for source_type, relation, target_type, is_allowed in _EXAMPLES:
        bucket = allowed if is_allowed else refused
        assert relation not in bucket, relation
        bucket[relation] = (source_type, target_type)
    closed = [name for name in relation_names if name not in open_relations]
    assert list(allowed) == relation_names
    assert list(refused) == closed
    assert open_relations == ["refers_to"]
    assert len(_EXAMPLES) == len(relation_names) + len(closed)


def test_schema_is_loaded_once(monkeypatch):
    validate._pairs = None
    calls = {"n": 0}
    real = validate.load_automotive_schema

    def wrapped(path=None):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(validate, "load_automotive_schema", wrapped)
    assert relation_allowed("Item", "in_source", "Source") is True
    assert relation_allowed("Source", "in_source", "Item") is False
    assert calls["n"] == 1


def test_answer_is_a_set_lookup():
    relation_allowed("Signal", "part_of", "Pdu")
    pairs = getattr(validate, "_pairs", None)
    assert isinstance(pairs, set)
    assert ("part_of", "Signal", "Pdu") in pairs
    assert ("part_of", "Requirement", "Component") not in pairs


@pytest.mark.parametrize(
    ("source_type", "relation", "target_type", "unknown"),
    (
        ("Item", "refers_to", "NotAType", "NotAType"),
        ("NotAType", "in_source", "Source", "NotAType"),
        ("Item", "not_a_relation", "Signal", "not_a_relation"),
    ),
)
def test_unknown_name_raises(source_type, relation, target_type, unknown):
    with pytest.raises(ValueError, match=unknown):
        relation_allowed(source_type, relation, target_type)


@pytest.mark.parametrize(
    ("source_type", "relation", "target_type", "is_allowed"),
    _EXAMPLES,
    ids=[
        f"{source}-{relation}-{'allowed' if is_allowed else 'refused'}-{target}"
        for source, relation, target, is_allowed in _EXAMPLES
    ],
)
def test_relation_allowed(source_type, relation, target_type, is_allowed):
    assert relation_allowed(source_type, relation, target_type) is is_allowed
