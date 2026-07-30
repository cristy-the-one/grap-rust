"""Flattened-table detection: keep row-lists out of the ontology and the LLM budget."""

import grap_mcp.kee.tabular as tab
from grap_mcp.kee.pipeline import read_chunks

BUDGET_TABLE = ("85 01 Rambursări de credite externe 13.630.000 13.630.000 13.630.000 "
                "88 Rambursări de credite interne 8.240.000 8.240.000 8.240.000 "
                "8501 TRANSFERURI 5.618.267.739 -43.500.000 5.574.767.739 39 03 "
                "Transferuri 1.234.000 2.345.000 3.456.000 4.567.000")
NOMENCLATURE = ("Placaje și panouri 20.20.1 2020.11 Placaje 20.20.11 31410 4412(.13-.19) "
                "2020.12 Panel 20.20.12 31420 4412(.22-.99) 2020.13 Plăci din așchii "
                "de lemn PAL 20.20.13 31430 4410 2020.14 Plăci 20.20.14 31440 4411")
# Citation-dense legislation. Numeric-token-rich for the same reason a table is,
# which is why the threshold sits above this band rather than through it.
ABROGATION = ("Articolul 42 Abrogat. Art. 42 a fost abrogat de art. 107 din LEGEA "
              "nr. 303 din 28 iunie 2004, publicată în MONITORUL OFICIAL nr. 576 "
              "din 29 iunie 2004. Prevederile se aplică începând cu data publicării.")
PLAIN_LAW = ("Adunarea generală stabilește adaosurile la remunerație și criteriile de "
             "acordare a acestora pentru directorul general; examinează programele de "
             "activitate și proiectul bugetului de venituri și cheltuieli ale societății.")


def test_flattened_tables_are_detected():
    """These arrive as running text — a layout-based detector scores them 0."""
    assert tab.is_tabular(BUDGET_TABLE)
    assert tab.is_tabular(NOMENCLATURE)


def test_citation_dense_law_is_kept():
    assert not tab.is_tabular(ABROGATION)
    assert not tab.is_tabular(PLAIN_LAW)


def test_short_chunks_are_never_skipped():
    assert not tab.is_tabular("1.234 5.678 9.012")


def test_threshold_can_be_disabled(monkeypatch):
    monkeypatch.setenv("GRAP_KEE_SKIP_TABULAR", "off")
    assert not tab.is_tabular(BUDGET_TABLE)
    monkeypatch.setenv("GRAP_KEE_SKIP_TABULAR", "0.75")
    assert tab.is_tabular(BUDGET_TABLE)


def test_skipping_does_not_renumber_surviving_chunks(tmp_path, monkeypatch):
    """THE constraint. Chunk ids embed the index; renumbering after a skip would
    make every processed document look changed and force a full re-extract."""
    monkeypatch.setenv("GRAP_KEE_SKIP_TABULAR", "0.75")
    doc = tmp_path / "d.txt"
    doc.write_text("\n\n".join([PLAIN_LAW, BUDGET_TABLE, ABROGATION]), encoding="utf-8")
    stats = {}
    got = read_chunks(str(tmp_path), chunk_chars=2000, stats=stats)
    ids = [s for s, _ in got]
    assert stats["tabular_skipped"] == 1
    assert ids == ["d#0", "d#2"], ids       # the gap at #1 is the point


def test_disabled_filter_keeps_every_chunk(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAP_KEE_SKIP_TABULAR", "off")
    doc = tmp_path / "d.txt"
    doc.write_text("\n\n".join([PLAIN_LAW, BUDGET_TABLE, ABROGATION]), encoding="utf-8")
    stats = {}
    got = read_chunks(str(tmp_path), chunk_chars=2000, stats=stats)
    assert [s for s, _ in got] == ["d#0", "d#1", "d#2"]
    assert stats["tabular_skipped"] == 0


def test_enabling_the_filter_does_not_retract_already_extracted_chunks(tmp_path, monkeypatch):
    """SKIPPED IS NOT REMOVED.

    Turning the filter on made already-extracted tabular chunks vanish from
    read_chunks, which is indistinguishable from being deleted from the corpus —
    so extend_graph_ontology retracted everything they had contributed. It cost
    135 concepts and 28 edges from the live working graph before the churn gate
    rejected the proposal.
    """
    import json as _json
    from grap_mcp.kee.graph_extract import extend_graph_ontology
    from grap_mcp.kee.embeddings import HashingEmbedder
    from grap_mcp.kee.llm import StubProvider

    docs = tmp_path / "docs"; docs.mkdir()
    wd = str(tmp_path / "g")
    (docs / "d.txt").write_text("\n\n".join([PLAIN_LAW, BUDGET_TABLE]), encoding="utf-8")

    def handler(system, user):
        return _json.dumps({"outcomes": [{"name": "Alpha", "characteristics": ["x"]}],
                            "relations": []})

    # Pass 1: filter OFF — both chunks extracted.
    monkeypatch.setenv("GRAP_KEE_SKIP_TABULAR", "off")
    out1 = extend_graph_ontology(wd, str(docs), StubProvider(handler=handler),
                                 HashingEmbedder(), sim=0.9, chunk_chars=2000)
    assert out1["added"] == 2, out1
    concepts_before = out1["concepts"]

    # Pass 2: filter ON — the table is skipped. It must NOT be treated as removed.
    monkeypatch.setenv("GRAP_KEE_SKIP_TABULAR", "0.75")
    out2 = extend_graph_ontology(wd, str(docs), StubProvider(handler=handler),
                                 HashingEmbedder(), sim=0.9, chunk_chars=2000)
    assert out2["removed"] == 0, f"skipped chunk was retracted: {out2}"
    assert out2["concepts"] == concepts_before, (
        f"enabling the filter destroyed {concepts_before - out2['concepts']} concept(s)")
