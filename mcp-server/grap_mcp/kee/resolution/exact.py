"""Stage 1 rule 1: identifier match (spec-entity-resolution.md).

Comparison uses Stage 0. The normalised mention equals the normalised
``native_id``, or the normalised last ``/`` segment of that id. A type
conflict blocks the link: a mention hinted as a signal is not linked to a
component.

Several matches are kept in this order: same source, then same file set,
then same release. A preference that fits exactly one entity selects it. A
preference that fits several narrows the set. A preference that fits none
is skipped. If more than one entity remains, the mention links to none and
those candidates are recorded as ambiguous.

Identifiers are case-sensitive. The normaliser folds case, so two entities
whose matched spellings differ only by case are ambiguous even when one of
them shares the mention's source. The folded form is used only when exactly
one entity matches it.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from grap_mcp.kee.resolution.normalise import normalise
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables

# kind_hint, or the schema type's own name, → schema type. Schema §8.
# A hint that is not in this table and is not already the entity type does
# not invent a conflict.
_HINT_TYPE = {
    "signal": "Signal",
    "message": "Frame",
    "frame": "Frame",
    "pdu": "Pdu",
    "component": "Component",
    "ecu": "Ecu",
    "network": "Network",
    "interface": "Interface",
    "data_element": "InterfaceElement",
    "interface_element": "InterfaceElement",
    "datatype": "DataType",
    "data_type": "DataType",
    "scaling": "DataType",
    "unit": "DataType",
    "parameter": "Parameter",
    "config_container": "Parameter",
    "config_module": "Parameter",
    "requirement": "Requirement",
    "mode": "State",
    "state": "State",
    "fault": "Fault",
    "diagnostic_service": "DiagnosticService",
    "term": "Term",
    "element": "Element",
    "test_concept": "TestConcept",
    "template": "Template",
    "test_case": "TestCase",
}


@dataclass(frozen=True)
class IdentifiedEntity:
    """An entity a reader identified. The key is ``type | scope | native_id``."""

    type: str
    native_id: str
    source_id: str
    release: str | None = None
    file_set: str | None = None


@dataclass(frozen=True)
class SourceScope:
    """File set and release of the document a mention was read from.

    ``source_id`` is already on the mention. These two are absent when the
    document does not say.
    """

    file_set: str | None = None
    release: str | None = None


def type_conflict(type_hint: str | None, entity_type: str) -> bool:
    """True when the hint names a schema type and the entity is a different one."""
    if type_hint is None:
        return False
    if not isinstance(type_hint, str):
        raise TypeError("type_hint must be a string or absent")
    hint = type_hint.strip().casefold().replace("-", "_").replace(" ", "_")
    if hint == "":
        return False
    if not isinstance(entity_type, str) or entity_type.strip() == "":
        raise ValueError("entity type must be a non-empty string")
    entity = entity_type.strip().casefold()
    if hint == entity or hint.replace("_", "") == entity.replace("_", ""):
        return False
    mapped = _HINT_TYPE.get(hint)
    if mapped is None:
        return False
    return mapped.casefold() != entity


def link_identifiers(
    tables: ResolutionTables,
    mentions: Sequence[Mention],
    entities: Sequence[IdentifiedEntity],
    *,
    sources: Mapping[str, SourceScope] | None = None,
    decided_at: str,
) -> dict:
    """Link each mention to one identified entity, or record the ambiguity.

    A unique link is appended as a ``same`` decision, method ``identifier``,
    decider ``rule``. Nothing is appended for an ambiguous mention, or for a
    mention with no match. A latest human ``not_same`` for the chosen pair
    is left as the latest decision.

    The printed line carries counts only. With no mentions the measured
    counts are ``n/a``.
    """
    if not isinstance(tables, ResolutionTables):
        raise TypeError("tables must be ResolutionTables")
    if not isinstance(decided_at, str) or decided_at.strip() == "":
        raise ValueError("decided_at must be a non-empty string")
    prepared = _entities(entities)
    scopes = _scopes(sources)
    rows = _mentions(mentions)

    if not rows:
        report = {
            "mentions": None,
            "linked_identifier": None,
            "ambiguous": None,
            "unlinked": None,
            "n_mentions": 0,
            "n_entities": len(prepared),
            "n_documents": 0,
            "n_ambiguous_candidates": None,
            "links": (),
            "ambiguous_mentions": (),
            "unlinked_ids": (),
        }
        print(format_report(report))
        return report

    forms = [_forms(entity) for entity in prepared]
    links: list[tuple[int, str]] = []
    ambiguous: list[tuple[int, tuple[str, ...]]] = []
    unlinked: list[int] = []

    for mention in sorted(rows, key=_mention_key):
        found = _matching(mention, prepared, forms)
        if not found:
            unlinked.append(mention.mention_id)
            continue
        if len(found) > 1 and _differ_only_by_case([item[1] for item in found]):
            ambiguous.append(_candidate_row(mention.mention_id, found))
            continue
        chosen = found
        if len(found) > 1:
            chosen = _narrow(found, mention, scopes.get(mention.source_id))
        if len(chosen) != 1:
            ambiguous.append(_candidate_row(mention.mention_id, chosen))
            continue
        entity = chosen[0][0]
        key = _entity_key(entity)
        if _human_rejects(tables, mention.mention_id, key):
            unlinked.append(mention.mention_id)
            continue
        tables.append_decision(
            mention.mention_id,
            key,
            "same",
            method="identifier",
            score=None,
            decider="rule",
            decided_at=decided_at,
        )
        links.append((mention.mention_id, key))

    documents = {mention.source_id for mention in rows}
    candidates = sum(len(row[1]) for row in ambiguous)
    report = {
        "mentions": len(rows),
        "linked_identifier": len(links),
        "ambiguous": len(ambiguous),
        "unlinked": len(unlinked),
        "n_mentions": len(rows),
        "n_entities": len(prepared),
        "n_documents": len(documents),
        "n_ambiguous_candidates": candidates,
        "links": tuple(links),
        "ambiguous_mentions": tuple(ambiguous),
        "unlinked_ids": tuple(unlinked),
    }
    print(format_report(report))
    return report


def format_report(report: Mapping) -> str:
    """One line. A figure with nothing behind it is ``n/a``."""
    return (
        f"mentions={_cell(report['mentions'])} "
        f"linked_identifier={_cell(report['linked_identifier'])} "
        f"ambiguous={_cell(report['ambiguous'])} "
        f"unlinked={_cell(report['unlinked'])} "
        f"n_mentions={report['n_mentions']} "
        f"n_entities={report['n_entities']} "
        f"n_documents={report['n_documents']} "
        f"n_ambiguous_candidates={_cell(report['n_ambiguous_candidates'])}"
    )


def _cell(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _mention_key(mention: Mention) -> tuple:
    return (
        mention.source_id,
        mention.item_id,
        mention.span_start,
        mention.span_end,
        mention.mention_id,
    )


def _matching(mention, entities, forms) -> list[tuple[IdentifiedEntity, str]]:
    form = normalise(mention.text)
    if form == "":
        return []
    found: list[tuple[IdentifiedEntity, str]] = []
    for entity, (native_form, segment, segment_form) in zip(entities, forms):
        if type_conflict(mention.type_hint, entity.type):
            continue
        if native_form == form:
            found.append((entity, entity.native_id))
            continue
        if segment != entity.native_id and segment_form == form:
            found.append((entity, segment))
    return found


def _forms(entity: IdentifiedEntity) -> tuple[str, str, str]:
    segment = _last_segment(entity.native_id)
    return normalise(entity.native_id), segment, normalise(segment)


def _last_segment(native_id: str) -> str:
    if "/" not in native_id:
        return native_id
    parts = [part for part in native_id.split("/") if part]
    if not parts:
        return native_id
    return parts[-1]


def _differ_only_by_case(surfaces: Sequence[str]) -> bool:
    folded = {_case_fold(surface) for surface in surfaces}
    if len(folded) != 1:
        return False
    written = {unicodedata.normalize("NFC", surface) for surface in surfaces}
    return len(written) > 1


def _case_fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _narrow(found, mention: Mention, scope: SourceScope | None):
    current = list(found)

    def source_ok(item) -> bool:
        return item[0].source_id == mention.source_id

    def file_ok(item) -> bool:
        entity = item[0]
        return (
            scope is not None
            and scope.file_set is not None
            and entity.file_set is not None
            and entity.file_set == scope.file_set
        )

    def release_ok(item) -> bool:
        entity = item[0]
        return (
            scope is not None
            and scope.release is not None
            and entity.release is not None
            and entity.release == scope.release
        )

    for predicate in (source_ok, file_ok, release_ok):
        preferred = [item for item in current if predicate(item)]
        if len(preferred) == 1:
            return preferred
        if len(preferred) > 1:
            current = preferred
    return current


def _candidate_row(mention_id: int, found) -> tuple[int, tuple[str, ...]]:
    keys = tuple(sorted(_entity_key(item[0]) for item in found))
    return mention_id, keys


def _human_rejects(tables: ResolutionTables, mention_id: int, entity_key: str) -> bool:
    latest = tables.decision_for(mention_id, entity_key)
    return latest is not None and latest.decider == "human" and latest.verdict == "not_same"


def _entities(entities: Sequence[IdentifiedEntity]) -> tuple[IdentifiedEntity, ...]:
    if isinstance(entities, (str, bytes)) or not isinstance(entities, Sequence):
        raise TypeError("entities must be a sequence of IdentifiedEntity")
    prepared: list[IdentifiedEntity] = []
    seen: set[str] = set()
    for entity in entities:
        if not isinstance(entity, IdentifiedEntity):
            raise TypeError("entities must be a sequence of IdentifiedEntity")
        key = _entity_key(entity)
        if key in seen:
            raise ValueError(f"duplicate entity {key}")
        seen.add(key)
        _optional(entity.file_set, "file_set")
        prepared.append(entity)
    return tuple(prepared)


def _entity_key(entity: IdentifiedEntity) -> str:
    type_name = _part(entity.type, "type")
    source_id = _part(entity.source_id, "source_id")
    native_id = _part(entity.native_id, "native_id")
    release = _optional(entity.release, "release")
    scope = source_id if release is None else f"{source_id}@{release}"
    if " | " in scope:
        raise ValueError("scope must not contain ' | '")
    return f"{type_name} | {scope} | {native_id}"


def _part(value: object, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string")
    if " | " in value:
        raise ValueError(f"{field} must not contain ' | '")
    return value


def _optional(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string or absent")
    if " | " in value:
        raise ValueError(f"{field} must not contain ' | '")
    return value


def _scopes(sources: Mapping[str, SourceScope] | None) -> dict[str, SourceScope]:
    if sources is None:
        return {}
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping of source_id to SourceScope")
    cleaned: dict[str, SourceScope] = {}
    for source_id, scope in sources.items():
        _part(source_id, "source_id")
        if not isinstance(scope, SourceScope):
            raise TypeError("sources must map to SourceScope")
        _optional(scope.file_set, "file_set")
        _optional(scope.release, "release")
        cleaned[source_id] = scope
    return cleaned


def _mentions(mentions: Sequence[Mention]) -> tuple[Mention, ...]:
    if isinstance(mentions, (str, bytes)) or not isinstance(mentions, Sequence):
        raise TypeError("mentions must be a sequence of Mention")
    rows: list[Mention] = []
    for mention in mentions:
        if not isinstance(mention, Mention):
            raise TypeError("mentions must be a sequence of Mention")
        if not isinstance(mention.mention_id, int) or isinstance(mention.mention_id, bool):
            raise ValueError("mention must already be stored")
        rows.append(mention)
    return tuple(rows)
