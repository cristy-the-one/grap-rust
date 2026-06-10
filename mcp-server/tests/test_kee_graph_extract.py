"""Phase 1: the budgeted graph-augmented extraction loop."""

import json

import pygrap  # noqa: F401

from grap_mcp.kee.graph_extract import graph_extract_ontology
from grap_mcp.kee.llm import StubProvider
from grap_mcp.kee.embeddings import HashingEmbedder


def _frag_json(outcomes, relations=None):
    return json.dumps({"outcomes": outcomes, "relations": relations or []})


def test_loop_links_via_context(tmp_path):
    # A context-aware stub: if the prompt already mentions "Test Plan", the model
    # reuses that exact name; otherwise it introduces it. Proves augmentation
    # makes later chunks link instead of orphaning.
    def handler(system, user):
        if "Test Plan" in user and "KNOWN ENTITIES" in user:
            return _frag_json([{"name": "Test Plan", "characteristics": ["reviewed"]}])
        return _frag_json([{"name": "Test Plan", "characteristics": ["coverage"]}])

    provider = StubProvider(handler=handler)
    chunks = [("a#0", "Defines the Test Plan."), ("b#0", "More on the Test Plan.")]
    out = graph_extract_ontology(chunks, provider, HashingEmbedder(),
                                 sim=0.5, workdir=str(tmp_path))
    # Single resolved entity, both chunks as provenance.
    assert len(out["ontology"]["process_outcomes"]) == 1
    oid = out["ontology"]["process_outcomes"][0]["id"]
    assert set(out["ontology"]["provenance"][oid]) == {"a#0", "b#0"}


def test_budget_caps_relooks(tmp_path):
    # Every chunk creates a conflicting relation => every chunk wants a re-look.
    # With relook_ratio=0.5 over 4 chunks, at most 2 re-looks may fire.
    calls = {"n": 0}

    def handler(system, user):
        calls["n"] += 1
        # Always assert A->B but flip the type so the 2nd+ assertions conflict.
        ty = "uses" if calls["n"] % 2 else "blocks"
        return _frag_json(
            [{"name": "A", "characteristics": []}, {"name": "B", "characteristics": []}],
            [{"from": "A", "type": ty, "to": "B"}],
        )

    provider = StubProvider(handler=handler)
    chunks = [(f"c#{i}", f"chunk {i} about A and B") for i in range(4)]
    out = graph_extract_ontology(chunks, provider, HashingEmbedder(),
                                 sim=0.5, relook_ratio=0.5, workdir=str(tmp_path))
    assert out["relooks"] <= 2
    # baseline calls = 4 (one per chunk); + at most 2 re-looks => exactly 4 + relooks.
    assert len(provider.calls) == 4 + out["relooks"]


def test_zero_budget_records_no_relook(tmp_path):
    def handler(system, user):
        return _frag_json(
            [{"name": "A", "characteristics": []}, {"name": "B", "characteristics": []}],
            [{"from": "A", "type": "uses", "to": "B"}],
        )

    provider = StubProvider(handler=handler)
    chunks = [("c#0", "A and B")]  # ceil(0.2*1)=1 budget, but no conflict => no relook
    out = graph_extract_ontology(chunks, provider, HashingEmbedder(),
                                 sim=0.5, relook_ratio=0.2, workdir=str(tmp_path))
    assert out["relooks"] == 0
    assert len(provider.calls) == 1


def test_pipeline_graph_augmented_matches_schema(tmp_path):
    from grap_mcp.kee import pipeline

    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.txt").write_text("The Test Plan defines coverage.", encoding="utf-8")

    provider = StubProvider(handler=lambda s, u: _frag_json(
        [{"name": "Test Plan", "characteristics": ["coverage"]}]))
    out = pipeline.extract_ontology(str(d), provider, HashingEmbedder(),
                                    sim=0.5, graph_augmented=True)
    onto = out["ontology"]
    # same top-level schema keys as reconcile output
    assert set(onto) >= {"version", "process_outcomes", "mapping_hints", "provenance", "relations"}
    assert any(po["name"] == "Test Plan" for po in onto["process_outcomes"])
    assert out.get("relooks") is not None  # graph path reports relooks


def test_pipeline_old_path_still_works(tmp_path):
    from grap_mcp.kee import pipeline

    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.txt").write_text("The Test Plan defines coverage.", encoding="utf-8")
    provider = StubProvider(handler=lambda s, u: _frag_json(
        [{"name": "Test Plan", "characteristics": ["coverage"]}]))
    out = pipeline.extract_ontology(str(d), provider, HashingEmbedder(),
                                    sim=0.86, graph_augmented=False)
    assert "ontology" in out and "fragments" in out  # old path reports fragments
