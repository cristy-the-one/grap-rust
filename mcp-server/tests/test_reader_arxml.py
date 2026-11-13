"""AR-07: the inventory prints tags, DEST values, roles, and iref shapes only.

system-4.2.arxml is not in this tree (AR-01 has not landed). The file below
stands in for that check: it has SHORT-NAMEs, reference paths, and values.
None of those strings may appear in the output.
"""

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import arxml_inventory  # noqa: E402


_WITH_REFS = """\
<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>WidgetAlpha</SHORT-NAME>
      <ELEMENTS>
        <I-SIGNAL UUID="uuid-marker-9f3a">
          <SHORT-NAME>SignalZebra</SHORT-NAME>
          <LENGTH>77</LENGTH>
          <I-SIGNAL-REF DEST="I-SIGNAL" BASE="BaseLabelQuartz">/WidgetAlpha/SignalZebra</I-SIGNAL-REF>
          <DESC>PlainWordsValue</DESC>
        </I-SIGNAL>
        <ASSEMBLY-SW-CONNECTOR>
          <SHORT-NAME>ConnQuartz</SHORT-NAME>
          <PROVIDER-IREF>
            <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">/WidgetAlpha/Proto</CONTEXT-COMPONENT-REF>
            <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">/WidgetAlpha/Port</TARGET-P-PORT-REF>
          </PROVIDER-IREF>
        </ASSEMBLY-SW-CONNECTOR>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""

_PACKAGE_ONLY = """\
<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>OnlyPackageName</SHORT-NAME>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


def _local(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _walk(path: Path):
    """Count tags, DEST values, reference roles, and iref child-tag shapes.

    Anything that is not one of those names is a secret: short names, paths,
    values, and every attribute other than DEST.
    """
    tags: Counter[str] = Counter()
    dests: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    secrets: list[str] = []
    root = ET.parse(path).getroot()
    for element in root.iter():
        tag = _local(element.tag)
        tags[tag] += 1
        dest = None
        for key, value in element.attrib.items():
            if _local(key) == "DEST":
                dest = value
            elif value.strip():
                secrets.append(value.strip())
        if dest is not None:
            dests[dest] += 1
            roles[tag] += 1
        text = (element.text or "").strip()
        if text:
            secrets.append(text)
        if tag.endswith("-IREF"):
            shape = ",".join(_local(child.tag) for child in list(element))
            shapes[shape] += 1
    return tags, dests, roles, shapes, secrets


def _lines(kind: str, counts: Counter[str], documents: int) -> list[str]:
    total = sum(counts.values())
    if total == 0:
        return [f"{kind} n/a n=0 documents={documents}"]
    return [
        f"{kind} {name if name else '-'} count={counts[name]} n={total} documents={documents}"
        for name in sorted(counts)
    ]


def _expected(paths: list[Path]) -> tuple[str, list[str]]:
    tags: Counter[str] = Counter()
    dests: Counter[str] = Counter()
    roles: Counter[str] = Counter()
    shapes: Counter[str] = Counter()
    secrets: list[str] = []
    for path in paths:
        one_tags, one_dests, one_roles, one_shapes, one_secrets = _walk(path)
        tags.update(one_tags)
        dests.update(one_dests)
        roles.update(one_roles)
        shapes.update(one_shapes)
        secrets.extend(one_secrets)
    documents = len(paths)
    lines = [f"documents n={documents}"]
    lines.extend(_lines("tag", tags, documents))
    lines.extend(_lines("dest", dests, documents))
    lines.extend(_lines("role", roles, documents))
    lines.extend(_lines("iref_shape", shapes, documents))
    return "\n".join(lines) + "\n", secrets


def test_output_has_counts_and_no_short_name_path_or_value(tmp_path: Path):
    """Tag, DEST, role, and iref-shape counts only. No short name, path, or value."""
    path = tmp_path / "input-marker.arxml"
    path.write_text(_WITH_REFS, encoding="utf-8")

    output = arxml_inventory.render([path])
    expected, secrets = _expected([path])

    assert output == expected
    assert "/" not in output
    assert "input-marker" not in output
    assert str(path) not in output
    for secret in secrets:
        assert secret not in output
    assert "tag I-SIGNAL count=1 " in output
    assert "dest I-SIGNAL count=1 " in output
    assert "role I-SIGNAL-REF count=1 " in output
    assert "iref_shape CONTEXT-COMPONENT-REF,TARGET-P-PORT-REF count=1 " in output
    assert " n=" in output
    assert " documents=1" in output


def test_two_files_sum_counts_and_document_denominator(tmp_path: Path):
    first = tmp_path / "one.arxml"
    second = tmp_path / "two.arxml"
    first.write_text(_WITH_REFS, encoding="utf-8")
    second.write_text(_PACKAGE_ONLY, encoding="utf-8")

    output = arxml_inventory.render([second, first])
    expected, secrets = _expected([second, first])

    assert output == expected
    assert output.startswith("documents n=2\n")
    for secret in secrets:
        assert secret not in output


def test_nothing_to_measure_prints_na(tmp_path: Path):
    path = tmp_path / "packages.arxml"
    path.write_text(_PACKAGE_ONLY, encoding="utf-8")

    output = arxml_inventory.render([path])
    expected, secrets = _expected([path])

    assert output == expected
    assert "dest n/a n=0 documents=1\n" in output
    assert "role n/a n=0 documents=1\n" in output
    assert "iref_shape n/a n=0 documents=1\n" in output
    for secret in secrets:
        assert secret not in output


def test_parse_error_prints_no_source_text(tmp_path: Path, capsys):
    path = tmp_path / "broken.arxml"
    path.write_text("<AUTOSAR><SHORT-NAME>LeakedName</SHORT-NAME>", encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        arxml_inventory.main([str(path)])

    assert caught.value.code != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not-xml" in captured.err
    assert "LeakedName" not in captured.err
    assert str(path) not in captured.err


def test_cli_matches_render(tmp_path: Path):
    path = tmp_path / "input-marker.arxml"
    path.write_text(_WITH_REFS, encoding="utf-8")
    script = _SCRIPTS / "arxml_inventory.py"

    completed = subprocess.run(
        [sys.executable, str(script), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout == arxml_inventory.render([path])
    assert "WidgetAlpha" not in completed.stdout
    assert "/" not in completed.stdout


# AUTOSAR's upper-hyphen form. Anything else is the bucket "<non-standard>".
_STANDARD_NAME = re.compile(r"^[A-Z][A-Z0-9-]*$")
_FIXED_LABELS = frozenset({
    "documents",
    "n",
    "tag",
    "dest",
    "role",
    "iref_shape",
    "count",
    "n/a",
})
_NON_STANDARD = "<non-standard>"

_NONSTANDARD_FILE = """\
<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR>
  <odd-tag>
    <I-SIGNAL-SignalZebra DEST="/WidgetAlpha/SignalZebra">SecretValue</I-SIGNAL-SignalZebra>
  </odd-tag>
</AUTOSAR>
"""


def _tokens(output: str) -> list[str]:
    """Split on whitespace, '=', and ',' so a shape's tags are separate tokens."""
    tokens = []
    for word in output.split():
        tokens.extend(word.replace("=", " ").replace(",", " ").split())
    return tokens


def _allowed_token(token: str) -> bool:
    if token in _FIXED_LABELS or token == _NON_STANDARD:
        return True
    if token.isdigit():
        return True
    return _STANDARD_NAME.fullmatch(token) is not None


def test_nonstandard_names_are_not_printed(tmp_path: Path):
    """A lowercase tag, a tag that contains a short name, and a path DEST stay out.

    Every token is a number, a fixed label, "<non-standard>", or upper-hyphen.
    """
    path = tmp_path / "mixed.arxml"
    path.write_text(_NONSTANDARD_FILE, encoding="utf-8")
    output = arxml_inventory.render([path])

    for secret in (
        "odd-tag",
        "I-SIGNAL-SignalZebra",
        "SignalZebra",
        "/WidgetAlpha/SignalZebra",
        "WidgetAlpha",
        "SecretValue",
    ):
        assert secret not in output

    disallowed = [token for token in _tokens(output) if not _allowed_token(token)]
    assert disallowed == []
    assert _NON_STANDARD in output
