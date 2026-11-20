"""PUB-01: the public-corpus .npy reader and ASC writer.

No network. The arrays and the five frames are built in memory.
"""

import struct
import sys
from datetime import datetime
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from fetch_public_corpus import read_npy, write_asc  # noqa: E402


def _npy(descr: str, count: int, payload: bytes) -> bytes:
    """A numpy 1.0 file. The header is a dict literal, never a pickle."""
    header = (
        "{'descr': '%s', 'fortran_order': False, 'shape': (%d,), }"
        % (descr, count)
    )
    text = header.encode("ascii")
    pad = (16 - ((10 + len(text) + 1) % 16)) % 16
    header_bytes = text + b" " * pad + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header_bytes)) + header_bytes + payload


def test_read_npy_reads_a_plain_little_endian_int():
    payload = struct.pack("<3q", 1, -2, 3)
    assert read_npy(_npy("<i8", 3, payload)) == [1, -2, 3]


def test_read_npy_reads_fixed_size_bytes():
    payload = b"ab\x00\x00" + b"\x01\x02\x03\x04"
    assert read_npy(_npy("|S4", 2, payload)) == [b"ab\x00\x00", b"\x01\x02\x03\x04"]


def test_read_npy_refuses_an_object_dtype():
    with pytest.raises(ValueError, match=r"\|O"):
        read_npy(_npy("|O", 1, b""))


def test_write_asc_matches_the_expected_text_for_five_frames():
    frames = [
        (11.0, 0x7FF, bytes.fromhex("FFFF")),
        (10.0, 0x224, bytes.fromhex("0000000000000008")),
        (10.5, 0xB0, bytes.fromhex("0102")),
        (10.25, 0x800, bytes.fromhex("AA")),
        (10.1, 0x24, bytes.fromhex("00")),
    ]
    segment = "b0c9d2329ad1606b|2018-08-02--08-34-47/40"
    text = write_asc(frames, segment, datetime(2018, 8, 2, 8, 34, 47))
    expected = (
        "date Thu Aug  2 08:34:47.000 2018\n"
        "base hex timestamps absolute\n"
        "internal events logged\n"
        "// Converted from comma2k19 b0c9d2329ad1606b|2018-08-02--08-34-47/40. Public, MIT.\n"
        "0.000000 1 224 Rx d 8 00 00 00 00 00 00 00 08\n"
        "0.100000 1 24 Rx d 1 00\n"
        "0.250000 1 800x Rx d 1 AA\n"
        "0.500000 1 B0 Rx d 2 01 02\n"
        "1.000000 1 7FF Rx d 2 FF FF\n"
    )
    assert text == expected
