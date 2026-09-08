import re

import pytest

from grap_mcp.kee.chunking import chunk_spans
from grap_mcp.kee.graph_extract import extend_graph_ontology
from grap_mcp.kee.graph_store import GraphStore
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.pipeline import read_chunks


def test_soft_limit_preserves_rule_and_its_condition():
    rule = "Consiliul poate numi un director general până la ocuparea postului conform legii."
    text = "Introducere. " + rule + " Următoarea regulă."
    spans = chunk_spans(text, 40)
    assert rule in [text[s.start:s.end] for s in spans]
    assert not any(s.forced_end for s in spans)


def test_abbreviations_and_article_headings_stay_with_statement():
    text = "Articolul 5. Legea nr. 101/1998 se aplică societății S.A. în condițiile art. 2. Următoarea frază."
    chunks = [text[s.start:s.end] for s in chunk_spans(text, 40)]
    assert chunks[0].startswith("Articolul 5. Legea nr. 101/1998")
    assert chunks[0].endswith("art. 2.")
    assert chunks[1] == "Următoarea frază."


@pytest.mark.parametrize("text", ["", " \n\n ", "🧪"*130, "cuvânt "*100,
    "Titlu\r\n\r\nArticolul 1\nText legal.\n\nArticolul 2\nAlt text legal."])
def test_exact_source_coverage_and_hard_bound(text):
    spans = chunk_spans(text, 20)
    assert all(0 < s.end-s.start <= 60 for s in spans)
    assert all(a.end <= b.start for a,b in zip(spans,spans[1:]))
    assert re.sub(r"\s", "", "".join(text[s.start:s.end] for s in spans)) == re.sub(r"\s", "", text)
    if len(text.strip()) > 120 and "." not in text:
        assert any(s.forced_end for s in spans)


def test_reader_and_durable_strategy_resumption(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "act.txt").write_text("Consiliul poate numi directorul până la ocuparea postului. Altă regulă.", encoding="utf-8")
    workdir = tmp_path / "graph"
    provider = StubProvider(default='{"outcomes":[],"relations":[]}')
    first = extend_graph_ontology(str(workdir), str(docs), provider, HashingEmbedder(),
                                 chunk_chars=25, chunk_strategy="statements")
    assert first["added"] > 0
    before = len(provider.calls)
    # Omission inherits the store's policy; no accidental re-extraction.
    extend_graph_ontology(str(workdir), str(docs), provider, HashingEmbedder(), chunk_chars=25)
    assert len(provider.calls) == before
    with GraphStore(str(workdir / "graph_store.db")) as store:
        assert store.load().chunk_strategy == "statements"
    with pytest.raises(ValueError, match="fresh workdir"):
        extend_graph_ontology(str(workdir), str(docs), provider, HashingEmbedder(), chunk_strategy="legacy")
    assert len(provider.calls) == before
    stats = {}
    read_chunks(str(docs), chunk_chars=10, chunk_strategy="statements", stats=stats)
    assert stats["forced_split_ids"]


def test_invalid_limits_fail_without_hanging(tmp_path):
    with pytest.raises(ValueError):
        chunk_spans("abc", 0)
    with pytest.raises(ValueError):
        read_chunks(str(tmp_path), chunk_chars=0)


def test_article_59_from_live_probe_keeps_the_predicate_complete():
    from grap_mcp.ingest import chunk_text
    rule = "Articolul 59 Comisia centrala de arbitraj se numeşte şi îşi desfăşoară activitatea potrivit legii."
    assert len(chunk_text(rule, max_chars=75)) > 1
    assert [rule[s.start:s.end] for s in chunk_spans(rule, 75)] == [rule]
