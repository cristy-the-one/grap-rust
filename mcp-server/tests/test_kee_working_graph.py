"""Phase 1 graph-augmented extraction: the live WorkingGraph."""

import pygrap  # noqa: F401  (ensures the binding is importable in this env)

from grap_mcp.kee.working_graph import WorkingGraph
from grap_mcp.kee.embeddings import HashingEmbedder


def _wg(tmp_path, **kw):
    return WorkingGraph(HashingEmbedder(), workdir=str(tmp_path), **kw)


def test_variants_collapse_to_one_node_with_aliases(tmp_path):
    # Two surface forms that the hashing embedder maps near-identically should
    # resolve to a single node carrying BOTH aliases.
    wg = _wg(tmp_path, merge_threshold=0.5)
    frag1 = {"source": "a#0", "outcomes": [{"name": "Test Plan", "characteristics": ["coverage"]}], "relations": []}
    frag2 = {"source": "b#0", "outcomes": [{"name": "test plan", "characteristics": ["schedule"]}], "relations": []}
    wg.upsert_fragment(frag1, "a#0")
    wg.upsert_fragment(frag2, "b#0")
    assert wg.node_count() == 1
    onto = wg.to_candidate_ontology("v")
    assert len(onto["process_outcomes"]) == 1
    # both characteristics survived, provenance from both chunks
    oid = onto["process_outcomes"][0]["id"]
    assert set(onto["provenance"][oid]) == {"a#0", "b#0"}


def test_distinct_concepts_stay_separate(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.9)
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "alpha beta gamma", "characteristics": []}], "relations": []}, "s")
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "delta epsilon zeta", "characteristics": []}], "relations": []}, "s")
    assert wg.node_count() == 2
