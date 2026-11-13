"""S-01: the automotive schema is data, loaded from YAML.

The 22 type names and 30 relation names are the tables in
docs/spec-automotive-schema.md §3 and §4, in that order.
"""

from grap_mcp.schema import load_automotive_schema

# §3, table order.
_NODE_TYPES = (
    "Source",
    "Item",
    "Statement",
    "Requirement",
    "TestConcept",
    "Template",
    "TestCase",
    "Ecu",
    "Component",
    "Interface",
    "InterfaceElement",
    "Network",
    "Frame",
    "Pdu",
    "Signal",
    "DataType",
    "Parameter",
    "State",
    "Fault",
    "DiagnosticService",
    "Term",
    "Element",
)

# §4, table order.
_RELATIONS = (
    "in_source",
    "stated_in",
    "mentions",
    "part_of",
    "has_statement",
    "about",
    "refers_to",
    "traces_to",
    "allocated_to",
    "depends_on",
    "verifies",
    "instantiates",
    "uses_template",
    "provides",
    "requires",
    "connects_to",
    "runs_on",
    "configured_by",
    "attached_to",
    "sends",
    "receives",
    "transmitted_on",
    "realised_by",
    "has_type",
    "applies_in",
    "transitions_to",
    "raises",
    "defines",
    "alias_of",
    "corresponds_to",
)


def test_schema_has_22_types_30_relations_and_known_ends():
    schema = load_automotive_schema()

    type_names = [node.name for node in schema.node_types]
    assert type_names == list(_NODE_TYPES)
    assert len(type_names) == 22

    relation_names = [relation.name for relation in schema.relations]
    assert relation_names == list(_RELATIONS)
    assert len(relation_names) == 30

    known = set(type_names)
    for relation in schema.relations:
        assert relation.pairs, relation.name
        for source, target in relation.pairs:
            assert source in known, (relation.name, source)
            assert target in known, (relation.name, target)
        assert set(relation.from_types) <= known, relation.name
        assert set(relation.to_types) <= known, relation.name
        assert set(relation.from_types) == {source for source, _ in relation.pairs}
        assert set(relation.to_types) == {target for _, target in relation.pairs}
