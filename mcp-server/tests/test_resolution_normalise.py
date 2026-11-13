"""E-13: Stage 0 normalisation (spec-entity-resolution.md).

Comparison only. The text stored on a mention is not rewritten.

The table is the card's three checks: a bracketed identifier, a call-shaped
identifier, and both encodings of a Romanian letter. The other rows are the
rest of Stage 0: case-fold, Unicode composition, whitespace, and the other
letter that has the same two encodings. Cedilla and comma-below are written
as code points so a save cannot fold one into the other.
"""

import pytest

from grap_mcp.kee.resolution.normalise import normalise

# ş U+015F and ș U+0219 are the two encodings of the same Romanian letter.
S_CEDILLA = "\u015f"
S_COMMA = "\u0219"
# ţ U+0163 and ț U+021B, the other pair. ă U+0103 is a different letter.
T_CEDILLA = "\u0163"
T_COMMA = "\u021b"
A_BREVE = "\u0103"

# (label, text as written, comparison form)
CASES = (
    ("brackets", "[SWS_Com_00123]", "sws com 00123"),
    ("bare-id", "SWS_Com_00123", "sws com 00123"),
    ("call", "Com_SendSignal()", "com send signal"),
    ("call-stem", "Com_SendSignal", "com send signal"),
    ("s-cedilla", S_CEDILLA, S_COMMA),
    ("s-comma", S_COMMA, S_COMMA),
    ("s-cedilla-capital", "\u015e", S_COMMA),
    ("s-decomposed-cedilla", "s\u0327", S_COMMA),
    ("s-decomposed-comma", "s\u0326", S_COMMA),
    ("t-cedilla", T_CEDILLA, T_COMMA),
    ("t-comma", T_COMMA, T_COMMA),
    (
        "word-and-breve",
        "  Na" + T_CEDILLA + "ional" + A_BREVE + "  ",
        "na" + T_COMMA + "ional" + A_BREVE,
    ),
    ("whitespace", "a \t b", "a b"),
    ("id-cedilla", "Com_\u015eemnal", "com " + S_COMMA + "emnal"),
)


@pytest.mark.parametrize(
    "text,expected",
    [(text, expected) for _label, text, expected in CASES],
    ids=[label for label, _text, _expected in CASES],
)
def test_stage0_normalise(text, expected):
    """Bracketed ids, call-shaped ids, and both Romanian encodings match."""
    assert normalise(text) == expected
