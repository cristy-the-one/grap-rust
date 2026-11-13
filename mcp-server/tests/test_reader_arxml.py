"""ARXML reader tests.

AR-07: the inventory prints tags, DEST values, roles, and iref shapes only.
The synthetic file below stands in for that check: it has SHORT-NAMEs,
reference paths, and values. None of those strings may appear in the output.

AR-02: a file set is refused with a named reason, or it yields the identity index.

AR-03: every DEST outside REFERENCE-BASES is classified, relative bases
resolve, and the six categories sum to references_seen.

AR-04: every identifiable element is a SourceItem. Scalar leaves become
attributes, each owned reference that emits a link becomes refers_to, and
dangling or undefined targets become one unresolved placeholder each.
"""

import hashlib
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

import grap_mcp.readers.arxml as arxml_reader
from grap_mcp.readers import SourceLink
from grap_mcp.readers.arxml import ArxmlError, _conserve, read_arxml

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
    items, report = read_arxml([path], source_id="system-4.2")
    index = _index_of(items)

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

    items, report = read_arxml([path], source_id="bases")
    index = _index_of(items)

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


def _index_of(items) -> dict[str, str]:
    """Path to tag, for identifiable items. Placeholders have no tag."""
    return {
        item.item_id: item.attributes["tag"]
        for item in items
        if item.kind_hint != "unresolved"
    }


def _assert_sampled_ref_links(item, report: dict) -> None:
    """Emitting references in the report match that item's refers_to links.

    The report keeps at most 50 references, a prefix of the full sequence.
    When the prefix is the whole sequence, every link is checked. Otherwise
    the prefix of that item's links is checked, and the integer totals stay
    outside this helper.
    """
    owned_refs = [
        ref for ref in report["references"]
        if ref.owner == item.item_id and ref.category in _LINK_CATEGORIES
    ]
    got = [link for link in item.links if link.type == "refers_to"]
    assert all(link.context == [] and link.role for link in got)
    compared = got if len(report["references"]) == report["references_seen"] else got[:len(owned_refs)]
    assert [link.target_id for link in compared] == [ref.target for ref in owned_refs]
    for link, ref in zip(compared, owned_refs, strict=True):
        if ref.role != "VALUE-REF":
            assert link.role == ref.role


@pytest.mark.parametrize(
    ("name", "identifiables", "packages", "references_seen", "counts"),
    _LOADABLE_REFERENCES,
)
def test_loadable_files_match_measured_reference_counts(
    name, identifiables, packages, references_seen, counts,
):
    """§8 reference counts for the four loadable fixtures, and conservation."""
    path = _FIXTURES / name
    items, report = read_arxml([path], source_id=path.stem)
    index = _index_of(items)

    assert len(index) == report["identifiables"] == identifiables
    assert report["files"] == 1
    assert report["references_seen"] == references_seen
    assert len(report["references"]) == min(50, references_seen)
    for category, expected in zip(_CATEGORIES, counts, strict=True):
        assert report[category] == expected
    assert _category_sum(report) == references_seen
    if packages is not None:
        assert report["packages"] == packages

    counted = Counter(ref.category for ref in report["references"])
    if references_seen <= 50:
        for category in _CATEGORIES:
            assert counted[category] == report[category]
    else:
        assert set(counted) <= set(_CATEGORIES)
        assert sum(counted.values()) == 50

    for category in ("dest_mismatch", "dangling", "unresolvable_relative", "empty"):
        listed = report["entries"][category]
        assert len(listed) == min(50, report[category])
        assert all(entry.category == category for entry in listed)


def test_system_4_2_three_relative_references_resolve():
    """125 resolved references include 3 relative ones: one named base, two default."""
    path = _FIXTURES / "system-4.2.arxml"
    items, report = read_arxml([path], source_id="system-4.2")

    assert report["base_declarations"] == 2
    assert [(base.label, base.base_path, base.is_default) for base in report["bases"]] == [
        ("ConstantBase", "/Constants", True),
        ("SystemSignalBase", "/SystemSignal", False),
    ]
    assert report["references_seen"] == 125
    assert report["resolved"] == 125
    assert len(report["references"]) == 50
    targets = Counter(
        link.target_id
        for item in items
        for link in item.links
        if link.type == "refers_to"
    )
    assert targets["/SystemSignal/SystemSignalInner/Signal2"] == 1
    assert targets["/Constants/BooleanFalse"] == 2
    assert sum(targets.values()) == 125


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

    items, report = read_arxml([path], source_id="global")
    index = _index_of(items)

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

    _items, report = read_arxml([path], source_id="local")

    assert report["references_seen"] == 1
    assert report["unresolvable_relative"] == 1
    assert report["resolved"] == 0
    assert report["dangling"] == 0
    assert _category_sum(report) == 1
    (ref,) = report["references"]
    assert ref.category == "unresolvable_relative"
    assert ref.target is None
    assert len(report["entries"]["unresolvable_relative"]) == 1


# §5 kind_hint table. Suffix rows are the ○ rules; exact tags are the rest.
_KIND_BY_TAG = {
    "AR-PACKAGE": "package",
    "ECU-INSTANCE": "ecu",
    "CAN-CLUSTER": "network",
    "CAN-PHYSICAL-CHANNEL": "network",
    "CAN-COMMUNICATION-CONTROLLER": "network",
    "LIN-CLUSTER": "network",
    "LIN-PHYSICAL-CHANNEL": "network",
    "LIN-COMMUNICATION-CONTROLLER": "network",
    "FLEXRAY-CLUSTER": "network",
    "FLEXRAY-PHYSICAL-CHANNEL": "network",
    "FLEXRAY-COMMUNICATION-CONTROLLER": "network",
    "ETHERNET-CLUSTER": "network",
    "ETHERNET-PHYSICAL-CHANNEL": "network",
    "ETHERNET-COMMUNICATION-CONTROLLER": "network",
    "CAN-FRAME": "message",
    "LIN-UNCONDITIONAL-FRAME": "message",
    "FLEXRAY-FRAME": "message",
    "I-SIGNAL-I-PDU": "pdu",
    "MULTIPLEXED-I-PDU": "pdu",
    "CONTAINER-I-PDU": "pdu",
    "SECURED-I-PDU": "pdu",
    "NM-PDU": "pdu",
    "N-PDU": "pdu",
    "DCM-I-PDU": "pdu",
    "GENERAL-PURPOSE-PDU": "pdu",
    "GENERAL-PURPOSE-I-PDU": "pdu",
    "USER-DEFINED-PDU": "pdu",
    "I-SIGNAL": "signal",
    "SYSTEM-SIGNAL": "signal",
    "I-SIGNAL-GROUP": "signal",
    "SYSTEM-SIGNAL-GROUP": "signal",
    "I-SIGNAL-TO-I-PDU-MAPPING": "relation",
    "PDU-TO-FRAME-MAPPING": "relation",
    "CAN-FRAME-TRIGGERING": "relation",
    "PDU-TRIGGERING": "relation",
    "I-SIGNAL-TRIGGERING": "relation",
    "ASSEMBLY-SW-CONNECTOR": "relation",
    "DELEGATION-SW-CONNECTOR": "relation",
    "SENDER-RECEIVER-TO-SIGNAL-MAPPING": "relation",
    "SWC-TO-ECU-MAPPING": "relation",
    "COMPU-METHOD": "scaling",
    "UNIT": "unit",
    "SW-BASE-TYPE": "datatype",
    "APPLICATION-PRIMITIVE-DATA-TYPE": "datatype",
    "APPLICATION-RECORD-DATA-TYPE": "datatype",
    "APPLICATION-ARRAY-DATA-TYPE": "datatype",
    "IMPLEMENTATION-DATA-TYPE": "datatype",
    "DATA-CONSTR": "datatype",
    "SYSTEM": "system",
    "SW-COMPONENT-PROTOTYPE": "component",
    "P-PORT-PROTOTYPE": "port",
    "R-PORT-PROTOTYPE": "port",
    "PR-PORT-PROTOTYPE": "port",
    "VARIABLE-DATA-PROTOTYPE": "data_element",
    "PARAMETER-DATA-PROTOTYPE": "data_element",
    "ARGUMENT-DATA-PROTOTYPE": "data_element",
    "CLIENT-SERVER-OPERATION": "data_element",
    "SWC-INTERNAL-BEHAVIOR": "behavior",
    "RUNNABLE-ENTITY": "behavior",
    "MODE-DECLARATION-GROUP": "mode",
    "MODE-DECLARATION": "mode",
    "ECUC-MODULE-CONFIGURATION-VALUES": "config_module",
    "ECUC-VALUE-COLLECTION": "config_module",
    "ECUC-CONTAINER-VALUE": "config_container",
}

# Categories that emit a link (§4.3). VALUE-REF roles are renamed by AR-06.
_LINK_CATEGORIES = (
    "resolved",
    "dest_mismatch",
    "unresolved_definition",
    "dangling",
)
_PLACEHOLDER_CATEGORIES = ("unresolved_definition", "dangling")


def _expected_kind(tag: str) -> str:
    kind = _KIND_BY_TAG.get(tag)
    if kind is not None:
        return kind
    if tag.endswith("-SW-COMPONENT-TYPE"):
        return "component"
    if tag.endswith("-INTERFACE"):
        return "interface"
    if tag.endswith("-EVENT"):
        return "behavior"
    return "element"


def _part_of(target_id: str) -> SourceLink:
    return SourceLink(type="part_of", target_id=target_id, role="", context=[])


def _refers(target_id: str, role: str) -> SourceLink:
    return SourceLink(type="refers_to", target_id=target_id, role=role, context=[])


def test_signal1_mapping_has_position_byte_order_and_signal_ref():
    """The mapping keeps START-POSITION, PACKING-BYTE-ORDER, and I-SIGNAL-REF."""
    path = _FIXTURES / "system-4.2.arxml"
    items, report = read_arxml([path], source_id="system-4.2")
    index = _index_of(items)
    item = next(
        one for one in items
        if one.item_id == "/ISignalIPdu/message1/Signal1"
    )

    assert item.source_id == "system-4.2"
    assert item.release == "4.4.0"
    assert item.kind_hint == "relation"
    assert item.text == "Signal1"
    assert item.heading_path == ["ISignalIPdu"]
    assert item.file == path.as_posix()
    assert item.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert item.attributes == {
        "tag": "I-SIGNAL-TO-I-PDU-MAPPING",
        "short_name": "Signal1",
        "PACKING-BYTE-ORDER": "MOST-SIGNIFICANT-BYTE-FIRST",
        "START-POSITION": "36",
    }
    assert item.links == [
        _part_of("/ISignalIPdu/message1"),
        _refers("/ISignal/signal1", "I-SIGNAL-REF"),
    ]
    assert index[item.item_id] == "I-SIGNAL-TO-I-PDU-MAPPING"
    assert report["tag_counts"]["I-SIGNAL-TO-I-PDU-MAPPING"] == 23


def test_dangling_fixture_yields_one_unresolved_item():
    """Four identifiables, one dangling reference, one unresolved placeholder."""
    path = _FIXTURES / "system-dangling-reference-4.2.arxml"
    items, report = read_arxml([path], source_id="dangling")
    index = _index_of(items)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    assert report["identifiables"] == len(index) == 4
    assert report["dangling"] == 1
    assert report["references_seen"] == 1
    assert [item.item_id for item in items] == [
        "/Cluster",
        "/Cluster/Cluster0",
        "/Cluster/Cluster0/Pch0",
        "/Cluster/Cluster0/Pch0/message1",
        "/PackageDoesNotExist/Message1",
    ]
    assert [item.kind_hint for item in items] == [
        "package",
        "network",
        "network",
        "relation",
        "unresolved",
    ]

    package, cluster, channel, triggering, placeholder = items
    assert package.links == []
    assert package.heading_path == []
    assert package.attributes == {
        "tag": "AR-PACKAGE",
        "short_name": "Cluster",
    }
    assert cluster.links == [_part_of("/Cluster")]
    assert cluster.heading_path == ["Cluster"]
    assert cluster.attributes == {
        "tag": "CAN-CLUSTER",
        "short_name": "Cluster0",
        "CAN-CLUSTER-VARIANTS.CAN-CLUSTER-CONDITIONAL.BAUDRATE": "500000",
    }
    assert channel.links == [_part_of("/Cluster/Cluster0")]
    assert channel.attributes == {
        "tag": "CAN-PHYSICAL-CHANNEL",
        "short_name": "Pch0",
    }
    assert triggering.heading_path == ["Cluster"]
    assert triggering.text == "message1"
    assert triggering.attributes == {
        "tag": "CAN-FRAME-TRIGGERING",
        "short_name": "message1",
        "CAN-ADDRESSING-MODE": "STANDARD",
        "IDENTIFIER": "5",
    }
    assert triggering.links == [
        _part_of("/Cluster/Cluster0/Pch0"),
        _refers("/PackageDoesNotExist/Message1", "FRAME-REF"),
    ]
    for item in (package, cluster, channel, triggering):
        assert item.source_id == "dangling"
        assert item.release == "4.4.0"
        assert item.file == path.as_posix()
        assert item.sha256 == digest

    assert placeholder.kind_hint == "unresolved"
    assert placeholder.text == ""
    assert placeholder.heading_path == []
    assert placeholder.attributes == {"expected_kind": "CAN-FRAME"}
    assert placeholder.links == []
    assert placeholder.release is None
    assert placeholder.file == ""
    assert placeholder.sha256 == ""
    assert placeholder.source_id == "dangling"
    assert sum(item.kind_hint == "unresolved" for item in items) == 1


def test_items_match_the_index_and_every_emitting_reference():
    """Identifiable items follow the index. Link-emitting references are kept."""
    path = _FIXTURES / "system-4.2.arxml"
    items, report = read_arxml([path], source_id="system-4.2")
    index = _index_of(items)
    owned = [item for item in items if item.kind_hint != "unresolved"]
    placeholders = [item for item in items if item.kind_hint == "unresolved"]

    assert [item.item_id for item in owned] == list(index)
    assert len(owned) == report["identifiables"] == 157
    assert placeholders == []
    assert sum(item.kind_hint == "package" for item in owned) == report["packages"] == 22
    assert report["tag_counts"] == dict(Counter(
        item.attributes["tag"] for item in owned
    ))
    assert sum(report["tag_counts"].values()) == 157
    for tag, count in (
        ("I-SIGNAL", 19),
        ("SYSTEM-SIGNAL", 13),
        ("CAN-FRAME", 8),
        ("CAN-FRAME-TRIGGERING", 8),
        ("I-SIGNAL-I-PDU", 7),
        ("I-SIGNAL-TO-I-PDU-MAPPING", 23),
        ("PDU-TO-FRAME-MAPPING", 7),
        ("COMPU-METHOD", 5),
        ("ECU-INSTANCE", 3),
    ):
        assert report["tag_counts"][tag] == count
    assert report["variation_points"] == 0

    for item in owned:
        assert item.kind_hint == _expected_kind(item.attributes["tag"])
        assert item.attributes["short_name"]
        assert "SHORT-NAME" not in item.attributes
        assert item.attributes["tag"] == index[item.item_id]
        parts = [link for link in item.links if link.type == "part_of"]
        if item.item_id.count("/") == 1:
            assert parts == []
        else:
            parent = item.item_id.rsplit("/", 1)[0]
            assert parts == [_part_of(parent)]
            assert parent in index
        _assert_sampled_ref_links(item, report)

    refers = sum(
        link.type == "refers_to" for item in items for link in item.links
    )
    emitted = sum(report[name] for name in _LINK_CATEGORIES)
    assert refers == emitted == 125
    assert (
        refers + report["unresolvable_relative"] + report["empty"]
        == report["references_seen"]
    )

    dancer = next(item for item in items if item.item_id == "/ECU/Dancer")
    assert dancer.kind_hint == "ecu"
    assert dancer.text == "Rhythm is a Dancer!"
    assert dancer.heading_path == ["ECU"]
    assert dancer.attributes["DESC.L-2"] == "Rhythm is a Dancer!"
    assert dancer.links == [
        _part_of("/ECU"),
        _refers("/PDU_GROUPS/RX/Dancer", "ASSOCIATED-COM-I-PDU-GROUP-REF"),
        _refers("/PDU_GROUPS/TX/Dancer", "ASSOCIATED-COM-I-PDU-GROUP-REF"),
    ]

    group = next(item for item in items if item.item_id == "/PDU_GROUPS/RX/Dancer")
    assert group.kind_hint == "element"
    assert group.attributes["tag"] == "I-SIGNAL-I-PDU-GROUP"
    assert group.attributes["COMMUNICATION-DIRECTION"] == "IN"
    assert group.heading_path == ["PDU_GROUPS", "RX"]
    assert group.links[0] == _part_of("/PDU_GROUPS/RX")

    compu = next(item for item in items if item.item_id == "/CompuMethod/Signal1")
    assert compu.kind_hint == "scaling"
    assert compu.text == "Signal1"
    prefix = (
        "COMPU-INTERNAL-TO-PHYS.COMPU-SCALES.COMPU-SCALE"
        ".COMPU-RATIONAL-COEFFS"
    )
    assert compu.attributes[f"{prefix}.COMPU-NUMERATOR.V[0]"] == "0"
    assert compu.attributes[f"{prefix}.COMPU-NUMERATOR.V[1]"] == "1e1"
    assert compu.attributes[f"{prefix}.COMPU-DENOMINATOR.V"] == "2"
    assert f"{prefix}.COMPU-DENOMINATOR.V[0]" not in compu.attributes

    signal4 = next(item for item in items if item.item_id == "/CompuMethod/Signal4")
    assert signal4.text == "Signal4"
    desc = "COMPU-INTERNAL-TO-PHYS.COMPU-SCALES.COMPU-SCALE.DESC.L-2"
    assert signal4.attributes[f"{desc}[0]"] == "One Comment"
    assert signal4.attributes[f"{desc}[1]"] == "Ein Kommentar"
    limits = "COMPU-INTERNAL-TO-PHYS.COMPU-SCALES.COMPU-SCALE.LOWER-LIMIT"
    assert signal4.attributes[f"{limits}[0]"] == "1"
    assert signal4.attributes[f"{limits}[1]"] == "2"


@pytest.mark.parametrize(
    "name",
    [row[0] for row in _LOADABLE_REFERENCES if row[0] != "system-4.2.arxml"],
)
def test_other_loadable_files_keep_items_and_links(name):
    """The other three loadable files: one item per index path, no dropped link."""
    path = _FIXTURES / name
    items, report = read_arxml([path], source_id=path.stem)
    index = _index_of(items)
    owned = [item for item in items if item.kind_hint != "unresolved"]
    placeholders = [item for item in items if item.kind_hint == "unresolved"]

    assert [item.item_id for item in owned] == list(index)
    assert len(owned) == report["identifiables"]
    assert report["tag_counts"] == dict(Counter(
        item.attributes["tag"] for item in owned
    ))
    assert report["variation_points"] == 0
    for item in owned:
        assert item.kind_hint == _expected_kind(item.attributes["tag"])
        assert "tag" in item.attributes and "short_name" in item.attributes
        assert "SHORT-NAME" not in item.attributes
        _assert_sampled_ref_links(item, report)

    seen: set[str] = set()
    order: list[str] = []
    first_dest: dict[str, str] = {}
    for ref in report["references"]:
        if ref.category in _PLACEHOLDER_CATEGORIES and ref.target not in seen:
            seen.add(ref.target)
            order.append(ref.target)
            first_dest[ref.target] = ref.dest
    complete = len(report["references"]) == report["references_seen"]
    if complete:
        assert [item.item_id for item in placeholders] == order
    else:
        assert len(report["references"]) == 50
        assert [item.item_id for item in placeholders if item.item_id in seen] == order
        assert len(placeholders) > len(order)
        assert len(placeholders) == len({item.item_id for item in placeholders})
    for item in placeholders:
        assert item.kind_hint == "unresolved"
        assert item.text == ""
        assert item.links == []
        if item.item_id in first_dest:
            assert item.attributes == {"expected_kind": first_dest[item.item_id]}
        else:
            assert set(item.attributes) == {"expected_kind"}
            assert item.attributes["expected_kind"]

    refers = sum(link.type == "refers_to" for item in items for link in item.links)
    emitted = sum(report[name] for name in _LINK_CATEGORIES)
    assert refers == emitted
    assert (
        refers + report["unresolvable_relative"] + report["empty"]
        == report["references_seen"]
    )
    if complete:
        assert len(report["references"]) == report["references_seen"]


def _write_arxml(tmp_path: Path, name: str, body: str, schema: str | None) -> Path:
    if schema is None:
        location = ""
    else:
        location = (
            ' xsi:schemaLocation="http://autosar.org/schema/r4.0 '
            f'{schema}"'
        )
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<AUTOSAR xmlns="http://autosar.org/schema/r4.0"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        f"{location}>\n"
        "  <AR-PACKAGES>\n"
        "    <AR-PACKAGE>\n"
        "      <SHORT-NAME>P</SHORT-NAME>\n"
        "      <ELEMENTS>\n"
        f"{body}"
        "      </ELEMENTS>\n"
        "    </AR-PACKAGE>\n"
        "  </AR-PACKAGES>\n"
        "</AUTOSAR>\n",
        encoding="utf-8",
    )
    return path


def test_desc_prefers_en_and_falls_through_when_empty(tmp_path: Path):
    """Item text is DESC, then LONG-NAME, then SHORT-NAME. EN wins inside one."""
    path = _write_arxml(
        tmp_path,
        "lang.arxml",
        """\
        <I-SIGNAL>
          <SHORT-NAME>S</SHORT-NAME>
          <DESC>
            <L-2 L="DE">DescDe</L-2>
            <L-2 L="EN">DescEn</L-2>
          </DESC>
          <LONG-NAME>
            <L-4 L="EN">LongEn</L-4>
          </LONG-NAME>
        </I-SIGNAL>
        <I-SIGNAL>
          <SHORT-NAME>OnlyShort</SHORT-NAME>
          <DESC><L-2 L="EN"></L-2></DESC>
          <LONG-NAME><L-4 L="FOR-ALL">LongOnly</L-4></LONG-NAME>
        </I-SIGNAL>
""",
        "AUTOSAR_00046.xsd",
    )
    items, report = read_arxml([path], source_id="lang")
    by_id = {item.item_id: item for item in items}

    signal = by_id["/P/S"]
    assert signal.text == "DescEn"
    assert signal.release == "4.4.0"
    assert signal.attributes["DESC.L-2[0]"] == "DescDe"
    assert signal.attributes["DESC.L-2[1]"] == "DescEn"
    assert signal.attributes["LONG-NAME.L-4"] == "LongEn"
    assert "LONG-NAME.L-4[0]" not in signal.attributes
    assert by_id["/P/OnlyShort"].text == "LongOnly"


def test_release_from_schema_file_name(tmp_path: Path):
    """§7, recorded on the item: hyphen form, five-digit table, otherwise null."""
    body = "        <I-SIGNAL><SHORT-NAME>S</SHORT-NAME></I-SIGNAL>\n"
    cases = (
        ("hyphen.arxml", "dir/AUTOSAR_4-2-2.xsd", "4.2.2"),
        ("five.arxml", "AUTOSAR_00044.xsd", "4.3.1"),
        ("unknown-five.arxml", "AUTOSAR_00099.xsd", None),
        ("other.arxml", "AUTOSAR_not-a-release.xsd", None),
        ("missing.arxml", None, None),
    )
    for name, schema, expected in cases:
        path = _write_arxml(tmp_path, name, body, schema)
        items, report = read_arxml([path], source_id=path.stem)
        item = next(one for one in items if one.item_id == "/P/S")
        assert item.release == expected


def test_unlisted_and_suffix_kinds(tmp_path: Path):
    """A tag missing from the table is element. Suffix rows use the table's label."""
    body = """\
        <APPLICATION-SW-COMPONENT-TYPE><SHORT-NAME>C</SHORT-NAME></APPLICATION-SW-COMPONENT-TYPE>
        <SENDER-RECEIVER-INTERFACE><SHORT-NAME>I</SHORT-NAME></SENDER-RECEIVER-INTERFACE>
        <TIMING-EVENT><SHORT-NAME>E</SHORT-NAME></TIMING-EVENT>
        <LIN-CLUSTER><SHORT-NAME>L</SHORT-NAME></LIN-CLUSTER>
        <FLEXRAY-COMMUNICATION-CONTROLLER><SHORT-NAME>F</SHORT-NAME></FLEXRAY-COMMUNICATION-CONTROLLER>
        <ETHERNET-PHYSICAL-CHANNEL><SHORT-NAME>H</SHORT-NAME></ETHERNET-PHYSICAL-CHANNEL>
        <SW-COMPONENT-PROTOTYPE><SHORT-NAME>P2</SHORT-NAME></SW-COMPONENT-PROTOTYPE>
        <P-PORT-PROTOTYPE><SHORT-NAME>Port</SHORT-NAME></P-PORT-PROTOTYPE>
        <NOT-A-KIND><SHORT-NAME>N</SHORT-NAME></NOT-A-KIND>
"""
    path = _write_arxml(tmp_path, "kinds.arxml", body, None)
    items, _report = read_arxml([path], source_id="kinds")
    kinds = {
        item.item_id: item.kind_hint
        for item in items
        if item.kind_hint != "package"
    }
    assert kinds == {
        "/P/C": "component",
        "/P/I": "interface",
        "/P/E": "behavior",
        "/P/L": "network",
        "/P/F": "network",
        "/P/H": "network",
        "/P/P2": "component",
        "/P/Port": "port",
        "/P/N": "element",
    }


def _wrap(names: list[str], text: str) -> str:
    inner = text
    for name in reversed(names):
        inner = f"<{name}>{inner}</{name}>"
    return inner


def test_depth_and_count_limits_set_truncated(tmp_path: Path):
    """Depth 10 is kept. Depth 11, or more than 300 leaves, sets _truncated."""
    deep = _wrap(list("ABCDEFGHIJ"), "leaf10")
    deeper = _wrap([*list("ABCDEFGHIJ"), "K"], "leaf11")
    path = _write_arxml(
        tmp_path,
        "deep.arxml",
        f"""\
        <I-SIGNAL>
          <SHORT-NAME>Deep</SHORT-NAME>
          {deep}
          {deeper}
        </I-SIGNAL>
""",
        None,
    )
    items, report = read_arxml([path], source_id="deep")
    item = next(one for one in items if one.item_id == "/P/Deep")
    assert item.attributes["A.B.C.D.E.F.G.H.I.J"] == "leaf10"
    assert "A.B.C.D.E.F.G.H.I.J.K" not in item.attributes
    assert item.attributes["_truncated"] == "true"
    assert report["attributes_truncated"] == 1

    leaves = "\n".join(f"          <N>{i}</N>" for i in range(301))
    wide = _write_arxml(
        tmp_path,
        "wide.arxml",
        f"""\
        <I-SIGNAL>
          <SHORT-NAME>Wide</SHORT-NAME>
{leaves}
        </I-SIGNAL>
""",
        None,
    )
    wide_items, wide_report = read_arxml([wide], source_id="wide")
    wide_item = next(one for one in wide_items if one.item_id == "/P/Wide")
    assert "SHORT-NAME" not in wide_item.attributes
    assert wide_item.attributes["N[0]"] == "0"
    assert wide_item.attributes["N[298]"] == "298"
    assert wide_item.attributes["N[299]"] == "299"
    assert "N[300]" not in wide_item.attributes
    assert wide_item.attributes["_truncated"] == "true"
    assert wide_item.attributes["tag"] == "I-SIGNAL"
    assert wide_item.attributes["short_name"] == "Wide"
    assert wide_report["attributes_truncated"] == 1


def test_variation_point_is_flagged_and_counted(tmp_path: Path):
    path = _write_arxml(
        tmp_path,
        "variant.arxml",
        """\
        <I-SIGNAL>
          <SHORT-NAME>S</SHORT-NAME>
          <VARIATION-POINT>
            <SHORT-LABEL>Cond</SHORT-LABEL>
          </VARIATION-POINT>
          <LENGTH>1</LENGTH>
        </I-SIGNAL>
        <I-SIGNAL>
          <SHORT-NAME>Plain</SHORT-NAME>
          <LENGTH>2</LENGTH>
        </I-SIGNAL>
""",
        None,
    )
    items, report = read_arxml([path], source_id="variant")
    by_id = {item.item_id: item for item in items}
    flagged = by_id["/P/S"]
    assert flagged.attributes["_has_variation_point"] == "true"
    assert flagged.attributes["VARIATION-POINT.SHORT-LABEL"] == "Cond"
    assert flagged.attributes["LENGTH"] == "1"
    assert "_has_variation_point" not in by_id["/P/Plain"].attributes
    assert report["variation_points"] == 1


def _zero_counts() -> dict[str, int]:
    return {name: 0 for name in _CATEGORIES}


def test_conservation_sum_mismatch_raises_arxml_error():
    """F21. A broken category sum is ArxmlError, not an assertion."""
    counts = _zero_counts()
    counts["resolved"] = 1

    with pytest.raises(ArxmlError, match="conservation") as caught:
        _conserve("probe", 2, counts, 2)

    message = str(caught.value)
    assert "references_seen=2" in message
    assert "n=2" in message
    assert "sum=1" in message
    assert "resolved=1" in message


def test_conservation_sum_mismatch_raises_under_optimise():
    """F21. python -O must not skip the conservation check."""
    script = """
from grap_mcp.readers.arxml import ArxmlError, _conserve
counts = {name: 0 for name in (
    "resolved", "dest_mismatch", "unresolved_definition",
    "dangling", "unresolvable_relative", "empty",
)}
counts["resolved"] = 1
try:
    _conserve("probe", 2, counts, 2)
except ArxmlError as exc:
    text = str(exc)
    assert "conservation" in text
    assert "references_seen=2" in text and "n=2" in text
    assert "sum=1" in text
else:
    raise SystemExit("no error")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    completed = subprocess.run(
        [sys.executable, "-O", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_dropped_dest_fails_independent_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """F22. A DEST the collector misses is a conservation error."""
    path = _write_arxml(
        tmp_path,
        "drop.arxml",
        """\
        <I-SIGNAL>
          <SHORT-NAME>S</SHORT-NAME>
          <I-SIGNAL-REF DEST="I-SIGNAL">/P/S</I-SIGNAL-REF>
        </I-SIGNAL>
""",
        None,
    )
    original = arxml_reader._walk

    def drop_refs(*args, **kwargs):
        seen = args[7]
        start = len(seen)
        original(*args, **kwargs)
        del seen[start:]

    monkeypatch.setattr(arxml_reader, "_walk", drop_refs)

    with pytest.raises(ArxmlError, match="conservation") as caught:
        read_arxml([path], source_id="drop")

    message = str(caught.value)
    assert "references_seen=0" in message
    assert "n=0" in message
    assert "dest_elements=1" in message
    assert "n_dest=1" in message


def test_returns_items_and_caps_every_report_list():
    """F23. (items, report), and no report list is longer than 50."""
    path = _FIXTURES / "system-4.2.arxml"
    items, report = read_arxml([path], source_id="system-4.2")

    assert isinstance(items, tuple)
    assert len(items) == report["identifiables"] == 157
    assert items[0].item_id
    assert "items" not in report
    assert report["references_seen"] == 125
    assert len(report["references"]) == 50

    def lists_in(value, prefix):
        if isinstance(value, (list, tuple)):
            assert len(value) <= 50, prefix
        elif isinstance(value, dict):
            for key, child in value.items():
                lists_in(child, f"{prefix}.{key}")

    lists_in(report, "report")


def test_short_name_is_not_also_an_attribute():
    """The duplicate SHORT-NAME leaf is omitted. short_name stays."""
    path = _FIXTURES / "system-4.2.arxml"
    items, _report = read_arxml([path], source_id="system-4.2")
    item = next(
        one for one in items
        if one.item_id == "/ISignalIPdu/message1/Signal1"
    )

    assert "SHORT-NAME" not in item.attributes
    assert item.attributes["short_name"] == "Signal1"
    assert item.attributes["START-POSITION"] == "36"
    assert item.attributes["PACKING-BYTE-ORDER"] == "MOST-SIGNIFICANT-BYTE-FIRST"
