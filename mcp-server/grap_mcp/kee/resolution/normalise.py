"""Stage 0 of entity resolution (spec-entity-resolution.md).

The string returned here is for comparison. A mention keeps the text as it
was written; this function does not write it back.

Order for every name: Unicode NFC, case-fold, then the cedilla and
comma-below encodings of the same Romanian letter become one letter, then
whitespace runs become a single space. An identifier-like name is split
before the case-fold, because a case-fold would erase the case changes the
split has to see.
"""

from __future__ import annotations

import re
import unicodedata

# Case-fold has already run, so the table is the lowercase pair.
# ş U+015F and ţ U+0163 are the legacy cedilla encodings.
# ș U+0219 and ț U+021B are the comma-below letters. Same letter, either way.
_SAME_LETTER = str.maketrans({
    "\u015f": "\u0219",
    "\u0163": "\u021b",
})

_BRACKETS = {"[": "]", "(": ")", "{": "}"}
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Return the comparison form of ``text``.

    Identifier-like names also lose one surrounding bracket pair and a
    trailing ``()``. Underscores and case changes become token breaks. The
    tokens are joined with one space.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    form = unicodedata.normalize("NFC", text)
    if _identifier_like(form):
        core = _strip_brackets(_strip_call(form))
        tokens = [_fold(part) for part in _split_identifier(core)]
        return " ".join(token for token in tokens if token)
    return _fold(form)


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFC", text).casefold().translate(_SAME_LETTER)
    return _SPACE.sub(" ", folded).strip()


def _strip_call(text: str) -> str:
    if text.endswith("()"):
        return text[:-2]
    return text


def _strip_brackets(text: str) -> str:
    if len(text) >= 2 and _BRACKETS.get(text[0]) == text[-1]:
        return text[1:-1]
    return text


def _identifier_like(text: str) -> bool:
    """Whether the extra identifier steps apply.

    The core is the text after one trailing ``()`` and one surrounding
    bracket pair. It is identifier-like when that core has no whitespace and
    any of those marks was present, or the core contains an underscore or an
    uppercase letter after its first character. A phrase is left whole.
    """
    core = _strip_brackets(_strip_call(text))
    if core == "" or any(ch.isspace() for ch in core):
        return False
    if core != text or "_" in core:
        return True
    return any(ch.isupper() for ch in core[1:])


def _split_identifier(text: str) -> list[str]:
    """Split on underscores and case changes. Anything else stays in the token.

    ``HTTPResponse`` breaks before the last capital of a run when a lowercase
    letter follows, so the acronym stays one token. A lower-to-upper break
    splits ``SendSignal``.
    """
    parts: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            parts.append("".join(buf))
            buf.clear()

    for index, ch in enumerate(text):
        if ch == "_":
            flush()
            continue
        if buf:
            prev = buf[-1]
            nxt = text[index + 1] if index + 1 < len(text) else ""
            if (prev.islower() and ch.isupper()) or (
                prev.isupper() and ch.isupper() and nxt.islower()
            ):
                flush()
        buf.append(ch)
    flush()
    return parts
