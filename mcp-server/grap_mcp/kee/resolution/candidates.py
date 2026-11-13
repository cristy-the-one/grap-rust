"""Stage 2 of entity resolution (spec-entity-resolution.md).

For each mention Stage 1 left unlinked, collect candidate entities and keep
their union. The three sources are shared rare tokens, initials of a
multi-word name, and nearest neighbours by a contextual embedding.
Type-incompatible entities are dropped before a source can select them, so a
wrong type does not take a neighbour place.

This stage is shadow mode only. It computes and reports candidates. It does
not create, merge, or change a link, and it does not write a decision. Scores
are Stage 3.

"Rare" is not a fixed cutoff. A token is rare when its entity frequency is at
or below the median frequency of the tokens in the entity catalogue. The
median is computed from that catalogue. The neighbour count is the caller's:
the spec leaves every threshold open, so this stage does not choose one. With
no embedder or no neighbour count, that source is not run and the report says
n/a.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from grap_mcp.kee.resolution.normalise import normalise
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables

_KEY_SEP = " | "
_RARE = "rare_token"
_INITIALS = "initials"
_EMBEDDING = "embedding"
_NA = "n/a"

_TYPE_BY_FOLD: dict[str, str] | None = None


@dataclass(frozen=True)
class Entity:
    """One entity Stage 2 may propose. ``key`` is ``type | scope | native_id``."""

    key: str
    name: str
    long_name: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        _entity_key(self.key)
        _required(self.name, "name")
        if self.long_name is not None:
            _required(self.long_name, "long_name")
        if self.description is not None:
            _required(self.description, "description")


@dataclass(frozen=True)
class Candidate:
    """One mention paired with one entity. ``sources`` is the union that found it."""

    mention_id: int | None
    entity_key: str
    sources: frozenset[str]


@dataclass(frozen=True)
class CandidateRun:
    candidates: tuple[Candidate, ...]
    report: dict


def find_candidates(
    mentions: Sequence[Mention],
    entities: Sequence[Entity],
    *,
    shadow: bool = True,
    graph: object | None = None,
    tables: ResolutionTables | None = None,
    linked_mention_ids: Collection[int] | None = None,
    embedder: object | None = None,
    neighbour_k: int | None = None,
) -> CandidateRun:
    """Collect Stage 2 candidates and print the counts.

    ``graph`` is accepted so the caller can hold the live graph in the same
    call. Shadow mode does not read or write it. ``shadow=False`` raises
    before any of that work: this stage has no mode that changes links.
    """
    if shadow is not True:
        raise RuntimeError(
            "Stage 2 is shadow mode only and does not create, merge, or change links"
        )
    if graph is not None and not hasattr(graph, "node_count"):
        raise TypeError("graph must be the working graph, or absent")
    if tables is not None and not isinstance(tables, ResolutionTables):
        raise TypeError("tables must be resolution tables, or absent")

    mention_rows = _mentions(mentions)
    entity_rows = _entities(entities)
    k = _neighbour_k(neighbour_k)
    channel = embedder is not None and k is not None
    if channel and not hasattr(embedder, "embed"):
        raise TypeError("embedder must provide embed(), or be absent")

    linked = _linked_ids(tables, linked_mention_ids)
    ordered = sorted(mention_rows, key=_mention_key)
    unlinked = [mention for mention in ordered if mention.mention_id not in linked]
    types = _schema_types()
    frequencies, median = _token_frequencies(entity_rows)

    contexts = {
        entity.key: text
        for entity in entity_rows
        if (text := _entity_context(entity)) is not None
    }
    vectors: dict[str, list[float]] = {}
    mention_vectors: dict[int, list[float]] = {}
    if channel and unlinked:
        mention_vectors = _embed_mentions(embedder, unlinked)
        vectors = _embed_entities(embedder, entity_rows, contexts)

    candidates: list[Candidate] = []
    per_mention: list[int] = []
    type_incompatible = 0
    from_rare = 0
    from_initials = 0
    from_embedding = 0
    for mention in unlinked:
        compatible: list[Entity] = []
        for entity in entity_rows:
            if _types_conflict(mention.type_hint, entity.key, types):
                type_incompatible += 1
                continue
            compatible.append(entity)
        found: dict[str, set[str]] = {}
        forms = _mention_forms(mention.text)
        mention_tokens = _tokens(mention.text)
        for entity in compatible:
            if _shares_rare_token(mention_tokens, entity, frequencies, median):
                found.setdefault(entity.key, set()).add(_RARE)
            if _initials_match(forms, entity):
                found.setdefault(entity.key, set()).add(_INITIALS)
        if channel:
            for key in _nearest(mention, compatible, vectors, mention_vectors, k):
                found.setdefault(key, set()).add(_EMBEDDING)
        for key in sorted(found):
            sources = frozenset(found[key])
            candidates.append(Candidate(mention.mention_id, key, sources))
            from_rare += _RARE in sources
            from_initials += _INITIALS in sources
            from_embedding += _EMBEDDING in sources
        per_mention.append(len(found))

    examined = len(unlinked) > 0
    report = {
        "shadow": 1,
        "n_mentions": len(mention_rows),
        "n_unlinked": len(unlinked),
        "n_linked_skipped": len(mention_rows) - len(unlinked),
        "n_entities": len(entity_rows),
        "n_documents": len({mention.source_id for mention in mention_rows}),
        "n_examined_pairs": len(unlinked) * len(entity_rows) if examined else None,
        "n_candidate_pairs": len(candidates) if examined else None,
        "candidates_per_mention": tuple(per_mention) if examined else None,
        "n_type_incompatible": type_incompatible if examined else None,
        "n_from_rare_tokens": from_rare if examined else None,
        "n_from_initials": from_initials if examined else None,
        "n_from_embedding": from_embedding if examined and channel else None,
        "rare_token_median": median,
        "n_token_types": len(frequencies) if entity_rows else None,
        "embedding_k": k if channel else None,
        "n_embedded_entities": len(vectors) if examined and channel else None,
    }
    print(format_report(report))
    return CandidateRun(tuple(candidates), report)


def format_report(report: Mapping) -> str:
    """One line. A figure with nothing behind it is ``n/a``."""
    fields = (
        ("shadow", report["shadow"]),
        ("mentions", report["n_mentions"]),
        ("n_mentions", report["n_mentions"]),
        ("n_unlinked", report["n_unlinked"]),
        ("n_linked_skipped", report["n_linked_skipped"]),
        ("n_entities", report["n_entities"]),
        ("n_documents", report["n_documents"]),
        ("n_examined_pairs", report["n_examined_pairs"]),
        ("n_candidate_pairs", report["n_candidate_pairs"]),
        ("candidates_per_mention", report["candidates_per_mention"]),
        ("n_type_incompatible", report["n_type_incompatible"]),
        ("n_from_rare_tokens", report["n_from_rare_tokens"]),
        ("n_from_initials", report["n_from_initials"]),
        ("n_from_embedding", report["n_from_embedding"]),
        ("rare_token_median", report["rare_token_median"]),
        ("n_token_types", report["n_token_types"]),
        ("embedding_k", report["embedding_k"]),
        ("n_embedded_entities", report["n_embedded_entities"]),
    )
    return " ".join(f"{name}={_cell(value)}" for name, value in fields)


def _mentions(value: Sequence[Mention]) -> list[Mention]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("mentions must be a sequence")
    rows = list(value)
    for mention in rows:
        if not isinstance(mention, Mention):
            raise TypeError("mentions must be Mention values")
    return rows


def _entities(value: Sequence[Entity]) -> list[Entity]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("entities must be a sequence")
    rows = list(value)
    for entity in rows:
        if not isinstance(entity, Entity):
            raise TypeError("entities must be Entity values")
    return rows


def _neighbour_k(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("neighbour_k must be a positive integer or absent")
    return value


def _linked_ids(tables: ResolutionTables | None, extra: Collection[int] | None) -> set[int]:
    """Mentions Stage 1 (or a person) has already linked.

    The latest decision for a pair wins. A ``same`` that is not a proposal is
    a link. A proposal is Stage 3's shadow score and leaves the mention open.
    """
    linked: set[int] = set()
    if extra is not None:
        if isinstance(extra, (str, bytes)):
            raise TypeError("linked_mention_ids must be ids, not a string")
        for mention_id in extra:
            if isinstance(mention_id, bool) or not isinstance(mention_id, int):
                raise TypeError("linked mention id must be an int")
            linked.add(mention_id)
    if tables is None:
        return linked
    rows = tables.conn.execute(
        """
        SELECT mention_id, entity_key, verdict, proposal
        FROM link_decisions
        ORDER BY decision_id
        """
    ).fetchall()
    latest: dict[tuple[int, str], tuple[str, int]] = {}
    for row in rows:
        latest[(int(row["mention_id"]), row["entity_key"])] = (
            row["verdict"], int(row["proposal"]),
        )
    for mention_id, _entity_key in list(latest):
        verdict, proposal = latest[(mention_id, _entity_key)]
        if verdict == "same" and proposal == 0:
            linked.add(mention_id)
    return linked


def _mention_key(mention: Mention) -> tuple:
    mention_id = -1 if mention.mention_id is None else mention.mention_id
    return (mention.source_id, mention.item_id, mention.span_start, mention.span_end, mention_id)


def _schema_types() -> dict[str, str]:
    global _TYPE_BY_FOLD
    if _TYPE_BY_FOLD is None:
        from grap_mcp.schema.loader import load_automotive_schema
        _TYPE_BY_FOLD = {
            _fold_label(node.name): node.name
            for node in load_automotive_schema().node_types
        }
    return _TYPE_BY_FOLD


def _fold_label(label: str | None) -> str:
    if not label:
        return ""
    return "".join(ch for ch in label.casefold() if ch.isalnum())


def _types_conflict(hint: str | None, entity_key: str, types: dict[str, str]) -> bool:
    """A known schema type on both sides, and the two types differ.

    The spec's case is a signal mention against a component. A hint that is
    not a schema type is not a conflict this stage can judge.
    """
    left = types.get(_fold_label(hint))
    right = types.get(_fold_label(_entity_key(entity_key).split(_KEY_SEP)[0]))
    return left is not None and right is not None and left != right


def _token_frequencies(entities: Sequence[Entity]) -> tuple[dict[str, int], float | None]:
    counts: dict[str, int] = {}
    for entity in entities:
        for token in _entity_tokens(entity):
            counts[token] = counts.get(token, 0) + 1
    if not counts:
        return counts, None
    return counts, _median(list(counts.values()))


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _entity_tokens(entity: Entity) -> set[str]:
    tokens = _tokens(entity.name)
    if entity.long_name is not None:
        tokens |= _tokens(entity.long_name)
    return tokens


def _tokens(text: str) -> set[str]:
    return {part for part in normalise(text).split(" ") if part}


def _shares_rare_token(
    mention_tokens: set[str],
    entity: Entity,
    frequencies: Mapping[str, int],
    median: float | None,
) -> bool:
    if median is None:
        return False
    shared = mention_tokens & _entity_tokens(entity)
    return any(frequencies[token] <= median for token in shared)


def _mention_forms(text: str) -> set[str]:
    """Comparison form, plus the same form with abbreviation dots removed.

    Stage 0 keeps the dots in ``E.C.U.``. The initials of a multi-word name
    have no dots, so the abbreviation form is compared as well.
    """
    folded = normalise(text)
    return {folded, folded.replace(".", "").replace(" ", "")}


def _initials_match(forms: set[str], entity: Entity) -> bool:
    for text in (entity.long_name, entity.name):
        if text is None:
            continue
        initials = _initials(text)
        if initials is not None and initials in forms:
            return True
    return False


def _initials(text: str) -> str | None:
    tokens = [part for part in normalise(text).split(" ") if part]
    if len(tokens) < 2:
        return None
    return "".join(token[0] for token in tokens)


def _entity_context(entity: Entity) -> str | None:
    """Long name and description. The bare name is never the embedded string."""
    long_name = entity.long_name.strip() if entity.long_name else ""
    description = entity.description.strip() if entity.description else ""
    if long_name and description:
        return f"{long_name}\n{description}"
    if long_name and long_name != entity.name.strip():
        return long_name
    if description and description != entity.name.strip():
        return description
    return None


def _embed(embedder: object, texts: Sequence[str]) -> list[list[float]]:
    vectors = getattr(embedder, "embed")(texts)
    if len(vectors) != len(texts):
        raise ValueError("embedder returned a different number of vectors")
    return [list(vector) for vector in vectors]


def _embed_mentions(embedder: object, mentions: Sequence[Mention]) -> dict[int, list[float]]:
    texts = [f"{mention.text}\n{mention.sentence}" for mention in mentions]
    vectors = _embed(embedder, texts)
    return {
        -1 if mention.mention_id is None else mention.mention_id: vector
        for mention, vector in zip(mentions, vectors)
    }


def _embed_entities(
    embedder: object, entities: Sequence[Entity], contexts: Mapping[str, str],
) -> dict[str, list[float]]:
    keyed = [entity for entity in entities if entity.key in contexts]
    if not keyed:
        return {}
    vectors = _embed(embedder, [contexts[entity.key] for entity in keyed])
    return {entity.key: vector for entity, vector in zip(keyed, vectors)}


def _nearest(
    mention: Mention,
    compatible: Sequence[Entity],
    vectors: Mapping[str, list[float]],
    mention_vectors: Mapping[int, list[float]],
    k: int,
) -> list[str]:
    mention_id = -1 if mention.mention_id is None else mention.mention_id
    mention_vector = mention_vectors[mention_id]
    ranked = sorted(
        (
            -_cosine(mention_vector, vectors[entity.key]),
            entity.key,
        )
        for entity in compatible
        if entity.key in vectors
    )
    return [key for _score, key in ranked[:k]]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions differ")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _entity_key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError("entity key must be a string")
    parts = key.split(_KEY_SEP)
    if len(parts) != 3 or any(part == "" for part in parts):
        raise ValueError(
            "entity key must be 'type | scope | native_id' "
            "(a discovered entity is 'Term | source | normalised name')"
        )
    return key


def _required(value: object, field: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _cell(value: object) -> str:
    if value is None:
        return _NA
    if isinstance(value, tuple):
        return ",".join(_cell(item) for item in value)
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)
