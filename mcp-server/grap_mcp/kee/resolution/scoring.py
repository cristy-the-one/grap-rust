"""Stage 3 of entity resolution (spec-entity-resolution.md).

Score the candidate pairs Stage 2 already found. This stage is shadow mode
only. It computes features and a score, returns them as proposals, and prints
the counts. It does not create, merge, or change a link, and it does not
append to ``link_decisions``. That table is append-only, so a shadow row
could not be removed.

The features are the ones the spec lists. The score is the unweighted mean of
the features that were measured: each measured feature counts once, and a
feature that was not measured is left out. No other coefficient is used.
Contextual embedding similarity is left out when there is no embedder, or
when the entity has no context beyond its bare name. The conflict flag is not
a weight. The spec says it always means not same, so it sets the outcome.

No threshold is applied. Fitting thresholds is E-18, and until then a pair
that is not a conflict stays undecided. The same-band count is ``n/a``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from grap_mcp.kee.reconcile import merge_compatible
from grap_mcp.kee.resolution.candidates import (
    Candidate,
    Entity,
    _cosine,
    _entity_context,
    _initials_match,
    _mention_forms,
    _schema_types,
    _tokens,
    _types_conflict,
)
from grap_mcp.kee.resolution.normalise import normalise
from grap_mcp.kee.resolution.tables import Mention, ResolutionTables

_KEY_SEP = " | "
_NA = "n/a"
_NOT_SAME = "not_same"
_UNDECIDED = "undecided"

# The same identifier test the working graph uses (card R-09). A whole name
# with no whitespace is an identifier when it carries an underscore, a slash,
# a dot, a digit, or a capital after the first character. Inside a phrase,
# only an underscore, a leading slash, a CamelCase hump, or a bracketed id
# counts. Prose dots and digits do not.
_BRACKETED_ID = re.compile(r"\[[^\]\s]+\]")
_CAMEL_HUMP = re.compile(r"[a-z][A-Z]")


@dataclass(frozen=True)
class Features:
    """The Stage 3 measurements for one candidate pair.

    Booleans are the yes/no features. Ratios are from 0 to 1.
    ``embedding_similarity`` is absent when it was not measured.
    ``token_overlap`` and ``edit_similarity`` are absent only when the
    comparison string has no characters to measure.
    """

    equal_normalised: bool
    token_overlap: float | None
    edit_similarity: float | None
    initials_match: bool
    embedding_similarity: float | None
    type_compatible: bool
    same_source: bool
    conflict: bool


@dataclass(frozen=True)
class Proposal:
    """One shadow score. ``proposal`` is always true, and nothing stores it."""

    mention_id: int | None
    entity_key: str
    sources: frozenset[str]
    features: Features
    score: float | None
    outcome: str
    proposal: bool = True


@dataclass(frozen=True)
class ScoreRun:
    proposals: tuple[Proposal, ...]
    report: dict


def score_candidates(
    candidates: Sequence[Candidate],
    mentions: Sequence[Mention],
    entities: Sequence[Entity],
    *,
    shadow: bool = True,
    graph: object | None = None,
    tables: ResolutionTables | None = None,
    embedder: object | None = None,
) -> ScoreRun:
    """Score Stage 2 candidates and print the counts.

    ``graph`` and ``tables`` are accepted so the caller can hold the live
    graph and the store in the same call. Shadow mode does not read or write
    either. ``shadow=False`` raises before any of that work: this stage has
    no mode that writes a decision.
    """
    if shadow is not True:
        raise RuntimeError(
            "Stage 3 is shadow mode only and does not create, merge, or change links"
        )
    if graph is not None and not hasattr(graph, "node_count"):
        raise TypeError("graph must be the working graph, or absent")
    if tables is not None and not isinstance(tables, ResolutionTables):
        raise TypeError("tables must be resolution tables, or absent")
    if embedder is not None and not hasattr(embedder, "embed"):
        raise TypeError("embedder must provide embed(), or be absent")

    mention_rows = _mentions(mentions)
    entity_rows = _entities(entities)
    candidate_rows = _candidates(candidates)
    by_mention = _by_mention(mention_rows)
    by_entity = _by_entity(entity_rows)
    paired = _pairs(candidate_rows, by_mention, by_entity)

    types = _schema_types()
    vectors = _vectors(embedder, paired) if embedder is not None and paired else {}
    proposals = tuple(sorted(
        (
            _proposal(candidate, mention, entity, types, vectors)
            for candidate, mention, entity in paired
        ),
        key=lambda proposal: _proposal_key(proposal, by_mention[proposal.mention_id]),
    ))
    report = _report(mention_rows, entity_rows, proposals, embedder is not None)
    print(format_report(report))
    return ScoreRun(proposals, report)


def format_report(report: Mapping) -> str:
    """One line. A figure with nothing behind it is ``n/a``."""
    fields = (
        ("shadow", report["shadow"]),
        ("mentions", report["n_mentions"]),
        ("n_mentions", report["n_mentions"]),
        ("n_entities", report["n_entities"]),
        ("n_documents", report["n_documents"]),
        ("n_candidate_pairs", report["n_candidate_pairs"]),
        ("n_proposals", report["n_proposals"]),
        ("n_conflict", report["n_conflict"]),
        ("n_not_same", report["n_not_same"]),
        ("n_undecided", report["n_undecided"]),
        ("n_same", report["n_same"]),
        ("n_equal", report["n_equal"]),
        ("n_initials_match", report["n_initials_match"]),
        ("n_type_compatible", report["n_type_compatible"]),
        ("n_same_source", report["n_same_source"]),
        ("n_with_embedding", report["n_with_embedding"]),
        ("n_scored", report["n_scored"]),
        ("score_n", report["score_n"]),
        ("scores", report["scores"]),
    )
    return " ".join(f"{name}={_cell(value)}" for name, value in fields)


def _proposal(
    candidate: Candidate,
    mention: Mention,
    entity: Entity,
    types: dict[str, str],
    vectors: Mapping[str, list[float]],
) -> Proposal:
    forms = _compared_forms(entity)
    mention_form = normalise(mention.text)
    equal = mention_form in forms
    overlap = _token_overlap(mention, entity)
    edited = _best_edit(mention_form, forms)
    initials = _initials_match(_mention_forms(mention.text), entity)
    embedded = _embedding(mention, entity, vectors)
    compatible = not _types_conflict(mention.type_hint, entity.key, types)
    same_source = mention.source_id == _scope(entity.key)
    conflict = _conflict(mention.text, entity.name)
    features = Features(
        equal_normalised=equal,
        token_overlap=overlap,
        edit_similarity=edited,
        initials_match=initials,
        embedding_similarity=embedded,
        type_compatible=compatible,
        same_source=same_source,
        conflict=conflict,
    )
    score, _count = _mean(features)
    outcome = _NOT_SAME if conflict else _UNDECIDED
    return Proposal(
        mention_id=mention.mention_id,
        entity_key=entity.key,
        sources=candidate.sources,
        features=features,
        score=score,
        outcome=outcome,
    )


def _mean(features: Features) -> tuple[float | None, int]:
    """Unweighted mean of the measured features. Conflict is not one of them."""
    values = (
        1.0 if features.equal_normalised else 0.0,
        features.token_overlap,
        features.edit_similarity,
        1.0 if features.initials_match else 0.0,
        features.embedding_similarity,
        1.0 if features.type_compatible else 0.0,
        1.0 if features.same_source else 0.0,
    )
    measured = [value for value in values if value is not None]
    if not measured:
        return None, 0
    return sum(measured) / len(measured), len(measured)


def _compared_forms(entity: Entity) -> set[str]:
    forms = {normalise(entity.name)}
    if entity.long_name is not None:
        forms.add(normalise(entity.long_name))
    return forms


def _token_overlap(mention: Mention, entity: Entity) -> float | None:
    """Token-level F1 of the two names. The 0.8 cutoff in Part B3 is not applied."""
    left = _tokens(mention.text)
    right = _tokens(entity.name)
    if entity.long_name is not None:
        right |= _tokens(entity.long_name)
    if not left or not right:
        return None
    shared = len(left & right)
    return (2 * shared) / (len(left) + len(right))


def _best_edit(mention_form: str, forms: set[str]) -> float | None:
    scores = [_edit_similarity(mention_form, form) for form in forms]
    measured = [score for score in scores if score is not None]
    if not measured:
        return None
    return max(measured)


def _edit_similarity(left: str, right: str) -> float | None:
    if left == "" and right == "":
        return None
    return 1.0 - _levenshtein(left, right) / max(len(left), len(right))


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if left == "":
        return len(right)
    if right == "":
        return len(left)
    previous = list(range(len(right) + 1))
    for i, left_ch in enumerate(left, 1):
        current = [i]
        for j, right_ch in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[j] + 1,
                previous[j - 1] + (left_ch != right_ch),
            ))
        previous = current
    return previous[-1]


def _embedding(
    mention: Mention, entity: Entity, vectors: Mapping[str, list[float]],
) -> float | None:
    context = _entity_context(entity)
    if context is None or context not in vectors:
        return None
    text = f"{mention.text}\n{mention.sentence}"
    if text not in vectors:
        return None
    return _cosine(vectors[text], vectors[context])


def _conflict(mention_text: str, entity_name: str) -> bool:
    """Differing numbers, the existing kind veto, or differing identifier tokens.

    ``merge_compatible`` is the veto already used on the live path. It is
    false when both sides carry numbers and those numbers differ, or when
    both sides open with a recognised kind and the kinds differ. Identifier
    tokens are the extra case that veto does not cover.
    """
    if not merge_compatible(mention_text, entity_name):
        return True
    return _identifiers_differ(mention_text, entity_name)


def _identifiers_differ(left: str, right: str) -> bool:
    left_tokens = _identifier_tokens(left)
    right_tokens = _identifier_tokens(right)
    return bool(left_tokens) and bool(right_tokens) and left_tokens != right_tokens


def _identifier_tokens(text: str) -> set[str]:
    raw = re.findall(r"\S+", text)
    chosen: list[str] = []
    if len(raw) == 1 and _name_is_identifier(raw[0]):
        chosen.append(raw[0])
    else:
        chosen.extend(token for token in raw if _token_is_strong_identifier(token))
    chosen.extend(_BRACKETED_ID.findall(text))
    return {token for token in (normalise(part) for part in chosen) if token}


def _name_is_identifier(name: str) -> bool:
    if not name or any(ch.isspace() for ch in name):
        return False
    if any(ch in "_/." or ch.isdigit() for ch in name):
        return True
    return any(ch.isupper() for ch in name[1:])


def _token_is_strong_identifier(token: str) -> bool:
    return "_" in token or token.startswith("/") or _CAMEL_HUMP.search(token) is not None


def _scope(entity_key: str) -> str:
    return entity_key.split(_KEY_SEP)[1]


def _vectors(embedder: object, paired: Sequence[tuple]) -> dict[str, list[float]]:
    texts: list[str] = []
    seen: set[str] = set()

    def add(text: str | None) -> None:
        if text and text not in seen:
            seen.add(text)
            texts.append(text)

    for _candidate, mention, entity in paired:
        add(f"{mention.text}\n{mention.sentence}")
        add(_entity_context(entity))
    if not texts:
        return {}
    vectors = getattr(embedder, "embed")(texts)
    if len(vectors) != len(texts):
        raise ValueError("embedder returned a different number of vectors")
    return {text: list(vector) for text, vector in zip(texts, vectors)}


def _report(
    mentions: Sequence[Mention],
    entities: Sequence[Entity],
    proposals: Sequence[Proposal],
    embedded: bool,
) -> dict:
    scored = bool(proposals)
    denominators = tuple(_mean(proposal.features)[1] for proposal in proposals)
    return {
        "shadow": 1,
        "n_mentions": len(mentions),
        "n_entities": len(entities),
        "n_documents": len({mention.source_id for mention in mentions}),
        "n_candidate_pairs": len(proposals) if scored else None,
        "n_proposals": len(proposals) if scored else None,
        "n_conflict": _count(proposals, lambda proposal: proposal.features.conflict) if scored else None,
        "n_not_same": _count(proposals, lambda proposal: proposal.outcome == _NOT_SAME) if scored else None,
        "n_undecided": _count(proposals, lambda proposal: proposal.outcome == _UNDECIDED) if scored else None,
        "n_same": None,
        "n_equal": _count(proposals, lambda proposal: proposal.features.equal_normalised) if scored else None,
        "n_initials_match": _count(proposals, lambda proposal: proposal.features.initials_match) if scored else None,
        "n_type_compatible": _count(proposals, lambda proposal: proposal.features.type_compatible) if scored else None,
        "n_same_source": _count(proposals, lambda proposal: proposal.features.same_source) if scored else None,
        "n_with_embedding": (
            _count(proposals, lambda proposal: proposal.features.embedding_similarity is not None)
            if scored and embedded else None
        ),
        "n_scored": _count(proposals, lambda proposal: proposal.score is not None) if scored else None,
        "score_n": denominators if scored else None,
        "scores": tuple(proposal.score for proposal in proposals) if scored else None,
    }


def _count(proposals: Sequence[Proposal], predicate) -> int:
    return sum(1 for proposal in proposals if predicate(proposal))


def _proposal_key(proposal: Proposal, mention: Mention) -> tuple:
    mention_id = -1 if mention.mention_id is None else mention.mention_id
    return (
        mention.source_id, mention.item_id, mention.span_start, mention.span_end,
        mention_id, proposal.entity_key,
    )


def _pairs(
    candidates: Sequence[Candidate],
    by_mention: Mapping[int | None, Mention],
    by_entity: Mapping[str, Entity],
) -> list[tuple[Candidate, Mention, Entity]]:
    paired = []
    for candidate in candidates:
        mention = by_mention.get(candidate.mention_id)
        if mention is None:
            raise ValueError(f"no mention {candidate.mention_id}")
        entity = by_entity.get(candidate.entity_key)
        if entity is None:
            raise ValueError(f"no entity {candidate.entity_key}")
        paired.append((candidate, mention, entity))
    return paired


def _by_mention(mentions: Sequence[Mention]) -> dict[int | None, Mention]:
    found: dict[int | None, Mention] = {}
    for mention in mentions:
        if mention.mention_id in found:
            raise ValueError(f"mention {mention.mention_id} is repeated")
        found[mention.mention_id] = mention
    return found


def _by_entity(entities: Sequence[Entity]) -> dict[str, Entity]:
    found: dict[str, Entity] = {}
    for entity in entities:
        if entity.key in found:
            raise ValueError(f"entity {entity.key} is repeated")
        found[entity.key] = entity
    return found


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


def _candidates(value: Sequence[Candidate]) -> list[Candidate]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("candidates must be a sequence")
    rows = list(value)
    for candidate in rows:
        if not isinstance(candidate, Candidate):
            raise TypeError("candidates must be Candidate values")
    return rows


def _cell(value: object) -> str:
    if value is None:
        return _NA
    if isinstance(value, tuple):
        return ",".join(_cell(item) for item in value)
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)
