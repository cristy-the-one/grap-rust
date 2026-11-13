"""Whether §4 allows a (source type, relation, target type) triple.

The schema is loaded once. Answers are membership tests on a set of
(relation, source type, target type). A type or relation the schema does
not contain raises. Pair lists and same-type relations are not the product
of the two end lists; the set holds the loader's listed pairs.
"""

from __future__ import annotations

from .loader import load_automotive_schema

_types: set[str] | None = None
_relations: set[str] | None = None
_pairs: set[tuple[str, str, str]] | None = None


def _ensure_loaded() -> tuple[set[str], set[str], set[tuple[str, str, str]]]:
    global _types, _relations, _pairs
    if _pairs is None or _types is None or _relations is None:
        schema = load_automotive_schema()
        types = {node.name for node in schema.node_types}
        relations = {relation.name for relation in schema.relations}
        pairs = {
            (relation.name, source, target)
            for relation in schema.relations
            for source, target in relation.pairs
        }
        _types = types
        _relations = relations
        _pairs = pairs
        return types, relations, pairs
    return _types, _relations, _pairs


def relation_allowed(source_type: str, relation: str, target_type: str) -> bool:
    """True when this source type may point at this target type via relation."""
    types, relations, pairs = _ensure_loaded()
    if relation not in relations:
        raise ValueError(f"unknown relation: {relation!r}")
    if source_type not in types:
        raise ValueError(f"unknown node type: {source_type!r}")
    if target_type not in types:
        raise ValueError(f"unknown node type: {target_type!r}")
    return (relation, source_type, target_type) in pairs
