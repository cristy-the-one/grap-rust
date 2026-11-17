"""GJ-05 step 0: the signal-loom DBC dump fixtures are copied byte for byte.

signal-loom owns the four files at 8be31cbf4c1ddc666185b93e2def74e12a340ebf.
This tree keeps that copy under tests/fixtures/loom_dbc_dump/ with a
SHA256SUMS. Every recorded line is checked here. A one-byte edit fails.
"""

import hashlib
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "loom_dbc_dump"
_SUMS = "SHA256SUMS"


def _recorded():
    rows = {}
    for line in (FIXTURES / _SUMS).read_text(encoding="utf-8").splitlines():
        if line == "":
            continue
        digest, name = line.split(maxsplit=1)
        rows[name.lstrip("*")] = digest
    return rows


def test_every_sha256sums_line_matches_the_copied_file():
    recorded = _recorded()
    copied = sorted(path.name for path in FIXTURES.iterdir() if path.name != _SUMS)
    assert sorted(recorded) == copied
    for name, digest in recorded.items():
        actual = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} is not the copied fixture"
