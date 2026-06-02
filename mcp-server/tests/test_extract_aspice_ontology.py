"""Tests for the ASPICE ontology extractor (scripts/extract_aspice_ontology.py).

Runs against small SYNTHETIC ASPICE-shaped text only — no real PDF content — so
parsing is verified without committing any licensed document text.
"""

import sys
from pathlib import Path

# the extractor lives at <repo>/scripts/
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import extract_aspice_ontology as ex  # noqa: E402

# Invented, ASPICE-shaped sample (uses the same »« quoting + watermark lines
# the real PDF exhibits) — not copied from the standard.
SYNTH = """\
Dokument wurde bereitgestellt vom VDA-QMC Internetportal
Nur zur internen Verwendung bestimmt.
SYS.5 System Qualification Test
SYS.5.BP1 »Develop qualification test stra-
tegy«
SYS.5.BP3 »Specify acceptance criteria«
SYS.5.BP1 »Develop qualification test strategy«
Output WP 08-52 »Test plan«
Output WP 08-50 »Test specification«
SWE.4 Software Unit Verification
SWE.4.BP1 »Develop unit verification strategy«
WPC 13-19 »Review record«
"""


def test_clean_text_strips_watermark_and_normalizes_quotes_and_hyphens():
    cleaned = ex.clean_text(SYNTH)
    assert "VDA-QMC" not in cleaned
    assert "Nur zur internen" not in cleaned
    assert "»" not in cleaned and "«" not in cleaned and '"' in cleaned
    # de-hyphenated across the line break
    assert "qualification test strategy" in cleaned


def test_tokenize():
    assert ex.tokenize("Test specification") == "test_specification"
    assert ex.tokenize("  Review/Record  ") == "review_record"


def test_parse_base_practices_groups_and_dedupes():
    bps = ex.parse_base_practices(ex.clean_text(SYNTH))
    assert set(bps) == {"SYS.5", "SWE.4"}
    sys5 = bps["SYS.5"]
    assert [b[0] for b in sys5] == ["SYS.5.BP1", "SYS.5.BP3"]  # deduped + sorted
    assert sys5[0][1] == "Develop qualification test strategy"


def test_parse_wp_catalog():
    cat = ex.parse_wp_catalog(ex.clean_text(SYNTH))
    assert cat["08-52"] == "Test plan"
    assert cat["08-50"] == "Test specification"
    assert cat["13-19"] == "Review record"


def test_parse_process_names():
    names = ex.parse_process_names(ex.clean_text(SYNTH))
    assert names["SYS.5"] == "System Qualification Test"
    assert names["SWE.4"] == "Software Unit Verification"


def test_associate_wps_by_region():
    pages = [
        "SYS.5 System Qualification Test\nOutput WP 08-52 \"x\"\nOutput WP 08-50 \"y\"",
        "SWE.4 Software Unit Verification\nWPC 13-19 \"z\"",
    ]
    names = {"SYS.5": "System Qualification Test", "SWE.4": "Software Unit Verification"}
    assoc = ex._associate_wps(pages, names, toc_cutoff=0)
    assert assoc["SYS.5"] == ["08-52", "08-50"]
    assert assoc["SWE.4"] == ["13-19"]


def test_build_ontology_uses_work_products_as_characteristics():
    text = ex.clean_text(SYNTH)
    onto = ex.build_ontology(
        process_names=ex.parse_process_names(text),
        base_practices=ex.parse_base_practices(text),
        wp_catalog=ex.parse_wp_catalog(text),
        proc_wps={"SYS.5": ["08-52", "08-50"], "SWE.4": ["13-19"]},
    )
    by_id = {o["id"]: o for o in onto["process_outcomes"]}
    assert by_id["SYS.5"]["name"] == "System Qualification Test"
    assert by_id["SYS.5"]["required_characteristics"] == ["test_plan", "test_specification"]
    assert by_id["SWE.4"]["required_characteristics"] == ["review_record"]
    # mapping-hint keywords drawn from name + BP titles
    hint = next(h for h in onto["mapping_hints"] if h["outcome"] == "SYS.5")
    kws = hint["indicators"][0]["keywords"]
    assert "qualification" in kws and "verification" not in kws
