"""PUB-03: public-2, offline. The zip and the .npy files are built here.

No network. The opener is injected. public-1 tests are not modified.
"""

import hashlib
import io
import struct
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import fetch_public_corpus as corpus  # noqa: E402

_HF_URL = (
    "https://huggingface.co/datasets/commaai/comma2k19/resolve/"
    "4bff77c7254c654c28d4c2726186b4e825adccee/raw_data/Chunk_1.zip"
)
_REVISION = "4bff77c7254c654c28d4c2726186b4e825adccee"
_SEGMENT = "b0c9d2329ad1606b|2018-07-29--12-02-42/30"
_CDN = "https://cdn.example/chunk_1.zip"
_PINS = {
    "t": "861affb87fb9db76bcec78cb13fe7c8426fc8fe7004d926297df2cc53dce6e86",
    "address": "835fc971d5f0314ef07a0b6ee69075643034d5a2f4757e64832682f98ff94009",
    "data": "1e2a02361be7147dff6bdb224057f8e7e869070322ebaaeeebe59b76faa8412f",
    "src": "26a42bd07b169ae174c4d2efb12b776f008dd07dafd98f8a768ba110f5da585f",
}
_REQ_001 = (
    "ACC brake lamps",
    "While adaptive cruise commands a deceleration (PCM_CRUISE.ACC_BRAKING is 1), "
    "ESP_CONTROL.BRAKE_LIGHTS_ACC shall become 1 within 0.5 s.",
)
_REQ_002 = (
    "Cruise cancel on brake",
    "When the driver presses the brake pedal (PCM_CRUISE_2.BRAKE_PRESSED becomes 1) "
    "while adaptive cruise is active (PCM_CRUISE.CRUISE_ACTIVE is 1), "
    "PCM_CRUISE.CRUISE_ACTIVE shall become 0 within 0.5 s.",
)
_CHANGES = (
    '- REQ-PUB-001: named ESP_CONTROL.BRAKE_LIGHTS_ACC as the pedal stop lamps; '
    'the DBC comments it "brake lights when ACC commands decel". Found by run pub2-1.',
    "- REQ-PUB-002: mixed ACC deceleration with the driver's brake; "
    "braking cancels ACC. Found by run pub2-1.",
    "- Corrections follow the DBC's documentation or standard ACC behaviour, "
    "never how a recording behaves.",
    "- REQ-PUB-003 to 008 are unchanged.",
)
_REQIF = "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"
_XHTML = "http://www.w3.org/1999/xhtml"


def _npy(descr: str, count: int, payload: bytes) -> bytes:
    header = (
        "{'descr': '%s', 'fortran_order': False, 'shape': (%d,), }"
        % (descr, count)
    )
    text = header.encode("ascii")
    pad = (16 - ((10 + len(text) + 1) % 16)) % 16
    header_bytes = text + b" " * pad + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header_bytes)) + header_bytes + payload


def _member(name: str) -> str:
    return f"Chunk_1/{_SEGMENT}/processed_log/CAN/raw_can/{name}"


def _payloads() -> dict[str, bytes]:
    return {
        "t": _npy("<f8", 1, struct.pack("<d", 1.5)),
        "address": _npy("<i8", 1, struct.pack("<q", 548)),
        "data": _npy("|S4", 1, b"\x01\x02\x03\x04"),
        "src": _npy("<i8", 1, struct.pack("<q", 0)),
    }


def _zip_bytes(payloads: dict[str, bytes]) -> tuple[bytes, str]:
    unrelated = "Chunk_1/unrelated.bin"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_member("t"), payloads["t"])
        zf.writestr(_member("address"), payloads["address"])
        zf.writestr(unrelated, b"not-a-can-member" * 40)
        zf.writestr(_member("data"), payloads["data"])
        zf.writestr(_member("src"), payloads["src"])
    return buf.getvalue(), unrelated


def _span(blob: bytes, name: str) -> tuple[int, int]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        info = zf.getinfo(name)
    name_len, extra_len = struct.unpack_from("<HH", blob, info.header_offset + 26)
    end = info.header_offset + 30 + name_len + extra_len + info.compress_size
    return info.header_offset, end


class _Headers:
    def __init__(self, items: dict[str, str]):
        self._items = {key.lower(): value for key, value in items.items()}

    def get(self, key, default=None):
        return self._items.get(key.lower(), default)


class _Response:
    def __init__(self, status: int, url: str, headers: dict[str, str], body: bytes):
        self.status = status
        self.headers = _Headers(headers)
        self._url = url
        self._body = body
        self._pos = 0

    def geturl(self) -> str:
        return self._url

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = len(self._body) - self._pos
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _opener(blob: bytes):
    requests = []
    bodies = []

    def opener(request, timeout=None, data=None):
        requests.append(request)
        header = request.get_header("Range")
        if header is None or not header.startswith("bytes="):
            raise AssertionError(f"request has no Range header: {header!r}")
        start_s, end_s = header[len("bytes="):].split("-", 1)
        start, end = int(start_s), int(end_s)
        if request.get_method() == "HEAD":
            body = blob[:1]
            status = 206
            url = _CDN
            headers = {
                "Content-Length": "1",
                "Content-Range": f"bytes 0-0/{len(blob)}",
                "Accept-Ranges": "bytes",
            }
        else:
            body = blob[start:end + 1]
            status = 206
            url = request.full_url
            headers = {
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {start}-{end}/{len(blob)}",
            }
        bodies.append(body)
        return _Response(status, url, headers, body)

    return opener, requests, bodies


def _ranges(requests) -> list[tuple[int, int]]:
    spans = []
    for request in requests:
        header = request.get_header("Range")
        start_s, end_s = header[len("bytes="):].split("-", 1)
        spans.append((int(start_s), int(end_s) + 1))
    return spans


def _reqif_objects(text: str) -> dict[str, tuple[str, str]]:
    root = ElementTree.fromstring(text)
    found = {}
    for obj in root.findall(f".//{{{_REQIF}}}SPEC-OBJECT"):
        div = obj.find(f".//{{{_XHTML}}}div")
        found[obj.attrib["IDENTIFIER"]] = (
            obj.attrib["LONG-NAME"],
            "".join(div.itertext()),
        )
    return found


def test_public2_reader_returns_only_the_four_and_ranges_them():
    payloads = _payloads()
    blob, unrelated = _zip_bytes(payloads)
    forbidden = _span(blob, unrelated)
    opener, requests, bodies = _opener(blob)
    members, fetched = corpus.read_public2_can(opener)
    assert list(members) == ["t", "address", "data", "src"]
    for name in ("t", "address", "data", "src"):
        assert members[name] == payloads[name]
    assert requests
    assert [request.get_method() for request in requests].count("HEAD") == 1
    assert requests[0].get_method() == "HEAD"
    assert requests[0].full_url == _HF_URL
    assert all(request.get_method() == "GET" for request in requests[1:])
    assert all(request.full_url == _CDN for request in requests[1:])
    assert all(request.get_header("Range") for request in requests)
    for start, end in _ranges(requests):
        assert end <= forbidden[0] or start >= forbidden[1]
    assert fetched == sum(len(body) for body in bodies)
    assert fetched < len(blob)


def test_public2_sha256_mismatch_names_the_member_and_writes_nothing(tmp_path):
    payloads = _payloads()
    blob, _unrelated = _zip_bytes(payloads)
    opener, _requests, _bodies = _opener(blob)
    expected = {
        name: hashlib.sha256(payloads[name]).hexdigest()
        for name in ("t", "address", "data", "src")
    }
    expected["data"] = "0" * 64
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(corpus.CorpusError, match=r"member data"):
        corpus._fetch(out, "public-2", opener=opener, expected_sha256=expected)
    assert list(out.iterdir()) == []


def test_public2_budget_raises_and_writes_nothing(tmp_path):
    payloads = _payloads()
    blob, _unrelated = _zip_bytes(payloads)
    opener, _requests, _bodies = _opener(blob)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(corpus.CorpusError, match="budget"):
        corpus._fetch(out, "public-2", opener=opener, zip_budget=1)
    assert list(out.iterdir()) == []


def test_spec_v2_replaces_only_001_and_002():
    public1 = {
        ident: (title, sentence)
        for ident, title, sentence in corpus._REQUIREMENTS
    }
    public2 = {
        ident: (title, sentence)
        for ident, title, sentence in corpus._requirements_for("public-2")
    }
    assert public2["REQ-PUB-001"] == _REQ_001
    assert public2["REQ-PUB-002"] == _REQ_002
    for number in range(3, 9):
        ident = f"REQ-PUB-00{number}"
        assert public2[ident] == public1[ident]
    rendered = _reqif_objects(corpus._reqif(corpus._requirements_for("public-2")))
    rendered_public1 = _reqif_objects(corpus._reqif())
    assert rendered["REQ-PUB-001"] == _REQ_001
    assert rendered["REQ-PUB-002"] == _REQ_002
    assert rendered_public1["REQ-PUB-001"] == public1["REQ-PUB-001"]
    assert rendered_public1["REQ-PUB-002"] == public1["REQ-PUB-002"]
    for number in range(3, 9):
        ident = f"REQ-PUB-00{number}"
        assert rendered[ident] == rendered_public1[ident]
        assert rendered[ident] == public1[ident]
    assert list(rendered) == [f"REQ-PUB-00{number}" for number in range(1, 9)]


def test_public2_pins_match_the_card():
    assert corpus._PUBLIC2_ZIP_URL == _HF_URL
    assert corpus._PUBLIC2_REVISION == _REVISION
    assert corpus._PUBLIC2_SEGMENT == _SEGMENT
    assert corpus._PUBLIC2_MEMBER_SHA256 == _PINS
    assert corpus._PUBLIC2_BYTE_BUDGET == 64 * 1024 * 1024


def test_public2_sources_records_the_fetch_and_the_changes():
    text = corpus._sources_public2(
        downloads=[],
        counts={},
        span=1.25,
        kept=3,
        bytes_fetched=12345,
    )
    assert _HF_URL in text
    assert _REVISION in text
    assert _SEGMENT in text
    for digest in _PINS.values():
        assert digest in text
    assert "12345" in text
    assert "Changes from public-1" in text
    for line in _CHANGES:
        assert line in text


def test_main_accepts_set_public_2(monkeypatch, tmp_path):
    seen = {}

    def fake(out, corpus_set="public-1", **kwargs):
        seen["out"] = out
        seen["set"] = corpus_set

    monkeypatch.setattr(corpus, "_fetch", fake)
    out = tmp_path / "out"
    assert corpus.main(["--out", str(out), "--set", "public-2"]) == 0
    assert seen["set"] == "public-2"
    assert seen["out"] == out


def test_main_defaults_to_public_1(monkeypatch, tmp_path):
    seen = {}

    def fake(out, corpus_set="public-1", **kwargs):
        seen["set"] = corpus_set

    monkeypatch.setattr(corpus, "_fetch", fake)
    assert corpus.main(["--out", str(tmp_path)]) == 0
    assert seen["set"] == "public-1"
