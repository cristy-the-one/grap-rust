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


class CountingEmb:
    """Embedder that records how many HTTP-shaped calls it received."""
    name = "counting"
    dim = 2

    def __init__(self):
        self.calls = 0
        self.texts = 0

    def embed(self, texts):
        texts = list(texts)
        self.calls += 1
        self.texts += len(texts)
        # deterministic, distinct-ish vectors
        return [[float(len(t) % 7), float(sum(map(ord, t)) % 11)] for t in texts]


def test_fragment_embeds_in_one_batched_call(tmp_path):
    """Resolution must not make one embed round-trip per name: a fragment with
    several distinct names is embedded in a single batched call."""
    emb = CountingEmb()
    wg = WorkingGraph(emb, workdir=str(tmp_path), merge_threshold=0.99)
    frag = {
        "source": "s#0",
        "outcomes": [{"name": "Alpha One", "characteristics": []},
                     {"name": "Beta Two", "characteristics": []},
                     {"name": "Gamma Three", "characteristics": []}],
        "relations": [{"from": "Alpha One", "type": "uses", "to": "Delta Four"}],
    }
    wg.upsert_fragment(frag, "s#0")
    # 4 distinct names, but they must not cost 4 separate round-trips.
    assert emb.calls == 1, f"expected a single batched embed call, got {emb.calls}"
    assert emb.texts == 4


def test_repeated_names_reuse_cached_vectors(tmp_path):
    """A name already embedded in an earlier fragment is not re-embedded."""
    emb = CountingEmb()
    wg = WorkingGraph(emb, workdir=str(tmp_path), merge_threshold=0.99)
    base = {"outcomes": [{"name": "Alpha One", "characteristics": []}], "relations": []}
    wg.upsert_fragment({"source": "s1", **base}, "s1")
    after_first = emb.texts
    wg.upsert_fragment({"source": "s2", **base}, "s2")
    assert emb.texts == after_first, "same name should not be embedded twice"


def test_self_loop_relation_is_parked_not_added(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    frag = {
        "source": "s#0",
        "outcomes": [{"name": "Verification", "characteristics": []}],
        # Both endpoints resolve to the same concept -> degenerate self-loop.
        "relations": [{"from": "Verification", "type": "uses", "to": "Verification"}],
    }
    wg.upsert_fragment(frag, "s#0")
    onto = wg.to_candidate_ontology("v")
    assert onto["relations"] == []
    assert len(wg.parked) == 1
    assert wg.parked[0]["reason"] == "self_loop"


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


def test_retract_changed_chunk_drops_its_facts(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    wg.upsert_fragment({"source": "s#0",
                        "outcomes": [{"name": "A", "characteristics": ["old"]},
                                     {"name": "B", "characteristics": []}],
                        "relations": [{"from": "A", "type": "uses", "to": "B"}]}, "s#0")
    wg.retract_chunk("s#0")
    onto = wg.to_candidate_ontology("v")
    assert onto["process_outcomes"] == []
    assert onto["relations"] == []


def test_retract_keeps_facts_with_other_provenance(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.95)
    base = {"outcomes": [{"name": "A", "characteristics": []}, {"name": "B", "characteristics": []}],
            "relations": [{"from": "A", "type": "uses", "to": "B"}]}
    wg.upsert_fragment({"source": "s1", **base}, "s1")
    wg.upsert_fragment({"source": "s2", **base}, "s2")
    wg.retract_chunk("s1")
    onto = wg.to_candidate_ontology("v")
    names = {o["name"] for o in onto["process_outcomes"]}
    assert names == {"A", "B"}
    assert [(r["from"], r["type"], r["to"]) for r in onto["relations"]] == [("A", "uses", "B")]
    a = next(o for o in onto["process_outcomes"] if o["name"] == "A")
    assert onto["provenance"][a["id"]] == ["s2"]


def test_retract_drops_only_the_chunks_characteristic(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.5)
    wg.upsert_fragment({"source": "s1", "outcomes": [{"name": "Test Plan", "characteristics": ["coverage"]}], "relations": []}, "s1")
    wg.upsert_fragment({"source": "s2", "outcomes": [{"name": "test plan", "characteristics": ["schedule"]}], "relations": []}, "s2")
    wg.retract_chunk("s1")
    onto = wg.to_candidate_ontology("v")
    assert len(onto["process_outcomes"]) == 1
    po = onto["process_outcomes"][0]
    assert "schedule" in po["required_characteristics"]
    assert "coverage" not in po["required_characteristics"]
