"""Stage 1 of statements: units and certain labels (spec-statements.md §4).

The model is not called. A unit is a sentence, a list entry, or a table row.
Sentence breaks come from the existing splitter, after a number, a bracketed
identifier, or an abbreviation has been masked so the break cannot fall inside
it. Certain labels are the rows of ``rules.json``, first match wins. A marked
block wins over every later row, and a unit so labelled is not sent on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from grap_mcp.kee.chunking import boundaries
from grap_mcp.readers import SourceItem

from .schema import statement_kinds

_RULES_PATH = Path(__file__).with_name("rules.json")

# The same abbreviations the sentence splitter already keeps, plus the short
# English forms it does not know. Masking them makes the reused splitter keep
# the following word as well.
_ABBREV = re.compile(
    r"(?i)\b(?:e\.g|i\.e|etc|cf|vs|fig|sec|ch|eq|no|nr|art|alin|lit|pct|"
    r"str|dr|prof|ing|jud|vol|cca|mr|mrs)\."
)
_INITIALS = re.compile(r"(?:[A-Z]\.){2,}")
_NUMBER_DOT = re.compile(r"(?<=\d)\.(?=\d)")
_CAPTION_DOT = re.compile(
    r"(?im)^(?:table|figure|fig\.|caption)\s+\d+\."
)
_BRACKETED_ID = re.compile(r"\[[^\]\s]+\]")
_LIST = re.compile(
    r"^(?:[-*\u2022]|\d+[.)]|[a-z][.)]|\(\d+\)|\([A-Za-z]\))\s+\S"
)
_DASH_ROW = re.compile(
    r"^(?P<term>[A-Z][A-Za-z0-9_./-]*(?:\s+[A-Z][A-Za-z0-9_./-]*){0,3})"
    r"\s+[—–-]\s+\S"
)
_FUNCTION = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "see",
})
_WHEN = (
    "block", "modal-outside-block", "may-only", "regex",
    "glossary", "heading", "no-verb",
)

_LOADED: tuple[dict, tuple[_Rule, ...], frozenset[str], frozenset[str]] | None = None


@dataclass(frozen=True)
class _Rule:
    id: str
    kind: str
    when: str
    marker: str | None
    regex: re.Pattern[str] | None
    heading_regex: re.Pattern[str] | None


@dataclass(frozen=True)
class _Piece:
    start: int
    end: int
    table: bool


@dataclass(frozen=True)
class Unit:
    """One sentence, list entry, or table row, with a certain label if it has one.

    ``kind`` is None when no row matched: the unit goes on with no kind.
    ``origin`` is ``rule`` when a row matched. ``to_model`` is false when the
    row's kind is one this stage does not send on.
    """

    start: int
    end: int
    kind: str | None
    rule_id: str | None
    origin: str | None
    to_model: bool


def label_item(item: SourceItem) -> list[Unit]:
    """Split one requirement or text item and apply the certain labels."""
    if not isinstance(item, SourceItem):
        raise TypeError("label_item reads a SourceItem")
    meta, rules, verbs, modals = _load()
    allowed = meta["item_kinds"]
    if item.kind_hint not in allowed:
        raise ValueError(
            "statements are read from items of kind "
            + ", ".join(allowed)
            + f", not {item.kind_hint}"
        )
    pieces = _pieces(item.text)
    fixed = _heading_block(item.heading_path, rules)
    skip = set(meta["skip_model"])
    units: list[Unit] = []
    prev_end = 0
    carried: str | None = None
    for piece in pieces:
        if fixed is None and carried is not None and _blank(item.text, prev_end, piece.start):
            carried = None
        block = fixed if fixed is not None else carried
        if block is None:
            block = _open_block(item.text[piece.start:piece.end], rules)
            if block is not None:
                carried = block
        rule = _match(
            item.text[piece.start:piece.end], block, piece,
            item.heading_path, rules, verbs, modals,
        )
        units.append(_unit(piece, rule, skip))
        prev_end = piece.end
    return units


def _unit(piece: _Piece, rule: _Rule | None, skip: set[str]) -> Unit:
    if rule is None:
        return Unit(piece.start, piece.end, None, None, None, True)
    return Unit(
        piece.start,
        piece.end,
        rule.kind,
        rule.id,
        "rule",
        rule.kind not in skip,
    )


def _match(text, block, piece, heading_path, rules, verbs, modals) -> _Rule | None:
    for rule in rules:
        if rule.when == "block":
            if block is not None and block == rule.marker:
                return rule
            continue
        if block is not None:
            continue
        if rule.when == "modal-outside-block" and _hits(rule, text):
            return rule
        if rule.when == "may-only" and _modals(text, modals) == {"may"}:
            return rule
        if rule.when == "regex" and _hits(rule, text):
            return rule
        if rule.when == "glossary" and _glossary(text, heading_path, piece.table, verbs):
            return rule
        if rule.when == "heading" and _heading(text, verbs, modals):
            return rule
        if rule.when == "no-verb" and _no_verb(text, verbs, modals):
            return rule
    return None


def _hits(rule: _Rule, text: str) -> bool:
    return rule.regex is not None and rule.regex.search(text) is not None


def _modals(text: str, modals: frozenset[str]) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z]+", text)} & modals


def _heading(text: str, verbs: frozenset[str], modals: frozenset[str]) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if re.match(r"^#{1,6}\s+\S", stripped):
        return True
    words = re.findall(r"[A-Za-z]+", stripped)
    lowered = {word.lower() for word in words}
    if lowered & modals:
        return False
    if re.match(r"^\d+(?:\.\d+)*\s+\S", stripped) and stripped[-1] not in ".!?":
        return True
    if (
        not words
        or len(words) > 8
        or stripped[-1] in ".!?"
        or not stripped[0].isupper()
        or lowered & verbs
    ):
        return False
    return True


def _no_verb(text: str, verbs: frozenset[str], modals: frozenset[str]) -> bool:
    """A fragment with no known verb. A longer punctuated sentence is not certain."""
    words = re.findall(r"[A-Za-z]+", text)
    if not words or len(words) > 12:
        return False
    lowered = {word.lower() for word in words}
    if lowered & verbs or lowered & modals:
        return False
    return not (text.rstrip()[-1:] in ".!?" and len(words) > 2)


def _glossary(text, heading_path, is_table, verbs) -> bool:
    stripped = text.strip()
    dashed = _DASH_ROW.match(stripped)
    if dashed and _term_ok(dashed.group("term"), verbs):
        return True
    if not is_table or not any(re.search(r"(?i)\bglossary\b", part) for part in heading_path):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    cells = [cell for cell in cells if cell]
    if len(cells) < 2 or not _identifier(cells[0]):
        return False
    return _term_ok(cells[0], verbs) and bool(cells[1])


def _term_ok(term: str, verbs: frozenset[str]) -> bool:
    words = term.split()
    lowered = {word.lower() for word in words}
    return bool(words) and not (lowered & _FUNCTION or lowered & verbs)


def _identifier(token: str) -> bool:
    """Same shape as a bracketed or bare identifier: no spaces, and not a plain word."""
    if not token or any(ch.isspace() for ch in token):
        return False
    if any(ch in "_/." or ch.isdigit() for ch in token):
        return True
    return any(ch.isupper() for ch in token[1:])


def _heading_block(heading_path, rules: tuple[_Rule, ...]) -> str | None:
    for segment in reversed(heading_path):
        head = segment.strip()
        for rule in rules:
            if rule.when == "block" and rule.heading_regex and rule.heading_regex.search(head):
                return rule.marker
    return None


def _open_block(text: str, rules: tuple[_Rule, ...]) -> str | None:
    stripped = text.strip()
    for rule in rules:
        if rule.when == "block" and rule.regex and rule.regex.search(stripped):
            return rule.marker
    return None


def _blank(text: str, prev_end: int, start: int) -> bool:
    return start > prev_end and re.search(r"\n\s*\n", text[prev_end:start]) is not None


def _pieces(text: str) -> list[_Piece]:
    lines = list(_lines(text))
    taken: list[tuple[int, int, bool, bool]] = []
    index = 0
    while index < len(lines):
        start, end, raw = lines[index]
        stripped = raw.strip()
        if stripped and stripped.count("|") >= 2:
            if re.fullmatch(r"[\s|:\-]+", stripped):
                taken.append((start, end, False, True))
            else:
                taken.append((*_trim(text, start, end), True, True))
            index += 1
            continue
        if stripped and _LIST.match(stripped):
            entry_end = end
            nxt = index + 1
            while nxt < len(lines):
                _ns, ne, nraw = lines[nxt]
                nstripped = nraw.strip()
                if (
                    not nstripped
                    or _LIST.match(nstripped)
                    or nstripped.count("|") >= 2
                    or nraw[:1] not in " \t"
                ):
                    break
                entry_end = ne
                nxt += 1
            taken.append((*_trim(text, start, entry_end), True, False))
            index = nxt
            continue
        index += 1

    pieces: list[_Piece] = []
    cursor = 0
    for start, end, is_unit, is_table in taken:
        if start > cursor:
            pieces.extend(_sentences(text, cursor, start))
        if is_unit and start < end:
            pieces.append(_Piece(start, end, is_table))
        cursor = max(cursor, end)
    if cursor < len(text):
        pieces.extend(_sentences(text, cursor, len(text)))
    return pieces


def _sentences(text: str, start: int, end: int) -> list[_Piece]:
    chunk = text[start:end]
    if not chunk.strip():
        return []
    points = boundaries(_mask(chunk))
    pieces = []
    for left, right in zip(points, points[1:]):
        s, e = _trim(chunk, left, right)
        if s < e:
            pieces.append(_Piece(start + s, start + e, False))
    return pieces


def _mask(text: str) -> str:
    chars = list(text)

    def hide(span: tuple[int, int]) -> None:
        for index in range(span[0], span[1]):
            if chars[index] in ".!?":
                chars[index] = "·"

    for pattern in (_ABBREV, _INITIALS, _NUMBER_DOT, _CAPTION_DOT, _BRACKETED_ID):
        for match in pattern.finditer(text):
            hide(match.span())
    return "".join(chars)


def _lines(text: str):
    offset = 0
    for raw in text.splitlines(keepends=True):
        yield offset, offset + len(raw), raw
        offset += len(raw)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _forms(verb: str) -> set[str]:
    """Finite forms only. A participle is often a noun, so it is not a verb here."""
    forms = {verb}
    if verb.endswith("e") and not verb.endswith("ee"):
        forms.update({verb + "s", verb + "d"})
    elif verb.endswith("y") and len(verb) > 1 and verb[-2] not in "aeiou":
        forms.update({verb[:-1] + "ies", verb[:-1] + "ied"})
    elif verb.endswith("y"):
        forms.update({verb + "s", verb + "ed"})
    else:
        forms.update({verb + "s", verb + "ed"})
    return forms


def _load():
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    raw = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
    kinds = set(statement_kinds())
    rules = tuple(_rule(row, kinds) for row in raw["rules"])
    ids = [rule.id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("a rule id is repeated")
    skip = raw["skip_model"]
    if any(kind not in kinds for kind in skip):
        raise ValueError("a skip-model kind is not in the schema file")
    verbs = set(raw["irregulars"])
    for verb in raw["verbs"]:
        verbs.update(_forms(verb))
    _LOADED = (raw, rules, frozenset(verbs), frozenset(raw["modals"]))
    return _LOADED


def _rule(row: dict, kinds: set[str]) -> _Rule:
    when = row["when"]
    if when not in _WHEN:
        raise ValueError(f"unknown rule {row['id']}")
    if row["kind"] not in kinds:
        raise ValueError("a rule kind is not in the schema file")
    return _Rule(
        id=row["id"],
        kind=row["kind"],
        when=when,
        marker=row.get("marker"),
        regex=_compiled(row.get("regex")),
        heading_regex=_compiled(row.get("heading_regex")),
    )


def _compiled(pattern: str | None) -> re.Pattern[str] | None:
    if pattern is None:
        return None
    return re.compile(pattern)
