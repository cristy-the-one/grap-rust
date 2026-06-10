"""Phase 1 graph-augmented extraction: the live WorkingGraph."""

import pygrap  # noqa: F401  (ensures the binding is importable in this env)

from grap_mcp.kee.working_graph import WorkingGraph, Signals
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


def test_relation_with_grounded_endpoints_is_added(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    frag = {
        "source": "s#0",
        "outcomes": [{"name": "Verification", "characteristics": []},
                     {"name": "Test Plan", "characteristics": []}],
        "relations": [{"from": "Verification", "type": "uses", "to": "Test Plan"}],
    }
    wg.upsert_fragment(frag, "s#0")
    onto = wg.to_candidate_ontology("v")
    assert len(onto["relations"]) == 1
    r = onto["relations"][0]
    assert (r["from"], r["type"], r["to"]) == ("Verification", "uses", "Test Plan")
    assert r["sources"] == ["s#0"]
    assert wg.parked == []


def test_relation_with_ungrounded_endpoint_is_parked(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    frag = {
        "source": "s#0",
        "outcomes": [{"name": "Verification", "characteristics": []}],
        # "Mystery Concept" is neither an existing entity nor an outcome here
        "relations": [{"from": "Verification", "type": "uses", "to": "Mystery Concept"}],
    }
    wg.upsert_fragment(frag, "s#0")
    onto = wg.to_candidate_ontology("v")
    assert onto["relations"] == []
    assert len(wg.parked) == 1
    assert wg.parked[0]["reason"] == "ungrounded"
    # No node was created for the ungrounded endpoint.
    assert wg.node_count() == 1


class FakeEmb:
    """Embedder returning caller-controlled vectors per exact string."""
    name = "fake"
    dim = 2

    def __init__(self, mapping):
        self.mapping = mapping

    def embed(self, texts):
        return [self.mapping[t] for t in texts]


def _wg_fake(tmp_path, mapping, **kw):
    return WorkingGraph(FakeEmb(mapping), workdir=str(tmp_path), **kw)


def test_merge_threshold_too_low_wrongly_merges(tmp_path):
    # Distinct concepts at cosine 0.8; threshold 0.5 => wrongly merged into one.
    emb = {"aaa": [1.0, 0.0], "bbb": [0.8, 0.6]}
    wg = _wg_fake(tmp_path, emb, merge_threshold=0.5)
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "aaa", "characteristics": []}], "relations": []}, "s")
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "bbb", "characteristics": []}], "relations": []}, "s")
    assert wg.node_count() == 1


def test_merge_threshold_high_keeps_them_separate(tmp_path):
    emb = {"aaa": [1.0, 0.0], "bbb": [0.8, 0.6]}
    wg = _wg_fake(tmp_path, emb, merge_threshold=0.95)
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "aaa", "characteristics": []}], "relations": []}, "s")
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "bbb", "characteristics": []}], "relations": []}, "s")
    assert wg.node_count() == 2


def test_conflicting_relation_type_is_parked_and_signaled(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    base = {"outcomes": [{"name": "A", "characteristics": []}, {"name": "B", "characteristics": []}]}
    wg.upsert_fragment({"source": "s1", **base, "relations": [{"from": "A", "type": "uses", "to": "B"}]}, "s1")
    sig = wg.upsert_fragment({"source": "s2", **base, "relations": [{"from": "A", "type": "blocks", "to": "B"}]}, "s2")
    assert any(c["kind"] == "relation" for c in sig.conflicts)
    assert any(p["reason"] == "conflict" for p in wg.parked)
    # original edge preserved (first-writer-wins)
    onto = wg.to_candidate_ontology("v")
    assert [(r["from"], r["type"], r["to"]) for r in onto["relations"]] == [("A", "uses", "B")]


def test_bridge_relation_raises_correlation(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    # Build two disconnected pairs, then a relation that bridges the two components.
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "A", "characteristics": []}, {"name": "B", "characteristics": []}],
                        "relations": [{"from": "A", "type": "uses", "to": "B"}]}, "s")
    wg.upsert_fragment({"source": "s", "outcomes": [{"name": "C", "characteristics": []}, {"name": "D", "characteristics": []}],
                        "relations": [{"from": "C", "type": "uses", "to": "D"}]}, "s")
    sig = wg.upsert_fragment({"source": "s", "outcomes": [{"name": "B", "characteristics": []}, {"name": "C", "characteristics": []}],
                              "relations": [{"from": "B", "type": "feeds", "to": "C"}]}, "s")
    assert any(c["kind"] == "bridge" for c in sig.correlations)


def test_context_for_empty_graph_is_empty(tmp_path):
    wg = _wg(tmp_path)
    ctx = wg.context_for("anything")
    assert ctx.entities == [] and ctx.relations == [] and ctx.relation_vocab == []


def test_context_for_surfaces_related_known_entities(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    wg.upsert_fragment({"source": "s",
                        "outcomes": [{"name": "Verification", "characteristics": ["evidence"]},
                                     {"name": "Test Plan", "characteristics": []}],
                        "relations": [{"from": "Verification", "type": "uses", "to": "Test Plan"}]}, "s")
    # A chunk literally naming a known entity must surface it (substring path),
    # along with the relation vocabulary.
    ctx = wg.context_for("This section discusses the Test Plan and its review.")
    assert "Test Plan" in ctx.entities
    assert "uses" in ctx.relation_vocab


def test_focused_context_restricts_to_signal_entities(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    wg.upsert_fragment({"source": "s",
                        "outcomes": [{"name": "A", "characteristics": []}, {"name": "B", "characteristics": []},
                                     {"name": "Z", "characteristics": []}],
                        "relations": [{"from": "A", "type": "uses", "to": "B"}]}, "s")
    sig = Signals(conflicts=[{"kind": "relation", "names": ["A", "B"], "type": "blocks"}])
    fctx = wg.focused_context(sig)
    assert set(fctx.entities) == {"A", "B"}  # Z excluded
