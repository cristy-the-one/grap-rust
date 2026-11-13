"""Stage 1 rules 2 and 3: defined aliases and self-reference.

A definition assigns a name to an entity. It applies inside the source that
states it, and in other sources only when the owner marks that source as a
shared glossary. The forms are an abbreviation list, ``X — hereafter Y``
(and the Romanian ``denumită în continuare``), and a glossary line.

Self-reference is a fixed phrase table. The mention links to the entity the
source declares, and only in that source's language.

A type conflict blocks either link. Comparison uses Stage 0. When the
entities that remain differ only by case, the mention links to none of them
and the candidates are recorded as ambiguous: the same-source entity is not
chosen. Any other tie is left ambiguous too. Rule 2 does not borrow rule 1's
scope order.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from grap_mcp.kee.resolution.exact import (
    IdentifiedEntity,
    _differ_only_by_case,
    _entities,
    _entity_key,
    _last_segment,
    _mentions,
    type_conflict,
)
from grap_mcp.kee.resolution.normalise import normalise
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables

# The phrase is the whole mention. Nothing else in that language is added here.
SELF_REFERENCE_PHRASES = {
    "en": ("this module", "the module"),
    "ro": ("prezenta lege",),
}

_LANGUAGES = tuple(SELF_REFERENCE_PHRASES)
_PHRASE_FORMS = {
    language: frozenset(normalise(phrase) for phrase in phrases)
    for language, phrases in SELF_REFERENCE_PHRASES.items()
}

_SIGNIFICATII = re.compile(r"semnifica[țţt]ii", re.I)
_ENTRY_START = re.compile(
    r"(?:^\s*|[;\n]\s*|\.\s+)(?P<term>(?:[A-Z]\.){2,}|[^;:\n.]{1,120}?)\s+[-–—]\s+",
    re.M,
)
_HEREAFTER_EN = re.compile(
    r"^(?P<entity>.+?)\s*[—–-]\s*hereafter\s+(?P<alias>.+?)\s*$",
    re.I,
)
_HEREAFTER_RO = re.compile(
    r"^(?P<entity>.+?),\s+denumit[ăa]?\s+în\s+continuare\s+(?P<alias>[^.;,]+)\s*$",
    re.I,
)
_GLOSSARY = re.compile(
    r"^\s*(?P<alias>[^:\n—–]{1,120}?)\s*(?::|[—–])\s*(?P<entity>\S.*?)\s*$",
)


@dataclass(frozen=True)
class DefinitionText:
    """One defining sentence, glossary line, or abbreviation list, and its source."""

    source_id: str
    text: str


@dataclass(frozen=True)
class _Assignment:
    source_id: str
    alias: str
    entity_name: str


def link_defined(
    tables: ResolutionTables,
    mentions: Sequence[Mention],
    entities: Sequence[IdentifiedEntity],
    definitions: Sequence[DefinitionText],
    *,
    subjects: Mapping[str, Sequence[IdentifiedEntity]] | None = None,
    languages: Mapping[str, str] | None = None,
    shared_glossaries: Sequence[str] | None = None,
    decided_at: str,
) -> dict:
    """Link mentions by a defined alias, then by self-reference.

    A unique link is appended as ``same``, decider ``rule``, method
    ``defined_alias`` or ``self_reference``. An ambiguous mention records
    its candidates and appends nothing. A latest human ``not_same`` for the
    chosen pair is left as the latest decision.

    The printed line carries counts only. With no mentions the measured
    counts are ``n/a``.
    """
    if not isinstance(tables, ResolutionTables):
        raise TypeError("tables must be ResolutionTables")
    if not isinstance(decided_at, str) or decided_at.strip() == "":
        raise ValueError("decided_at must be a non-empty string")
    prepared = _entities(entities)
    rows = _mentions(mentions)
    definition_rows = _definition_rows(definitions)
    assignments = _assignments(definition_rows)
    declared = _subjects(subjects)
    language_of = _languages(languages)
    shared = _shared(shared_glossaries)

    if not rows:
        report = {
            "mentions": None,
            "linked_defined_alias": None,
            "linked_self_reference": None,
            "ambiguous": None,
            "unlinked": None,
            "n_mentions": 0,
            "n_entities": len(prepared),
            "n_documents": 0,
            "n_definitions": len(definition_rows),
            "n_ambiguous_candidates": None,
            "links": (),
            "ambiguous_mentions": (),
            "unlinked_ids": (),
        }
        print(format_report(report))
        return report

    links: list[tuple[int, str]] = []
    ambiguous: list[tuple[int, tuple[str, ...]]] = []
    unlinked: list[int] = []
    alias_links = 0
    self_links = 0

    for mention in sorted(rows, key=_mention_key):
        found, method = _resolve(mention, prepared, assignments, declared, language_of, shared)
        if method is None:
            unlinked.append(mention.mention_id)
            continue
        # Case-only entities are ambiguous even when one of them shares the
        # mention's source. Rule 2 has no further scope order, so every other
        # tie is ambiguous as well.
        if len(found) > 1 and _differ_only_by_case([surface for _, surface in found]):
            ambiguous.append(_candidate_row(mention.mention_id, found))
            continue
        if len(found) != 1:
            ambiguous.append(_candidate_row(mention.mention_id, found))
            continue
        entity = found[0][0]
        key = _entity_key(entity)
        if _human_rejects(tables, mention.mention_id, key):
            unlinked.append(mention.mention_id)
            continue
        tables.append_decision(
            mention.mention_id,
            key,
            "same",
            method=method,
            score=None,
            decider="rule",
            decided_at=decided_at,
        )
        links.append((mention.mention_id, key))
        if method == "defined_alias":
            alias_links += 1
        else:
            self_links += 1

    documents = {mention.source_id for mention in rows}
    candidates = sum(len(row[1]) for row in ambiguous)
    report = {
        "mentions": len(rows),
        "linked_defined_alias": alias_links,
        "linked_self_reference": self_links,
        "ambiguous": len(ambiguous),
        "unlinked": len(unlinked),
        "n_mentions": len(rows),
        "n_entities": len(prepared),
        "n_documents": len(documents),
        "n_definitions": len(definition_rows),
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
        f"linked_defined_alias={_cell(report['linked_defined_alias'])} "
        f"linked_self_reference={_cell(report['linked_self_reference'])} "
        f"ambiguous={_cell(report['ambiguous'])} "
        f"unlinked={_cell(report['unlinked'])} "
        f"n_mentions={report['n_mentions']} "
        f"n_entities={report['n_entities']} "
        f"n_documents={report['n_documents']} "
        f"n_definitions={report['n_definitions']} "
        f"n_ambiguous_candidates={_cell(report['n_ambiguous_candidates'])}"
    )


def _resolve(mention, entities, assignments, declared, language_of, shared):
    alias_hits = _alias_hits(mention, entities, assignments, shared)
    if alias_hits:
        return alias_hits, "defined_alias"
    self_hits = _self_hits(mention, declared, language_of)
    if self_hits:
        return self_hits, "self_reference"
    return [], None


def _alias_hits(mention, entities, assignments, shared) -> list[tuple[IdentifiedEntity, str]]:
    form = normalise(mention.text)
    if form == "":
        return []
    found: list[tuple[IdentifiedEntity, str]] = []
    seen: set[str] = set()
    for assignment in assignments:
        if not _in_scope(assignment, mention, shared):
            continue
        if normalise(assignment.alias) != form:
            continue
        meaning = normalise(assignment.entity_name)
        if meaning == "":
            continue
        for entity in entities:
            if type_conflict(mention.type_hint, entity.type):
                continue
            surface = _surface(entity, meaning)
            if surface is None:
                continue
            key = _entity_key(entity)
            if key in seen:
                continue
            seen.add(key)
            found.append((entity, surface))
    return found


def _self_hits(mention, declared, language_of) -> list[tuple[IdentifiedEntity, str]]:
    language = language_of.get(mention.source_id)
    if language is None:
        return []
    form = normalise(mention.text)
    if form not in _PHRASE_FORMS[language]:
        return []
    found: list[tuple[IdentifiedEntity, str]] = []
    for entity in declared.get(mention.source_id, ()):
        if type_conflict(mention.type_hint, entity.type):
            continue
        found.append((entity, entity.native_id))
    return found


def _in_scope(assignment: _Assignment, mention: Mention, shared: set[str]) -> bool:
    return assignment.source_id == mention.source_id or assignment.source_id in shared


def _surface(entity: IdentifiedEntity, meaning: str) -> str | None:
    native = normalise(entity.native_id)
    if native == meaning and native != "":
        return entity.native_id
    segment = _last_segment(entity.native_id)
    if segment != entity.native_id and normalise(segment) == meaning:
        return segment
    return None


def _assignments(definitions: Sequence[DefinitionText]) -> tuple[_Assignment, ...]:
    rows: list[_Assignment] = []
    for definition in definitions:
        for alias, entity_name in _parse(definition.text):
            if alias.strip() == "" or entity_name.strip() == "":
                continue
            rows.append(_Assignment(definition.source_id, alias.strip(), entity_name.strip()))
    return tuple(rows)


def _parse(text: str) -> list[tuple[str, str]]:
    listed = _abbreviation_list(text)
    if listed is not None:
        return listed
    if match := _HEREAFTER_EN.match(text.strip()):
        return [(match.group("alias").strip(), match.group("entity").strip())]
    if match := _HEREAFTER_RO.match(text.strip()):
        return [(match.group("alias").strip(), match.group("entity").strip())]
    if match := _GLOSSARY.match(text.strip()):
        return [(match.group("alias").strip(), match.group("entity").strip())]
    return []


def _abbreviation_list(text: str) -> list[tuple[str, str]] | None:
    intro = _SIGNIFICATII.search(text)
    if intro is None:
        return None
    colon = text.find(":", intro.end())
    if colon < 0:
        return []
    tail = text[colon + 1:]
    entries = list(_ENTRY_START.finditer(tail))
    found: list[tuple[str, str]] = []
    for index, match in enumerate(entries):
        term = match["term"].strip()
        end = entries[index + 1].start() if index + 1 < len(entries) else len(tail)
        semicolon = tail.find(";", match.end(), end)
        if semicolon >= 0:
            end = semicolon + 1
        meaning = tail[match.end():end].strip().rstrip(";").strip()
        if meaning.endswith(".") and " " in meaning:
            meaning = meaning[:-1].strip()
        if not term or len(term) > 120 or not meaning:
            continue
        found.append((term, meaning))
    return found


def _definition_rows(definitions: Sequence[DefinitionText]) -> tuple[DefinitionText, ...]:
    if isinstance(definitions, (str, bytes)) or not isinstance(definitions, Sequence):
        raise TypeError("definitions must be a sequence of DefinitionText")
    rows: list[DefinitionText] = []
    for definition in definitions:
        if not isinstance(definition, DefinitionText):
            raise TypeError("definitions must be a sequence of DefinitionText")
        _part(definition.source_id, "source_id")
        _part(definition.text, "text")
        rows.append(definition)
    return tuple(rows)


def _subjects(subjects: Mapping[str, Sequence[IdentifiedEntity]] | None) -> dict[str, tuple[IdentifiedEntity, ...]]:
    if subjects is None:
        return {}
    if isinstance(subjects, (str, bytes)) or not isinstance(subjects, Mapping):
        raise TypeError("subjects must be a mapping of source_id to entities")
    cleaned: dict[str, tuple[IdentifiedEntity, ...]] = {}
    for source_id, value in subjects.items():
        _part(source_id, "source_id")
        if isinstance(value, IdentifiedEntity):
            declared = (value,)
        elif isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise TypeError("a source's subject must be an entity or a sequence of them")
        else:
            declared = tuple(value)
        cleaned[source_id] = _entities(declared)
    return cleaned


def _languages(languages: Mapping[str, str] | None) -> dict[str, str]:
    if languages is None:
        return {}
    if isinstance(languages, (str, bytes)) or not isinstance(languages, Mapping):
        raise TypeError("languages must be a mapping of source_id to a language")
    cleaned: dict[str, str] = {}
    for source_id, language in languages.items():
        _part(source_id, "source_id")
        if language not in _LANGUAGES:
            names = ", ".join(_LANGUAGES)
            raise ValueError(f"language must be one of {names}")
        cleaned[source_id] = language
    return cleaned


def _shared(shared_glossaries: Sequence[str] | None) -> set[str]:
    if shared_glossaries is None:
        return set()
    if isinstance(shared_glossaries, (str, bytes)) or not isinstance(shared_glossaries, Sequence):
        raise TypeError("shared_glossaries must be a sequence of source ids")
    return {_part(source_id, "source_id") for source_id in shared_glossaries}


def _part(value: object, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string")
    if " | " in value:
        raise ValueError(f"{field} must not contain ' | '")
    return value


def _human_rejects(tables: ResolutionTables, mention_id: int, entity_key: str) -> bool:
    latest = tables.decision_for(mention_id, entity_key)
    return latest is not None and latest.decider == "human" and latest.verdict == "not_same"


def _candidate_row(mention_id: int, found) -> tuple[int, tuple[str, ...]]:
    keys = tuple(sorted(_entity_key(item[0]) for item in found))
    return mention_id, keys


def _mention_key(mention: Mention) -> tuple:
    return (
        mention.source_id,
        mention.item_id,
        mention.span_start,
        mention.span_end,
        mention.mention_id,
    )


def _cell(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)
