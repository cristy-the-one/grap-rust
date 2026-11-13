"""Source records produced by readers.

Field meanings are fixed in docs/work-packages-2026-11.md, Part B1.
There was no shared SourceItem type elsewhere in the tree.
"""

from dataclasses import dataclass


@dataclass
class SourceLink:
    """A link the file format itself states."""

    type: str
    target_id: str
    role: str
    context: list[str]


@dataclass
class SourceItem:
    """One item from a source file."""

    source_id: str
    release: str | None
    item_id: str
    kind_hint: str
    text: str
    heading_path: list[str]
    attributes: dict[str, str]
    links: list[SourceLink]
    file: str
    sha256: str
