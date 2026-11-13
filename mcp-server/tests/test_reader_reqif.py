"""R-04: a ReqIF file becomes SourceItems, or the reader raises."""

import hashlib
from pathlib import Path

import pytest

from grap_mcp.readers import SourceLink
from grap_mcp.readers.reqif import read_reqif

# File order of SPEC-OBJECT is not hierarchy order. Relation type names are
# on SPEC-RELATION-TYPE, not on the relation's own LONG-NAME.
REQIF = """\
<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd" xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <THE-HEADER>
    <REQ-IF-HEADER IDENTIFIER="_header">
      <CREATION-TIME>2026-11-13T08:00:00Z</CREATION-TIME>
      <REQ-IF-TOOL-ID>grap-test</REQ-IF-TOOL-ID>
      <REQ-IF-VERSION>1.0</REQ-IF-VERSION>
      <SOURCE-TOOL-ID>grap-test</SOURCE-TOOL-ID>
      <TITLE>Vehicle spec</TITLE>
    </REQ-IF-HEADER>
  </THE-HEADER>
  <CORE-CONTENT>
    <REQ-IF-CONTENT>
      <DATATYPES>
        <DATATYPE-DEFINITION-STRING IDENTIFIER="_dt_string" LONG-NAME="String" MAX-LENGTH="1000"/>
        <DATATYPE-DEFINITION-XHTML IDENTIFIER="_dt_xhtml" LONG-NAME="XHTML"/>
        <DATATYPE-DEFINITION-INTEGER IDENTIFIER="_dt_int" LONG-NAME="Integer" MIN="0" MAX="10"/>
        <DATATYPE-DEFINITION-REAL IDENTIFIER="_dt_real" LONG-NAME="Real" MIN="0" MAX="1" ACCURACY="2"/>
        <DATATYPE-DEFINITION-BOOLEAN IDENTIFIER="_dt_bool" LONG-NAME="Boolean"/>
        <DATATYPE-DEFINITION-ENUMERATION IDENTIFIER="_dt_status" LONG-NAME="StatusType">
          <SPECIFIED-VALUES>
            <ENUM-VALUE IDENTIFIER="_ev_approved" LONG-NAME="Approved">
              <PROPERTIES><EMBEDDED-VALUE KEY="1" OTHER-CONTENT=""/></PROPERTIES>
            </ENUM-VALUE>
            <ENUM-VALUE IDENTIFIER="_ev_draft" LONG-NAME="Draft">
              <PROPERTIES><EMBEDDED-VALUE KEY="0" OTHER-CONTENT=""/></PROPERTIES>
            </ENUM-VALUE>
          </SPECIFIED-VALUES>
        </DATATYPE-DEFINITION-ENUMERATION>
      </DATATYPES>
      <SPEC-TYPES>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot_heading" LONG-NAME="Heading">
          <SPEC-ATTRIBUTES>
            <ATTRIBUTE-DEFINITION-STRING IDENTIFIER="_ad_chapter" LONG-NAME="ReqIF.ChapterName">
              <TYPE><DATATYPE-DEFINITION-STRING-REF>_dt_string</DATATYPE-DEFINITION-STRING-REF></TYPE>
            </ATTRIBUTE-DEFINITION-STRING>
          </SPEC-ATTRIBUTES>
        </SPEC-OBJECT-TYPE>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot_req" LONG-NAME="Requirement">
          <SPEC-ATTRIBUTES>
            <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="_ad_text" LONG-NAME="ReqIF.Text">
              <TYPE><DATATYPE-DEFINITION-XHTML-REF>_dt_xhtml</DATATYPE-DEFINITION-XHTML-REF></TYPE>
            </ATTRIBUTE-DEFINITION-XHTML>
            <ATTRIBUTE-DEFINITION-STRING IDENTIFIER="_ad_foreign" LONG-NAME="ReqIF.ForeignID">
              <TYPE><DATATYPE-DEFINITION-STRING-REF>_dt_string</DATATYPE-DEFINITION-STRING-REF></TYPE>
            </ATTRIBUTE-DEFINITION-STRING>
            <ATTRIBUTE-DEFINITION-REAL IDENTIFIER="_ad_factor" LONG-NAME="Factor">
              <TYPE><DATATYPE-DEFINITION-REAL-REF>_dt_real</DATATYPE-DEFINITION-REAL-REF></TYPE>
            </ATTRIBUTE-DEFINITION-REAL>
            <ATTRIBUTE-DEFINITION-ENUMERATION IDENTIFIER="_ad_status" LONG-NAME="Status" MULTI-VALUED="true">
              <TYPE><DATATYPE-DEFINITION-ENUMERATION-REF>_dt_status</DATATYPE-DEFINITION-ENUMERATION-REF></TYPE>
            </ATTRIBUTE-DEFINITION-ENUMERATION>
            <ATTRIBUTE-DEFINITION-STRING IDENTIFIER="_ad_asil" LONG-NAME="ASIL">
              <TYPE><DATATYPE-DEFINITION-STRING-REF>_dt_string</DATATYPE-DEFINITION-STRING-REF></TYPE>
            </ATTRIBUTE-DEFINITION-STRING>
            <ATTRIBUTE-DEFINITION-INTEGER IDENTIFIER="_ad_priority" LONG-NAME="Priority">
              <TYPE><DATATYPE-DEFINITION-INTEGER-REF>_dt_int</DATATYPE-DEFINITION-INTEGER-REF></TYPE>
            </ATTRIBUTE-DEFINITION-INTEGER>
            <ATTRIBUTE-DEFINITION-BOOLEAN IDENTIFIER="_ad_mandatory" LONG-NAME="IsMandatory">
              <TYPE><DATATYPE-DEFINITION-BOOLEAN-REF>_dt_bool</DATATYPE-DEFINITION-BOOLEAN-REF></TYPE>
            </ATTRIBUTE-DEFINITION-BOOLEAN>
          </SPEC-ATTRIBUTES>
        </SPEC-OBJECT-TYPE>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot_def" LONG-NAME="Definition">
          <SPEC-ATTRIBUTES>
            <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="_ad_def_text" LONG-NAME="ReqIF.Text">
              <TYPE><DATATYPE-DEFINITION-XHTML-REF>_dt_xhtml</DATATYPE-DEFINITION-XHTML-REF></TYPE>
            </ATTRIBUTE-DEFINITION-XHTML>
          </SPEC-ATTRIBUTES>
        </SPEC-OBJECT-TYPE>
        <SPEC-RELATION-TYPE IDENTIFIER="_rt_satisf" LONG-NAME="Satisfies"/>
        <SPEC-RELATION-TYPE IDENTIFIER="_rt_refine" LONG-NAME="Refinement"/>
        <SPEC-RELATION-TYPE IDENTIFIER="_rt_trace" LONG-NAME="Trace"/>
        <SPEC-RELATION-TYPE IDENTIFIER="_rt_refs" LONG-NAME="References"/>
        <SPECIFICATION-TYPE IDENTIFIER="_spectype" LONG-NAME="Specification"/>
      </SPEC-TYPES>
      <SPEC-OBJECTS>
        <SPEC-OBJECT IDENTIFIER="_parent" LONG-NAME="Controllability">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot_req</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-XHTML>
              <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>_ad_text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
              <THE-VALUE><xhtml:div>The vehicle shall remain controllable.</xhtml:div></THE-VALUE>
            </ATTRIBUTE-VALUE-XHTML>
            <ATTRIBUTE-VALUE-STRING THE-VALUE="SYS-1">
              <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>_ad_foreign</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION>
            </ATTRIBUTE-VALUE-STRING>
            <ATTRIBUTE-VALUE-REAL THE-VALUE="0.10">
              <DEFINITION><ATTRIBUTE-DEFINITION-REAL-REF>_ad_factor</ATTRIBUTE-DEFINITION-REAL-REF></DEFINITION>
            </ATTRIBUTE-VALUE-REAL>
            <ATTRIBUTE-VALUE-ENUMERATION>
              <DEFINITION><ATTRIBUTE-DEFINITION-ENUMERATION-REF>_ad_status</ATTRIBUTE-DEFINITION-ENUMERATION-REF></DEFINITION>
              <VALUES><ENUM-VALUE-REF>_ev_approved</ENUM-VALUE-REF></VALUES>
            </ATTRIBUTE-VALUE-ENUMERATION>
          </VALUES>
        </SPEC-OBJECT>
        <SPEC-OBJECT IDENTIFIER="_h" LONG-NAME="System">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot_heading</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-STRING THE-VALUE="1 Chassis">
              <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>_ad_chapter</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION>
            </ATTRIBUTE-VALUE-STRING>
          </VALUES>
        </SPEC-OBJECT>
        <SPEC-OBJECT IDENTIFIER="_abs" LONG-NAME="ABS">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot_req</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-XHTML>
              <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>_ad_text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
              <THE-VALUE><xhtml:div>The system shall <xhtml:strong>modulate</xhtml:strong> brake pressure.</xhtml:div></THE-VALUE>
            </ATTRIBUTE-VALUE-XHTML>
            <ATTRIBUTE-VALUE-ENUMERATION>
              <DEFINITION><ATTRIBUTE-DEFINITION-ENUMERATION-REF>_ad_status</ATTRIBUTE-DEFINITION-ENUMERATION-REF></DEFINITION>
              <VALUES>
                <ENUM-VALUE-REF>_ev_draft</ENUM-VALUE-REF>
                <ENUM-VALUE-REF>_ev_approved</ENUM-VALUE-REF>
              </VALUES>
            </ATTRIBUTE-VALUE-ENUMERATION>
          </VALUES>
        </SPEC-OBJECT>
        <SPEC-OBJECT IDENTIFIER="_brake" LONG-NAME="Brake">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot_req</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-XHTML>
              <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>_ad_text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
              <THE-VALUE><xhtml:div>The system shall brake.</xhtml:div></THE-VALUE>
            </ATTRIBUTE-VALUE-XHTML>
            <ATTRIBUTE-VALUE-STRING THE-VALUE="SYS-BRK-1">
              <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>_ad_foreign</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION>
            </ATTRIBUTE-VALUE-STRING>
            <ATTRIBUTE-VALUE-STRING THE-VALUE="D">
              <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>_ad_asil</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION>
            </ATTRIBUTE-VALUE-STRING>
            <ATTRIBUTE-VALUE-INTEGER THE-VALUE="2">
              <DEFINITION><ATTRIBUTE-DEFINITION-INTEGER-REF>_ad_priority</ATTRIBUTE-DEFINITION-INTEGER-REF></DEFINITION>
            </ATTRIBUTE-VALUE-INTEGER>
            <ATTRIBUTE-VALUE-BOOLEAN THE-VALUE="true">
              <DEFINITION><ATTRIBUTE-DEFINITION-BOOLEAN-REF>_ad_mandatory</ATTRIBUTE-DEFINITION-BOOLEAN-REF></DEFINITION>
            </ATTRIBUTE-VALUE-BOOLEAN>
            <ATTRIBUTE-VALUE-ENUMERATION>
              <DEFINITION><ATTRIBUTE-DEFINITION-ENUMERATION-REF>_ad_status</ATTRIBUTE-DEFINITION-ENUMERATION-REF></DEFINITION>
              <VALUES><ENUM-VALUE-REF>_ev_draft</ENUM-VALUE-REF></VALUES>
            </ATTRIBUTE-VALUE-ENUMERATION>
          </VALUES>
        </SPEC-OBJECT>
        <SPEC-OBJECT IDENTIFIER="_def" LONG-NAME="ABS term">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot_def</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-XHTML>
              <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>_ad_def_text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
              <THE-VALUE><xhtml:div>ABS means anti-lock braking.</xhtml:div></THE-VALUE>
            </ATTRIBUTE-VALUE-XHTML>
          </VALUES>
        </SPEC-OBJECT>
        <SPEC-OBJECT IDENTIFIER="_happ" LONG-NAME="Appendix">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot_heading</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-STRING THE-VALUE="Appendix">
              <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>_ad_chapter</ATTRIBUTE-DEFINITION-STRING-REF></DEFINITION>
            </ATTRIBUTE-VALUE-STRING>
          </VALUES>
        </SPEC-OBJECT>
      </SPEC-OBJECTS>
      <SPEC-RELATIONS>
        <SPEC-RELATION IDENTIFIER="_rel_brake_sat" LONG-NAME="not-the-type-name">
          <TYPE><SPEC-RELATION-TYPE-REF>_rt_satisf</SPEC-RELATION-TYPE-REF></TYPE>
          <SOURCE><SPEC-OBJECT-REF>_brake</SPEC-OBJECT-REF></SOURCE>
          <TARGET><SPEC-OBJECT-REF>_parent</SPEC-OBJECT-REF></TARGET>
        </SPEC-RELATION>
        <SPEC-RELATION IDENTIFIER="_rel_brake_ref">
          <TYPE><SPEC-RELATION-TYPE-REF>_rt_refs</SPEC-RELATION-TYPE-REF></TYPE>
          <SOURCE><SPEC-OBJECT-REF>_brake</SPEC-OBJECT-REF></SOURCE>
          <TARGET><SPEC-OBJECT-REF>_def</SPEC-OBJECT-REF></TARGET>
        </SPEC-RELATION>
        <SPEC-RELATION IDENTIFIER="_rel_abs_refn">
          <TYPE><SPEC-RELATION-TYPE-REF>_rt_refine</SPEC-RELATION-TYPE-REF></TYPE>
          <SOURCE><SPEC-OBJECT-REF>_abs</SPEC-OBJECT-REF></SOURCE>
          <TARGET><SPEC-OBJECT-REF>_brake</SPEC-OBJECT-REF></TARGET>
        </SPEC-RELATION>
        <SPEC-RELATION IDENTIFIER="_rel_parent_trace">
          <TYPE><SPEC-RELATION-TYPE-REF>_rt_trace</SPEC-RELATION-TYPE-REF></TYPE>
          <SOURCE><SPEC-OBJECT-REF>_parent</SPEC-OBJECT-REF></SOURCE>
          <TARGET><SPEC-OBJECT-REF>_abs</SPEC-OBJECT-REF></TARGET>
        </SPEC-RELATION>
        <SPEC-RELATION IDENTIFIER="_rel_def_missing">
          <TYPE><SPEC-RELATION-TYPE-REF>_rt_refs</SPEC-RELATION-TYPE-REF></TYPE>
          <SOURCE><SPEC-OBJECT-REF>_def</SPEC-OBJECT-REF></SOURCE>
          <TARGET><SPEC-OBJECT-REF>_missing</SPEC-OBJECT-REF></TARGET>
        </SPEC-RELATION>
      </SPEC-RELATIONS>
      <SPECIFICATIONS>
        <SPECIFICATION IDENTIFIER="_spec" LONG-NAME="Vehicle spec">
          <TYPE><SPECIFICATION-TYPE-REF>_spectype</SPECIFICATION-TYPE-REF></TYPE>
          <CHILDREN>
            <SPEC-HIERARCHY IDENTIFIER="_sh_h">
              <OBJECT><SPEC-OBJECT-REF>_h</SPEC-OBJECT-REF></OBJECT>
              <CHILDREN>
                <SPEC-HIERARCHY IDENTIFIER="_sh_parent">
                  <OBJECT><SPEC-OBJECT-REF>_parent</SPEC-OBJECT-REF></OBJECT>
                  <CHILDREN>
                    <SPEC-HIERARCHY IDENTIFIER="_sh_brake">
                      <OBJECT><SPEC-OBJECT-REF>_brake</SPEC-OBJECT-REF></OBJECT>
                    </SPEC-HIERARCHY>
                    <SPEC-HIERARCHY IDENTIFIER="_sh_abs">
                      <OBJECT><SPEC-OBJECT-REF>_abs</SPEC-OBJECT-REF></OBJECT>
                    </SPEC-HIERARCHY>
                  </CHILDREN>
                </SPEC-HIERARCHY>
              </CHILDREN>
            </SPEC-HIERARCHY>
          </CHILDREN>
        </SPECIFICATION>
        <SPECIFICATION IDENTIFIER="_spec_app" LONG-NAME="Other">
          <TYPE><SPECIFICATION-TYPE-REF>_spectype</SPECIFICATION-TYPE-REF></TYPE>
          <CHILDREN>
            <SPEC-HIERARCHY IDENTIFIER="_sh_app">
              <OBJECT><SPEC-OBJECT-REF>_happ</SPEC-OBJECT-REF></OBJECT>
              <CHILDREN>
                <SPEC-HIERARCHY IDENTIFIER="_sh_brake_again">
                  <OBJECT><SPEC-OBJECT-REF>_brake</SPEC-OBJECT-REF></OBJECT>
                </SPEC-HIERARCHY>
              </CHILDREN>
            </SPEC-HIERARCHY>
          </CHILDREN>
        </SPECIFICATION>
      </SPECIFICATIONS>
    </REQ-IF-CONTENT>
  </CORE-CONTENT>
</REQ-IF>
"""


def _link(link_type: str, target_id: str, role: str) -> SourceLink:
    return SourceLink(type=link_type, target_id=target_id, role=role, context=[])


def test_reqif_items_attributes_headings_and_links(tmp_path: Path):
    path = tmp_path / "vehicle.reqif"
    payload = REQIF.encode("utf-8")
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    items = read_reqif(path, corpus_root=tmp_path)
    assert read_reqif(path, corpus_root=tmp_path) == items

    assert len(items) == 6
    assert [item.item_id for item in items] == [
        "_parent",
        "_h",
        "_abs",
        "_brake",
        "_def",
        "_happ",
    ]
    assert [item.kind_hint for item in items] == [
        "requirement",
        "heading",
        "requirement",
        "requirement",
        "definition",
        "heading",
    ]
    assert [item.heading_path for item in items] == [
        ["1 Chassis"],
        [],
        ["1 Chassis", "Controllability"],
        ["1 Chassis", "Controllability"],
        [],
        [],
    ]
    assert all("Vehicle spec" not in item.heading_path for item in items)
    assert all("Appendix" not in item.heading_path for item in items)

    assert items[0].text == "The vehicle shall remain controllable."
    assert items[0].attributes == {
        "req_if_text": "The vehicle shall remain controllable.",
        "req_if_foreign_id": "SYS-1",
        "factor": "0.10",
        "status": "Approved",
    }
    assert items[0].links == [_link("traces_to", "_abs", "Trace")]

    assert items[1].text == "1 Chassis"
    assert items[1].attributes == {"req_if_chapter_name": "1 Chassis"}
    assert items[1].links == []

    assert items[2].text == "The system shall modulate brake pressure."
    assert items[2].attributes == {
        "req_if_text": "The system shall modulate brake pressure.",
        "status": "Draft,Approved",
    }
    assert items[2].links == [_link("traces_to", "_brake", "Refinement")]

    assert items[3].text == "The system shall brake."
    assert items[3].attributes == {
        "req_if_text": "The system shall brake.",
        "req_if_foreign_id": "SYS-BRK-1",
        "asil": "D",
        "priority": "2",
        "is_mandatory": "true",
        "status": "Draft",
    }
    assert items[3].links == [
        _link("traces_to", "_parent", "Satisfies"),
        _link("refers_to", "_def", "References"),
    ]

    assert items[4].text == "ABS means anti-lock braking."
    assert items[4].attributes == {"req_if_text": "ABS means anti-lock braking."}
    assert items[4].links == [_link("refers_to", "_missing", "References")]

    assert items[5].text == "Appendix"
    assert items[5].attributes == {"req_if_chapter_name": "Appendix"}
    assert items[5].links == []

    for item in items:
        assert item.source_id == "vehicle"
        assert item.release is None
        assert item.file == "vehicle.reqif"
        assert item.sha256 == digest
        assert all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in item.attributes.items()
        )
        assert all(" " not in key and key == key.lower() for key in item.attributes)
        assert all(link.context == [] for link in item.links)


def test_identical_bytes_give_identical_items(tmp_path: Path):
    payload = REQIF.encode("utf-8")
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    left_root.mkdir()
    right_root.mkdir()
    left = left_root / "vehicle.reqif"
    right = right_root / "vehicle.reqif"
    left.write_bytes(payload)
    right.write_bytes(payload)
    assert read_reqif(left, corpus_root=left_root) == read_reqif(right, corpus_root=right_root)


def test_corrupt_reqif_raises(tmp_path: Path):
    path = tmp_path / "broken.reqif"
    path.write_bytes(b"this is not a reqif\n")
    with pytest.raises(Exception):
        read_reqif(path, corpus_root=tmp_path)


def test_xhtml_embedded_object_raises(tmp_path: Path):
    # An embedded xhtml object is not text. Returning the rest would drop it.
    path = tmp_path / "object.reqif"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd" xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <CORE-CONTENT>
    <REQ-IF-CONTENT>
      <SPEC-TYPES>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot" LONG-NAME="Requirement">
          <SPEC-ATTRIBUTES>
            <ATTRIBUTE-DEFINITION-XHTML IDENTIFIER="_ad_text" LONG-NAME="ReqIF.Text">
              <TYPE><DATATYPE-DEFINITION-XHTML-REF>_dt</DATATYPE-DEFINITION-XHTML-REF></TYPE>
            </ATTRIBUTE-DEFINITION-XHTML>
          </SPEC-ATTRIBUTES>
        </SPEC-OBJECT-TYPE>
      </SPEC-TYPES>
      <SPEC-OBJECTS>
        <SPEC-OBJECT IDENTIFIER="_req" LONG-NAME="Picture">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot</SPEC-OBJECT-TYPE-REF></TYPE>
          <VALUES>
            <ATTRIBUTE-VALUE-XHTML>
              <DEFINITION><ATTRIBUTE-DEFINITION-XHTML-REF>_ad_text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>
              <THE-VALUE>
                <xhtml:div>See <xhtml:object data="pic.png" type="image/png"/>.</xhtml:div>
              </THE-VALUE>
            </ATTRIBUTE-VALUE-XHTML>
          </VALUES>
        </SPEC-OBJECT>
      </SPEC-OBJECTS>
    </REQ-IF-CONTENT>
  </CORE-CONTENT>
</REQ-IF>
""",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="embedded objects"):
        read_reqif(path, corpus_root=tmp_path)


BARE = """\
<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">
  <CORE-CONTENT>
    <REQ-IF-CONTENT>
      <SPEC-TYPES>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot" LONG-NAME="Requirement"/>
      </SPEC-TYPES>
      <SPEC-OBJECTS>
        <SPEC-OBJECT LONG-NAME="Bare">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot</SPEC-OBJECT-TYPE-REF></TYPE>
        </SPEC-OBJECT>
      </SPEC-OBJECTS>
    </REQ-IF-CONTENT>
  </CORE-CONTENT>
</REQ-IF>
"""


def test_missing_identifier_uses_source_index(tmp_path: Path):
    path = tmp_path / "bare.reqif"
    payload = BARE.encode("utf-8")
    path.write_bytes(payload)
    items = read_reqif(path, corpus_root=tmp_path)
    assert len(items) == 1
    assert items[0].item_id == "bare#0"
    assert items[0].source_id == "bare"
    assert items[0].kind_hint == "requirement"
    assert items[0].text == ""
    assert items[0].heading_path == []
    assert items[0].attributes == {}
    assert items[0].links == []
    assert items[0].release is None
    assert items[0].sha256 == hashlib.sha256(payload).hexdigest()


def test_relation_with_missing_source_raises(tmp_path: Path):
    path = tmp_path / "dangling.reqif"
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<REQ-IF xmlns="http://www.omg.org/spec/ReqIF/20110401/reqif.xsd">
  <CORE-CONTENT>
    <REQ-IF-CONTENT>
      <SPEC-TYPES>
        <SPEC-OBJECT-TYPE IDENTIFIER="_sot" LONG-NAME="Requirement"/>
        <SPEC-RELATION-TYPE IDENTIFIER="_rt" LONG-NAME="Satisfies"/>
      </SPEC-TYPES>
      <SPEC-OBJECTS>
        <SPEC-OBJECT IDENTIFIER="_keep" LONG-NAME="Keep">
          <TYPE><SPEC-OBJECT-TYPE-REF>_sot</SPEC-OBJECT-TYPE-REF></TYPE>
        </SPEC-OBJECT>
      </SPEC-OBJECTS>
      <SPEC-RELATIONS>
        <SPEC-RELATION IDENTIFIER="_rel">
          <TYPE><SPEC-RELATION-TYPE-REF>_rt</SPEC-RELATION-TYPE-REF></TYPE>
          <SOURCE><SPEC-OBJECT-REF>_gone</SPEC-OBJECT-REF></SOURCE>
          <TARGET><SPEC-OBJECT-REF>_keep</SPEC-OBJECT-REF></TARGET>
        </SPEC-RELATION>
      </SPEC-RELATIONS>
    </REQ-IF-CONTENT>
  </CORE-CONTENT>
</REQ-IF>
""",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="SPEC-RELATION"):
        read_reqif(path, corpus_root=tmp_path)
