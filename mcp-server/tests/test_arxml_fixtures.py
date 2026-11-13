"""AR-01: the ARXML fixtures are the exact bytes the reader spec was measured on."""

import hashlib
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arxml"


def _recorded():
    rows = {}
    for line in (FIXTURES / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        rows[name.lstrip("*")] = digest
    return rows


def test_nine_fixtures_are_recorded():
    assert len(_recorded()) == 9
    assert sorted(_recorded()) == sorted(p.name for p in FIXTURES.glob("*.arxml"))


def test_fixture_bytes_match_recorded_hashes():
    for name, digest in _recorded().items():
        actual = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} is not the measured fixture"
