"""Whether §4 allows a (source type, relation, target type) triple.

The allowed pairs come from the schema loader. Pair lists and same-type
relations are not the product of the two end lists.
"""

from __future__ import annotations

from .loader import load_automotive_schema


def relation_allowed(source_type: str, relation: str, target_type: str) -> bool:
    """True when this source type may point at this target type via relation."""
    schema = load_automotive_schema()
    for entry in schema.relations:
        if entry.name == relation:
            return (source_type, target_type) in entry.pairs
    return False
