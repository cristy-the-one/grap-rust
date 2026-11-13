"""ARXML reader tests.

AR-07: the inventory prints tags, DEST values, roles, and iref shapes only.
The synthetic file below stands in for that check: it has SHORT-NAMEs,
reference paths, and values. None of those strings may appear in the output.

AR-02: a file set is refused with a named reason, or it yields the identity index.

AR-03: every DEST outside REFERENCE-BASES is classified, relative bases
resolve, and the six categories sum to references_seen.
"""

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from grap_mcp.readers.arxml import ArxmlError, read_arxml

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


# The five files §8 refuses. system-4.2.arxml is the measured index.
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "arxml"
_REFUSED = (
    "system-3.2.3.arxml",
    "system-DAI-3.1.2.arxml",
    "system-bad-root-tag-4.2.arxml",
    "system-illegal-namespace-4.2.arxml",
    "system-illegal-version-4.2.2.1.0.arxml",
)


@pytest.mark.parametrize("name", _REFUSED)
def test_refused_files_raise_unsupported_dialect(name: str):
    """Each of the five files is refused, naming unsupported-dialect, the file, and its root tag."""
    path = _FIXTURES / name
    root_tag = ET.parse(path).getroot().tag

    with pytest.raises(ArxmlError) as caught:
        read_arxml([path], source_id=path.stem)

    message = str(caught.value)
    assert "unsupported-dialect" in message
    assert name in message
    assert root_tag in message


def test_system_4_2_index_counts_and_identities():
    """Measured index: 157 identifiables, 22 packages, and both §8 identities."""
    path = _FIXTURES / "system-4.2.arxml"
    index, report = read_arxml([path], source_id="system-4.2")

    assert len(index) == report["identifiables"] == 157
    assert sum(tag == "AR-PACKAGE" for tag in index.values()) == report["packages"] == 22
    assert report["files"] == 1
    assert report["base_declarations"] == 2
    assert [(base.label, base.base_path, base.is_default) for base in report["bases"]] == [
        ("ConstantBase", "/Constants", True),
        ("SystemSignalBase", "/SystemSignal", False),
    ]
    assert index["/Cluster/Cluster0/Pch0/message1"] == "CAN-FRAME-TRIGGERING"
    assert index["/ISignalIPdu/message1/Signal1"] == "I-SIGNAL-TO-I-PDU-MAPPING"


def test_thirty_byte_doctype_raises_dtd_not_allowed(tmp_path: Path):
    """A 30-byte file that contains <!DOCTYPE is refused before it is parsed."""
    payload = b"<!DOCTYPE " + b"x" * 20
    assert len(payload) == 30
    path = tmp_path / "doctype.arxml"
    path.write_bytes(payload)

    with pytest.raises(ArxmlError) as caught:
        read_arxml([path], source_id="doctype")

    message = str(caught.value)
    assert "dtd-not-allowed" in message
    assert path.name in message
    assert "not-xml" not in message


def test_entity_declaration_raises_dtd_not_allowed(tmp_path: Path):
    path = tmp_path / "entity.arxml"
    path.write_bytes(b"<!ENTITY e 'x'>")

    with pytest.raises(ArxmlError) as caught:
        read_arxml([path], source_id="entity")

    message = str(caught.value)
    assert "dtd-not-allowed" in message
    assert path.name in message


def test_not_well_formed_raises_not_xml(tmp_path: Path):
    path = tmp_path / "broken.arxml"
    path.write_text("<AUTOSAR>", encoding="utf-8")

    with pytest.raises(ArxmlError) as caught:
        read_arxml([path], source_id="broken")

    message = str(caught.value)
    assert "not-xml" in message
    assert path.name in message
    assert "unsupported-dialect" not in message


def test_packages_only_is_vacuous(tmp_path: Path):
    """Packages are identifiables, and a set with nothing else is refused."""
    path = tmp_path / "packages.arxml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<AUTOSAR xmlns="http://autosar.org/schema/r4.0">\n'
        "  <AR-PACKAGES>\n"
        "    <AR-PACKAGE><SHORT-NAME>Only</SHORT-NAME></AR-PACKAGE>\n"
        "  </AR-PACKAGES>\n"
        "</AUTOSAR>\n",
        encoding="utf-8",
    )

    with pytest.raises(ArxmlError) as caught:
        read_arxml([path], source_id="packages")

    message = str(caught.value)
    assert "vacuous" in message
    assert path.name in message


def test_reference_bases_are_recorded_and_not_indexed(tmp_path: Path):
    """Step 2 records bases and does not walk into REFERENCE-BASES."""
    path = tmp_path / "bases.arxml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<AUTOSAR xmlns="http://autosar.org/schema/r4.0">\n'
        "  <AR-PACKAGES>\n"
        "    <AR-PACKAGE>\n"
        "      <SHORT-NAME>Pkg</SHORT-NAME>\n"
        "      <REFERENCE-BASES>\n"
        "        <REFERENCE-BASE>\n"
        "          <SHORT-LABEL>Hidden</SHORT-LABEL>\n"
        "          <IS-DEFAULT>true</IS-DEFAULT>\n"
        "          <IS-GLOBAL>false</IS-GLOBAL>\n"
        "          <BASE-IS-THIS-PACKAGE>true</BASE-IS-THIS-PACKAGE>\n"
        "          <PACKAGE-REF DEST=\"AR-PACKAGE\">/Ignored</PACKAGE-REF>\n"
        "          <AR-PACKAGE><SHORT-NAME>ShouldNotIndex</SHORT-NAME></AR-PACKAGE>\n"
        "        </REFERENCE-BASE>\n"
        "        <REFERENCE-BASE>\n"
        "          <SHORT-LABEL> Elsewhere </SHORT-LABEL>\n"
        "          <IS-DEFAULT>false</IS-DEFAULT>\n"
        "          <IS-GLOBAL>true</IS-GLOBAL>\n"
        "          <BASE-IS-THIS-PACKAGE>false</BASE-IS-THIS-PACKAGE>\n"
        '          <PACKAGE-REF DEST="AR-PACKAGE">  /Other  </PACKAGE-REF>\n'
        "        </REFERENCE-BASE>\n"
        "      </REFERENCE-BASES>\n"
        "      <ELEMENTS>\n"
        "        <I-SIGNAL><SHORT-NAME> Visible </SHORT-NAME></I-SIGNAL>\n"
        "      </ELEMENTS>\n"
        "    </AR-PACKAGE>\n"
        "  </AR-PACKAGES>\n"
        "</AUTOSAR>\n",
        encoding="utf-8",
    )

    index, report = read_arxml([path], source_id="bases")

    assert set(index) == {"/Pkg", "/Pkg/Visible"}
    assert index["/Pkg"] == "AR-PACKAGE"
    assert index["/Pkg/Visible"] == "I-SIGNAL"
    assert report["identifiables"] == 2
    assert report["packages"] == 1
    assert report["base_declarations"] == 2
    recorded = [
        (base.label, base.base_path, base.is_default, base.is_global, base.package)
        for base in report["bases"]
    ]
    assert recorded == [
        ("Hidden", "/Pkg", True, False, "/Pkg"),
        ("Elsewhere", "/Other", False, True, "/Pkg"),
    ]


# §4.3 categories, in the conservation-sum order.
_CATEGORIES = (
    "resolved",
    "dest_mismatch",
    "unresolved_definition",
    "dangling",
    "unresolvable_relative",
    "empty",
)

# §8 measured rows for the four loadable files.
# packages is None where §8 does not state one.
# Category order matches _CATEGORIES.
_LOADABLE_REFERENCES = (
    ("system-4.2.arxml", 157, 22, 125, (125, 0, 0, 0, 0, 0)),
    ("ecu-extract-4.2.arxml", 17, 1, 69, (7, 0, 55, 7, 0, 0)),
    ("system-dangling-reference-4.2.arxml", 4, None, 1, (0, 0, 0, 1, 0, 0)),
    ("system-missing-factor-4.2.arxml", 16, None, 5, (5, 0, 0, 0, 0, 0)),
)


def _category_sum(report: dict) -> int:
    return sum(report[name] for name in _CATEGORIES)


@pytest.mark.parametrize(
    ("name", "identifiables", "packages", "references_seen", "counts"),
    _LOADABLE_REFERENCES,
)
def test_loadable_files_match_measured_reference_counts(
    name, identifiables, packages, references_seen, counts,
):
    """§8 reference counts for the four loadable fixtures, and conservation."""
    path = _FIXTURES / name
    index, report = read_arxml([path], source_id=path.stem)

    assert len(index) == report["identifiables"] == identifiables
    assert report["files"] == 1
    assert report["references_seen"] == references_seen
    assert len(report["references"]) == references_seen
    for category, expected in zip(_CATEGORIES, counts, strict=True):
        assert report[category] == expected
    assert _category_sum(report) == references_seen
    if packages is not None:
        assert report["packages"] == packages

    counted = Counter(ref.category for ref in report["references"])
    for category in _CATEGORIES:
        assert counted[category] == report[category]

    for category in ("dest_mismatch", "dangling", "unresolvable_relative", "empty"):
        listed = report["entries"][category]
        assert len(listed) == min(50, report[category])
        assert all(entry.category == category for entry in listed)


def test_system_4_2_three_relative_references_resolve():
    """125 resolved references include 3 relative ones: one named base, two default."""
    path = _FIXTURES / "system-4.2.arxml"
    _index, report = read_arxml([path], source_id="system-4.2")

    assert report["base_declarations"] == 2
    assert report["references_seen"] == 125
    assert report["resolved"] == 125
    relative = [ref for ref in report["references"] if not ref.text.startswith("/")]
    assert len(relative) == 3
    assert {ref.category for ref in relative} == {"resolved"}
    assert Counter(ref.base for ref in relative) == Counter({
        "SystemSignalBase": 1,
        None: 2,
    })
    assert Counter(ref.target for ref in relative) == Counter({
        "/SystemSignal/SystemSignalInner/Signal2": 1,
        "/Constants/BooleanFalse": 2,
    })


def _outer_base_file(is_global: bool) -> str:
    """A default base on Outer, and a relative reference owned inside Inner."""
    flag = "true" if is_global else "false"
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>Outer</SHORT-NAME>
      <REFERENCE-BASES>
        <REFERENCE-BASE>
          <SHORT-LABEL>OuterBase</SHORT-LABEL>
          <IS-DEFAULT>true</IS-DEFAULT>
          <IS-GLOBAL>{flag}</IS-GLOBAL>
          <BASE-IS-THIS-PACKAGE>true</BASE-IS-THIS-PACKAGE>
        </REFERENCE-BASE>
      </REFERENCE-BASES>
      <AR-PACKAGES>
        <AR-PACKAGE>
          <SHORT-NAME>Inner</SHORT-NAME>
          <ELEMENTS>
            <I-SIGNAL>
              <SHORT-NAME>Sig</SHORT-NAME>
            </I-SIGNAL>
            <I-SIGNAL>
              <SHORT-NAME>Owner</SHORT-NAME>
              <I-SIGNAL-REF DEST="I-SIGNAL">Inner/Sig</I-SIGNAL-REF>
            </I-SIGNAL>
          </ELEMENTS>
        </AR-PACKAGE>
      </AR-PACKAGES>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""


def test_outer_global_base_resolves(tmp_path: Path):
    """A default base on an outer package resolves when IS-GLOBAL is true."""
    path = tmp_path / "global.arxml"
    path.write_text(_outer_base_file(True), encoding="utf-8")

    index, report = read_arxml([path], source_id="global")

    assert index["/Outer/Inner/Sig"] == "I-SIGNAL"
    assert report["references_seen"] == 1
    assert report["resolved"] == 1
    assert report["unresolvable_relative"] == 0
    assert _category_sum(report) == 1
    (ref,) = report["references"]
    assert ref.category == "resolved"
    assert ref.base is None
    assert ref.target == "/Outer/Inner/Sig"
    assert ref.role == "I-SIGNAL-REF"
    assert ref.dest == "I-SIGNAL"


def test_outer_base_not_global_is_unresolvable(tmp_path: Path):
    """The same base with IS-GLOBAL false is not guessed."""
    path = tmp_path / "local.arxml"
    path.write_text(_outer_base_file(False), encoding="utf-8")

    _index, report = read_arxml([path], source_id="local")

    assert report["references_seen"] == 1
    assert report["unresolvable_relative"] == 1
    assert report["resolved"] == 0
    assert report["dangling"] == 0
    assert _category_sum(report) == 1
    (ref,) = report["references"]
    assert ref.category == "unresolvable_relative"
    assert ref.target is None
    assert len(report["entries"]["unresolvable_relative"]) == 1
