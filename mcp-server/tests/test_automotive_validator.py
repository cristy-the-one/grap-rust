"""S-02: a (type, relation, type) triple is allowed or refused, per §4.

One allowed example and one refused example for each relation, in the order
of docs/spec-automotive-schema.md §4. Expected answers are taken from that
section. The implementation must read them from the S-01 loader.
"""

import pytest

from grap_mcp.schema import load_automotive_schema
from grap_mcp.schema.validate import relation_allowed

# (source type, relation, target type, allowed)
# Refused examples use two known types whenever §4 refuses such a pair.
# refers_to allows every pair of known types, so its refusal is a name that
# is not a node type.
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
    ("Item", "refers_to", "NotAType", False),
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
    allowed: dict[str, tuple[str, str]] = {}
    refused: dict[str, tuple[str, str]] = {}
    for source_type, relation, target_type, is_allowed in _EXAMPLES:
        bucket = allowed if is_allowed else refused
        assert relation not in bucket, relation
        bucket[relation] = (source_type, target_type)
    assert list(allowed) == relation_names
    assert list(refused) == relation_names
    assert len(_EXAMPLES) == 2 * len(relation_names)


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
