"""Exact source spans with soft size targets and explicit forced boundaries."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Chunk:
    start: int
    end: int
    forced_end: bool = False


_ABBREVIATION = re.compile(r"\b(?:nr|art|alin|lit|pct|str|dr|prof|ing|jud|vol|cca)\.$", re.I)
_HEADER = re.compile(r"\bArticolul\s+\d+\.$", re.I)


def boundaries(text):
    """Conservative structural/sentence boundaries; no linguistic truth claim."""
    ends = {0, len(text)}
    for match in re.finditer(r"\n[ \t]*\n|(?<!\w)Articolul\s+\d+", text):
        ends.add(match.start())
    for match in re.finditer(r"[.!?][\"”»)]*(?=\s|$)", text):
        prefix = text[max(0, match.start()-40):match.start()+1]
        if text[match.start()] == "." and (
                _ABBREVIATION.search(prefix) or _HEADER.search(prefix)
                or re.search(r"(?:\b[A-ZĂÂÎȘȚ]\.)+$", prefix)):
            continue
        rest = text[match.end():match.end()+40].lstrip()
        # A lowercase continuation may be an abbreviation or the next item in
        # one sentence. Keep it together rather than guessing a sentence break.
        if not rest or rest[0].isupper() or rest[0].isdigit() or rest[0] in "(–—-":
            ends.add(match.end())
    return sorted(ends)


def chunk_spans(text: str, target: int = 800):
    """Pack complete units toward target; split only units exceeding 3*target.

    Every chunk is a contiguous source slice. No overlapping text, synthesized
    headers or rewritten whitespace. Inter-chunk omissions are whitespace only.
    """
    if type(target) is not int or target < 1:
        raise ValueError("Chunk target must be a positive integer")
    hard = 3 * target
    points = boundaries(text)
    units = []
    for start, end in zip(points, points[1:]):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        while end-start > hard:
            cut = text.rfind(" ", start, start+hard+1)
            if cut <= start:
                cut = start+hard
            units.append(Chunk(start, cut, True))
            start = cut
            while start < end and text[start].isspace():
                start += 1
        if start < end:
            units.append(Chunk(start, end))
    packed = []
    for unit in units:
        if packed and not packed[-1].forced_end and unit.end-packed[-1].start <= target:
            previous = packed.pop()
            packed.append(Chunk(previous.start, unit.end, unit.forced_end))
        else:
            packed.append(unit)
    return packed
