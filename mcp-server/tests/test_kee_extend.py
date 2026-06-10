"""Phase 2: the extend (sync-with-retraction) orchestration."""

import json
import pygrap  # noqa: F401

from grap_mcp.kee.graph_extract import extend_graph_ontology
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.embeddings import HashingEmbedder


def _frag(outcomes, relations=None):
    return json.dumps({"outcomes": outcomes, "relations": relations or []})


def _handler(system, user):
    if "alpha" in user:
        return _frag([{"name": "Alpha", "characteristics": []}])
    if "beta" in user:
        return _frag([{"name": "Beta", "characteristics": []}])
    if "gamma" in user:
        return _frag([{"name": "Gamma", "characteristics": []}])
    return "{}"


def test_extend_new_then_unchanged_skips_llm(tmp_path):
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")

    p1 = StubProvider(handler=_handler)
    out1 = extend_graph_ontology(wd, str(docs), p1, HashingEmbedder(), sim=0.9)
    assert out1["added"] == 1 and out1["removed"] == 0
    assert any(o["name"] == "Alpha" for o in out1["ontology"]["process_outcomes"])

    p2 = StubProvider(handler=_handler)
    out2 = extend_graph_ontology(wd, str(docs), p2, HashingEmbedder(), sim=0.9)
    assert out2["added"] == 0 and out2["changed"] == 0 and out2["removed"] == 0
    assert len(p2.calls) == 0
    assert any(o["name"] == "Alpha" for o in out2["ontology"]["process_outcomes"])


def test_extend_changed_and_removed(tmp_path):
    wd = str(tmp_path / "g")
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha topic", encoding="utf-8")
    (docs / "b.txt").write_text("beta topic", encoding="utf-8")

    extend_graph_ontology(wd, str(docs), StubProvider(handler=_handler), HashingEmbedder(), sim=0.9)

    (docs / "a.txt").write_text("gamma topic", encoding="utf-8")
    (docs / "b.txt").unlink()
    out = extend_graph_ontology(wd, str(docs), StubProvider(handler=_handler), HashingEmbedder(), sim=0.9)

    names = {o["name"] for o in out["ontology"]["process_outcomes"]}
    assert names == {"Gamma"}
    assert out["changed"] == 1 and out["removed"] == 1
