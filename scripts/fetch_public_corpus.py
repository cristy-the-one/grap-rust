#!/usr/bin/env python3
"""Download a public rehearsal corpus and write it outside the repository.

    python scripts/fetch_public_corpus.py --out /tmp/grap-corpus/public-1

Writes ``bus.dbc``, ``drive.asc``, ``spec.reqif`` and ``SOURCES.md`` under
``--out``. Refuses an ``--out`` that sits inside a git repository. Downloads
nothing into the repository. Uses only the standard library.

The DBC is commaai/opendbc (MIT) at one pinned commit, assembled the way
that repository's generator assembles ``toyota_new_mc_pt_generated`` for the
non-hybrid Toyota RAV4. The recording is one comma2k19 example segment
(MIT) at one pinned commit. ``spec.reqif`` is written from the sentences
below: there is no public ReqIF for this car.

REQ-PUB-007 and REQ-PUB-008 are plain words only. They are the control:
they name no DBC id and are expected to fail grounding.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_OPENDC_REPO = "commaai/opendbc"
_OPENDC_COMMIT = "79ddcc7bb72d557fc5b20d038c0fad9c9d423446"
_COMMA2K19_REPO = "commaai/comma2k19"
_COMMA2K19_COMMIT = "4c7f1a6e1957745beadc1def0e7225f559b09a2a"
_SEGMENT = "b0c9d2329ad1606b|2018-08-02--08-34-47/40"
_POWERTRAIN_SRC = 0
# Frame 548 is BRAKE_MODULE in toyota_new_mc_pt (non-hybrid). Frame 550 is
# the hybrid BRAKE_MODULE. The example dongle is the RAV4, so 548 must be
# present on the powertrain bus and 550 must not be the brake module there.
_RAV4_FRAME = 548
_HYBRID_BRAKE_FRAME = 550
# PCS_HUD (1041) has overlapping signals PCS_TEMP and SET_ME_X10. The strict
# DBC reader rejects the generated file until that one message is left out.
_DROP_FRAME = "1041"
_DBC_FILES = (
    "_toyota_2017.dbc",
    "_toyota_adas_standard.dbc",
    "toyota_new_mc_pt.dbc",
)
_DBC_DIR = "opendbc/dbc/generator/toyota"
_CAN_NAMES = ("t", "address", "data", "src")
_IMPORT = re.compile(r'CM_ "IMPORT (.*?)";\n')
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
_NUMERIC = {
    ("<", "f", 2): "e",
    ("<", "f", 4): "f",
    ("<", "f", 8): "d",
    ("<", "i", 1): "b",
    ("<", "i", 2): "h",
    ("<", "i", 4): "i",
    ("<", "i", 8): "q",
    ("<", "u", 1): "B",
    ("<", "u", 2): "H",
    ("<", "u", 4): "I",
    ("<", "u", 8): "Q",
    ("|", "i", 1): "b",
    ("|", "u", 1): "B",
}

# Six requirements name two signals each, Message.Signal, as written in the
# DBC. The last two are the plain-word control. See the module docstring.
_REQUIREMENTS = (
    (
        "REQ-PUB-001",
        "Service brake lamps",
        "When the driver applies the service brake, BRAKE_MODULE.BRAKE_PRESSED "
        "shall become 1 and the stop lamps reported by ESP_CONTROL.BRAKE_LIGHTS_ACC "
        "shall light.",
    ),
    (
        "REQ-PUB-002",
        "Cruise brake command",
        "While adaptive cruise commands a deceleration, PCM_CRUISE.ACC_BRAKING "
        "shall be 1 in the same braking period that PCM_CRUISE_2.BRAKE_PRESSED "
        "reports the brake switch.",
    ),
    (
        "REQ-PUB-003",
        "Gear engagement",
        "Drive shall be engaged only in a forward gear: GEAR_PACKET.DRIVE_ENGAGED "
        "shall be 1 only when GEAR_PACKET.GEAR is a forward position.",
    ),
    (
        "REQ-PUB-004",
        "Turn indicator",
        "A command from the turn stalk BLINKERS_STATE.BLINKER_BUTTON_PRESSED "
        "shall be followed by the selected side on BLINKERS_STATE.TURN_SIGNALS.",
    ),
    (
        "REQ-PUB-005",
        "Wheel speeds",
        "While the vehicle is driving in a straight line, WHEEL_SPEEDS.WHEEL_SPEED_FL "
        "and WHEEL_SPEEDS.WHEEL_SPEED_FR shall stay consistent with each other.",
    ),
    (
        "REQ-PUB-006",
        "Beam lamps",
        "The dipped-beam lamps LIGHT_STALK.LOW_BEAM and the main-beam lamps "
        "LIGHT_STALK.HIGH_BEAM shall follow the stalk, and the main beam shall "
        "not stay on against oncoming traffic.",
    ),
    (
        "REQ-PUB-007",
        "Brake lamps in words",
        "When the driver presses the brake pedal, the brake lamps shall light promptly.",
    ),
    (
        "REQ-PUB-008",
        "Indicator in words",
        "Moving the turn stalk shall switch on the direction indicator on the selected side.",
    ),
)


class CorpusError(Exception):
    """A source could not be verified or read. Nothing is substituted."""


def read_npy(data: bytes) -> list:
    """Read one plain little-endian numpy 1.0 array. Never unpickles.

    Supports little-endian float, int and uint, and ``|S<n>`` bytes.
    Anything else, including an object dtype or ``allow_pickle``, raises
    ``ValueError`` naming the dtype.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("npy data must be bytes")
    if len(data) < 10 or data[:6] != b"\x93NUMPY":
        raise ValueError("not a numpy .npy file")
    major = data[6]
    minor = data[7]
    if major != 1:
        raise ValueError(f"refusing numpy version {major}.{minor}")
    header_len = struct.unpack_from("<H", data, 8)[0]
    header_end = 10 + header_len
    if len(data) < header_end:
        raise ValueError("truncated numpy header")
    header = _npy_header(data[10:header_end])
    descr = header.get("descr")
    if header.get("allow_pickle"):
        raise ValueError(f"refusing dtype {descr!r}: allow_pickle")
    count, item = _plain_dtype(descr, header.get("shape"), header.get("fortran_order"))
    payload = data[header_end:]
    need = count * item[1]
    if len(payload) < need:
        raise ValueError(
            f"truncated numpy array: have {len(payload)} bytes, need {need} for dtype {descr!r}"
        )
    if item[0] == "S":
        width = item[1]
        return [bytes(payload[index * width:(index + 1) * width]) for index in range(count)]
    values: list = []
    offset = 0
    remaining = count
    while remaining:
        step = min(remaining, 4096)
        values.extend(struct.unpack_from("<" + item[0] * step, payload, offset))
        offset += step * item[1]
        remaining -= step
    return values


def write_asc(frames, segment: str, when: datetime) -> str:
    """Vector ASC in the shape loom-judge already accepts. Channel 1 only.

    ``frames`` is ``(t, arbitration_id, data)``. ``t`` is seconds. The file's
    timestamps are seconds from the first frame, six digits, in time order.
    An id above ``0x7FF`` gets an ``x`` suffix.
    """
    ordered = sorted(frames, key=lambda frame: (frame[0],))
    if not ordered:
        raise ValueError("no frames to write")
    first = ordered[0][0]
    lines = [
        f"date {_asc_date(when)}",
        "base hex timestamps absolute",
        "internal events logged",
        f"// Converted from comma2k19 {segment}. Public, MIT.",
    ]
    for t, can_id, data in ordered:
        payload = bytes(data)
        body = " ".join(f"{byte:02X}" for byte in payload)
        lines.append(
            f"{t - first:.6f} 1 {_asc_id(int(can_id))} Rx d {len(payload)} {body}"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="Directory outside any git repo")
    args = parser.parse_args(argv)
    out = args.out
    repo = _git_repo(out)
    if repo is not None:
        print(f"refusing --out {out}: it is inside the git repository {repo}", file=sys.stderr)
        return 2
    try:
        _fetch(out)
    except CorpusError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _fetch(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    downloads = []
    downloads.append(_licence(_OPENDC_REPO, _OPENDC_COMMIT))
    downloads.append(_licence(_COMMA2K19_REPO, _COMMA2K19_COMMIT))
    dbc_files = {}
    for name in _DBC_FILES:
        item = _get(
            _OPENDC_REPO,
            _OPENDC_COMMIT,
            f"{_DBC_DIR}/{name}",
            "MIT",
        )
        downloads.append(item)
        dbc_files[name] = item["data"].decode("utf-8")
    arrays = {}
    for name in _CAN_NAMES:
        path = f"Example_1/{_SEGMENT}/processed_log/CAN/raw_can/{name}"
        item = _get(_COMMA2K19_REPO, _COMMA2K19_COMMIT, path, "MIT")
        downloads.append(item)
        arrays[name] = read_npy(item["data"])
    frames, counts = _powertrain_frames(arrays)
    bus = _assemble_dbc(dbc_files)
    asc = write_asc(frames, _SEGMENT, _segment_when(_SEGMENT))
    reqif = _reqif()
    (out / "bus.dbc").write_text(bus, encoding="utf-8")
    (out / "drive.asc").write_text(asc, encoding="utf-8")
    (out / "spec.reqif").write_text(reqif, encoding="utf-8")
    span = frames[-1][0] - frames[0][0]
    (out / "SOURCES.md").write_text(
        _sources(downloads, counts, span, len(frames)),
        encoding="utf-8",
    )
    total = len(arrays["t"])
    print(
        f"frames={len(frames)}/{total} src={_POWERTRAIN_SRC} span_s={span!r} "
        f"id548={counts.get(_RAV4_FRAME, 0)} id550={counts.get(_HYBRID_BRAKE_FRAME, 0)}"
    )
    print(f"wrote {out / 'bus.dbc'}")
    print(f"wrote {out / 'drive.asc'}")
    print(f"wrote {out / 'spec.reqif'}")
    print(f"wrote {out / 'SOURCES.md'}")


def _npy_header(raw: bytes) -> dict:
    try:
        text = raw.decode("latin-1").strip()
        header = ast.literal_eval(text)
    except (ValueError, SyntaxError, UnicodeError) as exc:
        raise ValueError(f"refusing numpy header: {exc}") from exc
    if not isinstance(header, dict):
        raise ValueError(f"refusing numpy header {type(header).__name__}")
    return header


def _plain_dtype(descr, shape, fortran_order):
    if not isinstance(descr, str):
        raise ValueError(f"refusing dtype {descr!r}")
    if descr in {"O", "|O", "<O", ">O", "=O", "object"} or descr.endswith("O"):
        raise ValueError(f"refusing dtype {descr!r}")
    if fortran_order not in (True, False):
        raise ValueError(f"refusing dtype {descr!r}: fortran_order {fortran_order!r}")
    if not isinstance(shape, tuple) or len(shape) != 1 or not isinstance(shape[0], int):
        raise ValueError(f"refusing dtype {descr!r}: shape {shape!r}")
    if shape[0] < 0:
        raise ValueError(f"refusing dtype {descr!r}: shape {shape!r}")
    endian, code, size = _split_descr(descr)
    if code == "S":
        if size < 1:
            raise ValueError(f"refusing dtype {descr!r}")
        return shape[0], ("S", size)
    fmt = _NUMERIC.get((endian, code, size))
    if fmt is None:
        raise ValueError(f"refusing dtype {descr!r}")
    return shape[0], (fmt, size)


def _split_descr(descr: str):
    if descr[:1] not in "<>=|":
        raise ValueError(f"refusing dtype {descr!r}")
    endian = descr[0]
    body = descr[1:]
    code = []
    digits = []
    for char in body:
        if char.isdigit():
            digits.append(char)
        elif digits:
            raise ValueError(f"refusing dtype {descr!r}")
        else:
            code.append(char)
    if not code or not digits:
        raise ValueError(f"refusing dtype {descr!r}")
    return endian, "".join(code), int("".join(digits))


def _asc_date(when: datetime) -> str:
    msec = when.microsecond // 1000
    return (
        f"{_WEEKDAYS[when.weekday()]} {_MONTHS[when.month - 1]} {when.day:2d} "
        f"{when.hour:02d}:{when.minute:02d}:{when.second:02d}.{msec:03d} {when.year:04d}"
    )


def _asc_id(can_id: int) -> str:
    if can_id < 0:
        raise ValueError(f"refusing negative CAN id {can_id}")
    text = f"{can_id:X}"
    if can_id > 0x7FF:
        text += "x"
    return text


def _segment_when(segment: str) -> datetime:
    stamp = segment.split("|", 1)[1].split("/", 1)[0]
    return datetime.strptime(stamp, "%Y-%m-%d--%H-%M-%S")


def _powertrain_frames(arrays: dict) -> tuple[list, dict]:
    times = arrays["t"]
    addresses = arrays["address"]
    payloads = arrays["data"]
    srcs = arrays["src"]
    total = len(times)
    if not (len(addresses) == len(payloads) == len(srcs) == total):
        raise CorpusError(
            "raw_can arrays differ in length: "
            f"t={len(times)} address={len(addresses)} data={len(payloads)} src={len(srcs)}"
        )
    counts: dict[int, int] = {}
    frames = []
    for index in range(total):
        if srcs[index] != _POWERTRAIN_SRC:
            continue
        can_id = int(addresses[index])
        counts[can_id] = counts.get(can_id, 0) + 1
        frames.append((float(times[index]), can_id, bytes(payloads[index])))
    rav4 = counts.get(_RAV4_FRAME, 0)
    hybrid = counts.get(_HYBRID_BRAKE_FRAME, 0)
    if rav4 == 0 or hybrid > rav4:
        raise CorpusError(
            "recording does not match the non-hybrid RAV4 DBC: "
            f"src {_POWERTRAIN_SRC} has frame {_RAV4_FRAME} x{rav4} "
            f"and frame {_HYBRID_BRAKE_FRAME} x{hybrid}"
        )
    frames.sort(key=lambda frame: frame[0])
    return frames, counts


def _assemble_dbc(files: dict[str, str]) -> str:
    filename = "toyota_new_mc_pt.dbc"
    source = files[filename]
    includes = _IMPORT.findall(source)
    missing = [name for name in includes if name not in files]
    if missing:
        raise CorpusError(f"DBC import missing: {', '.join(missing)}")
    parts = ['CM_ "AUTOGENERATED FILE, DO NOT EDIT";\n']
    for include in includes:
        parts.append(f'\n\nCM_ "Imported file {include} starts here";\n')
        parts.append(files[include])
        if not files[include].endswith("\n"):
            parts.append("\n")
    parts.append(f'\nCM_ "{filename} starts here";\n')
    parts.append(_IMPORT.sub("", source))
    return _drop_frame("".join(parts), _DROP_FRAME)


def _drop_frame(text: str, frame_id: str) -> str:
    kept = []
    skipping = False
    for line in text.splitlines(keepends=True):
        if line.startswith("BO_ "):
            parts = line.split()
            if len(parts) > 1 and parts[1] == frame_id:
                skipping = True
                continue
        if skipping:
            if line[:1] in " \t" or line.strip() == "":
                continue
            skipping = False
        head = line.split()
        if head[:2] == ["VAL_", frame_id] or head[:3] == ["CM_", "SG_", frame_id]:
            continue
        kept.append(line)
    return "".join(kept)


def _reqif() -> str:
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    objects = []
    for ident, title, sentence in _REQUIREMENTS:
        text = _xml(sentence)
        name = _xml(title)
        objects.append(
            f'        <SPEC-OBJECT IDENTIFIER="{ident}" LONG-NAME="{name}">\n'
            f"          <TYPE><SPEC-OBJECT-TYPE-REF>_sot_req</SPEC-OBJECT-TYPE-REF></TYPE>\n"
            f"          <VALUES>\n"
            f"            <ATTRIBUTE-VALUE-XHTML>\n"
            f"              <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>_ad_text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>\n"
            f"              <THE-VALUE><xhtml:div>{text}</xhtml:div></THE-VALUE>\n"
            f"            </ATTRIBUTE-VALUE-XHTML>\n"
            f'            <ATTRIBUTE-VALUE-STRING THE-VALUE="{ident}">\n'
            f"              <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>_ad_foreign</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION>\n"
            f"            </ATTRIBUTE-VALUE-STRING>\n"
            f"          </VALUES>\n"
            f"        </SPEC-OBJECT>"
        )
    body = "\n".join(objects)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        "  <THE-HEADER>\n"
        '    <REQ-IF-HEADER IDENTIFIER="_header">\n'
        f"      <CREATION-TIME>{created}</CREATION-TIME>\n"
        "      <REQ-IF-TOOL-ID>grap-fetch-public-corpus</REQ-IF-TOOL-ID>\n"
        "      <REQ-IF-VERSION>1.0</REQ-IF-VERSION>\n"
        "      <SOURCE-TOOL-ID>grap-fetch-public-corpus</SOURCE-TOOL-ID>\n"
        "      <TITLE>Public corpus spec</TITLE>\n"
        "    </REQ-IF-HEADER>\n"
        "  </THE-HEADER>\n"
        "  <CORE-CONTENT>\n"
        "    <REQ-IF-CONTENT>\n"
        "      <DATATYPES>\n"
        '        <DATATYPE-DEFINITION-STRING IDENTIFIER="_dt_string" LONG-NAME="String" MAX-LENGTH="1000"/>\n'
        '        <DATATYPE-DEFINITION-XHTML IDENTIFIER="_dt_xhtml" LONG-NAME="XHTML"/>\n'
        "      </DATATYPES>\n"
        "      <SPEC-TYPES>\n"
        '        <SPEC-OBJECT-TYPE IDENTIFIER="_sot_req" LONG-NAME="Requirement">\n'
        "          <SPEC-ATTRIBUTES>\n"
        '            <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="_ad_text" LONG-NAME="ReqIF.Text">\n'
        "              <TYPE><DATATYPE-DEFINITION-XHTML-REF>_dt_xhtml</DATATYPE-DEFINITION-XHTML-REF></TYPE>\n"
        "            </ATTRIBUTE-DEFINITION-XHTML>\n"
        '            <ATTRIBUTE-DEFINITION-STRING IDENTIFIER="_ad_foreign" LONG-NAME="ReqIF.ForeignID">\n'
        "              <TYPE><DATATYPE-DEFINITION-STRING-REF>_dt_string</DATATYPE-DEFINITION-STRING-REF></TYPE>\n"
        "            </ATTRIBUTE-DEFINITION-STRING>\n"
        "          </SPEC-ATTRIBUTES>\n"
        "        </SPEC-OBJECT-TYPE>\n"
        "      </SPEC-TYPES>\n"
        "      <SPEC-OBJECTS>\n"
        f"{body}\n"
        "      </SPEC-OBJECTS>\n"
        "    </REQ-IF-CONTENT>\n"
        "  </CORE-CONTENT>\n"
        "</REQ-IF>\n"
    )


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _licence(repo: str, commit: str) -> dict:
    item = _get(repo, commit, "LICENSE", "MIT")
    text = item["data"].decode("utf-8")
    # The MIT licence is the grant plus the warranty disclaimer. opendbc's
    # file has no "MIT License" heading; comma2k19's file does. Either way
    # the grant text is what was checked.
    grant = "Permission is hereby granted, free of charge"
    warranty = 'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND'
    if grant not in text or warranty not in text:
        first = text.splitlines()[0] if text.splitlines() else ""
        raise CorpusError(
            f"{repo}@{commit} LICENSE is not the MIT grant; first line: {first!r}"
        )
    copyright_line = next(
        (line for line in text.splitlines() if line.startswith("Copyright")),
        "",
    )
    if copyright_line == "":
        raise CorpusError(f"{repo}@{commit} LICENSE has no copyright line")
    item["copyright"] = copyright_line
    return item


def _get(repo: str, commit: str, path: str, licence: str) -> dict:
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{urllib.parse.quote(path, safe='/')}"
    request = urllib.request.Request(url, headers={"User-Agent": "grap-fetch-public-corpus"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise CorpusError(f"download failed: {url} HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise CorpusError(f"download failed: {url} {exc.reason}") from exc
    if status != 200:
        raise CorpusError(f"download failed: {url} HTTP {status}")
    return {
        "url": url,
        "repo": repo,
        "commit": commit,
        "path": path,
        "licence": licence,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data": data,
    }


def _sources(downloads: list[dict], counts: dict, span: float, kept: int) -> str:
    lines = [
        "# Public corpus sources",
        "",
        "Downloaded by `scripts/fetch_public_corpus.py`. Nothing here is committed.",
        "Each downloaded file is listed with the URL that was fetched, the pinned",
        "commit, the licence checked in that commit's LICENSE file, and the sha256",
        "of the downloaded bytes.",
        "",
        f"Segment `{_SEGMENT}` is Example_1 of commaai/comma2k19. The route date in",
        "the segment name is 2018-08-02. opendbc at the pinned commit maps the",
        "non-hybrid Toyota RAV4 to `toyota_new_mc_pt_generated`. `drive.asc` keeps",
        f"src {_POWERTRAIN_SRC} only. That bus carries frame {_RAV4_FRAME}",
        f"({counts.get(_RAV4_FRAME, 0)} frames) and frame {_HYBRID_BRAKE_FRAME}",
        f"({counts.get(_HYBRID_BRAKE_FRAME, 0)} frames).",
        f"Frames written: {kept}. Span from the first kept frame to the last: {span!r} seconds.",
        "",
        "`bus.dbc` is the generated file: `_toyota_2017.dbc`, then",
        "`_toyota_adas_standard.dbc`, then `toyota_new_mc_pt.dbc` with its IMPORT",
        f"lines removed. Message {_DROP_FRAME} (PCS_HUD) is omitted because signals",
        "PCS_TEMP and SET_ME_X10 overlap and the strict DBC reader rejects the file.",
        "`spec.reqif` is written by this script from sentences about behaviour the",
        "DBC names. There is no public ReqIF for this car.",
        "",
    ]
    for item in downloads:
        lines.extend([
            f"- url: {item['url']}",
            f"  repo path: {item['path']}",
            f"  commit: {item['commit']}",
            f"  licence: {item['licence']}"
            + (f" ({item['copyright']})" if item.get("copyright") else ""),
            f"  sha256: {item['sha256']}",
            "",
        ])
    return "\n".join(lines)


def _git_repo(path: Path) -> Path | None:
    current = path if path.is_absolute() else Path.cwd() / path
    current = current.resolve()
    if current.is_file():
        current = current.parent
    for parent in (current, *current.parents):
        if (parent / ".git").exists():
            return parent
    return None


if __name__ == "__main__":
    raise SystemExit(main())
