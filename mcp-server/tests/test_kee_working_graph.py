"""Phase 1 graph-augmented extraction: the live WorkingGraph."""

import pytest
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


def _endpoints(wg, relation):
    return (wg._concept_to_node[relation["from_id"]],
            wg._concept_to_node[relation["to_id"]])


def test_relation_encoded_as_property_name_with_value_true(tmp_path):
    """The relation is the edge property name, value true, so the engine can
    prune on the name. The legacy form {"rel": "<verb>"} does not.
    ``depends_on`` is a schema relation; a free verb takes the role form instead."""
    wg = _wg(tmp_path, merge_threshold=0.95)
    wg.upsert_fragment({
        "source": "s#0",
        "outcomes": [{"name": "Verification", "characteristics": []},
                     {"name": "Test Plan", "characteristics": []}],
        "relations": [{"from": "Verification", "type": "depends_on", "to": "Test Plan"}],
    }, "s#0")
    src, dst = _endpoints(wg, wg.relations[0])
    assert wg.kg.neighbors_by_relation(src, "depends_on", True) == [(dst, 1.0)]
    assert wg.kg.neighbors_by_relation(src, "rel", "depends_on") == []
    hits = wg.kg.search_and_traverse(
        "Verification", max_seeds=5, required_properties=["depends_on"],
        min_strength=0.0, max_depth=2)
    assert any(h.node == dst and h.depth == 1 for h in hits)
    blocked = wg.kg.search_and_traverse(
        "Verification", max_seeds=5, required_properties=["rel"],
        min_strength=0.0, max_depth=2)
    assert all(h.node != dst for h in blocked)


def test_reopened_graph_projects_relation_as_property_name(tmp_path):
    """A saved graph stores the verb in the edge row. Reopening rebuilds the
    engine projection, which is how an existing graph picks up the encoding."""
    wd = str(tmp_path / "g")
    wg = WorkingGraph(HashingEmbedder(), merge_threshold=0.95, workdir=wd)
    wg.upsert_fragment({
        "source": "s#0",
        "outcomes": [{"name": "Verification", "characteristics": []},
                     {"name": "Test Plan", "characteristics": []}],
        "relations": [{"from": "Verification", "type": "depends_on", "to": "Test Plan"}],
    }, "s#0")
    wg.save()
    wg.close()
    reopened = WorkingGraph.open(wd, HashingEmbedder(), merge_threshold=0.95)
    src, dst = _endpoints(reopened, reopened.relations[0])
    assert reopened.kg.neighbors_by_relation(src, "depends_on", True) == [(dst, 1.0)]
    assert reopened.kg.neighbors_by_relation(src, "rel", "depends_on") == []


def test_migrate_legacy_rel_property_rewrites_edges(tmp_path, capsys):
    """An existing projection that still has {"rel": "<verb>"} is rewritten
    in place from the relation list. A second run finds nothing left to migrate."""
    wg = _wg(tmp_path, merge_threshold=0.95)
    wg.upsert_fragment({
        "source": "s#0",
        "outcomes": [{"name": "Verification", "characteristics": []},
                     {"name": "Test Plan", "characteristics": []},
                     {"name": "Requirement", "characteristics": []}],
        "relations": [
            {"from": "Verification", "type": "depends_on", "to": "Test Plan"},
            {"from": "Test Plan", "type": "verifies", "to": "Requirement"},
        ],
    }, "s#0")
    assert len(wg.relations) == 2
    wg.kg.clear_edges()
    for edge in wg.relations:
        src, dst = _endpoints(wg, edge)
        wg.kg.add_edge(src, dst, 1.0, {"rel": edge["type"]})
        assert wg.kg.neighbors_by_relation(src, "rel", edge["type"]) == [(dst, 1.0)]
    report = wg.migrate_edge_encoding()
    assert report == {"migrated": 2, "rewritten": 2, "n": 2}
    printed = capsys.readouterr().out
    assert "migrated=2" in printed and "rewritten=2" in printed and "n=2" in printed
    for edge in wg.relations:
        src, dst = _endpoints(wg, edge)
        assert wg.kg.neighbors_by_relation(src, edge["type"], True) == [(dst, 1.0)]
        assert wg.kg.neighbors_by_relation(src, "rel", edge["type"]) == []
    again = wg.migrate_edge_encoding()
    assert again == {"migrated": 0, "rewritten": 2, "n": 2}
    assert wg.kg.edge_count() == 2


def test_migrate_empty_graph_prints_zero_denominator(tmp_path, capsys):
    wg = _wg(tmp_path)
    report = wg.migrate_edge_encoding()
    assert report == {"migrated": 0, "rewritten": 0, "n": 0}
    assert "n=0" in capsys.readouterr().out
    assert wg.kg.edge_count() == 0


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


def test_identifier_names_resolve_by_exact_string(tmp_path):
    # Identical vectors must not merge two identifier-shaped names. The same
    # identifier seen twice is one node.
    same = [1.0, 0.0]
    emb = {"Com_SendSignal": list(same), "Com_SendSignalGroup": list(same)}
    wg = _wg_fake(tmp_path, emb, merge_threshold=0.86)
    wg.upsert_fragment(
        {"source": "s1", "outcomes": [{"name": "Com_SendSignal", "characteristics": []}], "relations": []},
        "s1",
    )
    wg.upsert_fragment(
        {"source": "s2", "outcomes": [{"name": "Com_SendSignalGroup", "characteristics": []}], "relations": []},
        "s2",
    )
    assert wg.node_count() == 2
    wg.upsert_fragment(
        {"source": "s3", "outcomes": [{"name": "Com_SendSignal", "characteristics": []}], "relations": []},
        "s3",
    )
    assert wg.node_count() == 2


@pytest.mark.parametrize("left,right", [
    ("VehSpdFL", "VehSpdFR"),
    ("BrakePedalPosition", "BrakePedalPressure"),
    ("vehicle_speed", "vehicle_speed_raw"),
    ("/ISignal/SigA", "/ISignal/SigB"),
])
def test_automotive_identifier_pairs_stay_two_nodes(tmp_path, left, right):
    # Identical vectors must not merge these identifier shapes.
    same = [1.0, 0.0]
    wg = _wg_fake(tmp_path, {left: list(same), right: list(same)}, merge_threshold=0.86)
    wg.upsert_fragment(
        {"source": "s1", "outcomes": [{"name": left, "characteristics": []}], "relations": []},
        "s1",
    )
    wg.upsert_fragment(
        {"source": "s2", "outcomes": [{"name": right, "characteristics": []}], "relations": []},
        "s2",
    )
    assert wg.node_count() == 2


@pytest.mark.parametrize("act,reference", [
    ("Legea", "Legea nr. 101/1998"),
    ("Lege", "Lege nr. 58/1998"),
    ("OUG", "OUG nr. 12/2020"),
    ("HG", "HG nr. 7/1999"),
    ("Ordonanța", "Ordonanța nr. 33/2000"),
    ("Decizia", "Decizia nr. 4/2001"),
])
def test_legal_act_reference_is_not_an_identifier(tmp_path, act, reference):
    # A legal act reference still similarity-merges. Identical vectors, one node.
    same = [1.0, 0.0]
    wg = _wg_fake(tmp_path, {act: list(same), reference: list(same)}, merge_threshold=0.86)
    wg.upsert_fragment(
        {"source": "s1", "outcomes": [{"name": act, "characteristics": []}], "relations": []},
        "s1",
    )
    wg.upsert_fragment(
        {"source": "s2", "outcomes": [{"name": reference, "characteristics": []}], "relations": []},
        "s2",
    )
    assert wg.node_count() == 1


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


def test_sends_and_receives_both_survive_upsert_save_open_and_retract(tmp_path):
    """F26: two relations between the same nodes are kept, including provenance.

    ``EcuA sends FrameX`` and ``EcuA receives FrameX`` come from different
    chunks. Both are present after upsert, after save, and after open.
    Retracting the sends chunk removes only that relation.
    """
    wd = str(tmp_path / "wd")
    emb = HashingEmbedder()
    wg = WorkingGraph(emb, workdir=wd, merge_threshold=0.99)
    outcomes = [
        {"name": "EcuA", "characteristics": []},
        {"name": "FrameX", "characteristics": []},
    ]
    wg.upsert_fragment({
        "source": "sends#0",
        "outcomes": outcomes,
        "relations": [{"from": "EcuA", "type": "sends", "to": "FrameX"}],
    }, "sends#0")
    wg.upsert_fragment({
        "source": "receives#0",
        "outcomes": outcomes,
        "relations": [{"from": "EcuA", "type": "receives", "to": "FrameX"}],
    }, "receives#0")

    def relations(graph):
        onto = graph.to_candidate_ontology("v")
        return sorted((r["from"], r["type"], r["to"]) for r in onto["relations"])

    assert relations(wg) == [("EcuA", "receives", "FrameX"), ("EcuA", "sends", "FrameX")]
    wg.save()
    wg.close()

    opened = WorkingGraph.open(wd, emb, merge_threshold=0.99)
    assert relations(opened) == [("EcuA", "receives", "FrameX"), ("EcuA", "sends", "FrameX")]
    opened.retract_chunk("sends#0")
    assert relations(opened) == [("EcuA", "receives", "FrameX")]


def test_second_relation_type_between_the_same_pair_is_kept(tmp_path):
    """A second relation type between the same nodes is kept, not parked.

    F26 replaced the one-type-per-pair rule. ``uses`` and ``blocks`` are two
    relations. Neither is a conflict, and neither is dropped.
    """
    wg = _wg(tmp_path, merge_threshold=0.95)
    base = {"outcomes": [{"name": "A", "characteristics": []}, {"name": "B", "characteristics": []}]}
    wg.upsert_fragment({"source": "s1", **base, "relations": [{"from": "A", "type": "uses", "to": "B"}]}, "s1")
    sig = wg.upsert_fragment({"source": "s2", **base, "relations": [{"from": "A", "type": "blocks", "to": "B"}]}, "s2")
    # Nothing is parked or dropped. The second-look signal still fires once,
    # as it did before the key changed; whether it should for a legitimate
    # pair is left for tuning against labelled data.
    assert [c["type"] for c in sig.conflicts] == ["blocks"]
    assert not any(p["reason"] == "conflict" for p in wg.parked)
    onto = wg.to_candidate_ontology("v")
    assert sorted((r["from"], r["type"], r["to"]) for r in onto["relations"]) == [
        ("A", "blocks", "B"), ("A", "uses", "B")]


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


def test_promote_parked_endpoints_recovers_relations(tmp_path):
    """Endpoints that recur across chunks become concepts, and the relations
    parked because of them are recovered."""
    wg = _wg(tmp_path, merge_threshold=0.99)
    # "GUVERNUL" is never an outcome, but three acts point at it.
    for i in range(3):
        wg.upsert_fragment({
            "source": f"s{i}",
            "outcomes": [{"name": f"Actul {i}", "characteristics": []}],
            "relations": [{"from": f"Actul {i}", "type": "emit", "to": "GUVERNUL"}],
        }, f"s{i}")

    assert len(wg.relations) == 0, "relations should start out parked"
    assert sum(1 for p in wg.parked if p["reason"] == "ungrounded") == 3

    res = wg.promote_parked_endpoints(min_refs=2)

    assert res["promoted"] == 1                 # GUVERNUL became a concept
    assert res["relations_recovered"] == 3      # all three edges recovered
    assert res["still_parked"] == 0
    assert len(wg.relations) == 3
    assert any(c == "GUVERNUL" for c in wg.canonical.values())


def test_promote_respects_min_refs_gate(tmp_path):
    """A one-off endpoint stays parked: frequency is the governance gate."""
    wg = _wg(tmp_path, merge_threshold=0.99)
    wg.upsert_fragment({
        "source": "s0",
        "outcomes": [{"name": "Actul 0", "characteristics": []}],
        "relations": [{"from": "Actul 0", "type": "emit", "to": "Typo Entity Xyzzy"}],
    }, "s0")

    res = wg.promote_parked_endpoints(min_refs=2)
    assert res["promoted"] == 0
    assert res["relations_recovered"] == 0
    assert res["still_parked"] == 1             # nothing lost — still queued
    assert not any(c == "Typo Entity Xyzzy" for c in wg.canonical.values())


def test_promote_keeps_provenance_and_is_idempotent(tmp_path):
    wg = _wg(tmp_path, merge_threshold=0.99)
    for i in range(2):
        wg.upsert_fragment({
            "source": f"s{i}",
            "outcomes": [{"name": f"Actul {i}", "characteristics": []}],
            "relations": [{"from": f"Actul {i}", "type": "publicat", "to": "MONITORUL OFICIAL"}],
        }, f"s{i}")

    wg.promote_parked_endpoints(min_refs=2)
    node = next(n for n, c in wg.canonical.items() if c == "MONITORUL OFICIAL")
    assert wg.node_sources.get(node) == {"s0", "s1"}, "promoted concept keeps its sources"

    # running it again must not duplicate concepts or edges
    rels, concepts = len(wg.relations), len(wg.canonical)
    again = wg.promote_parked_endpoints(min_refs=2)
    assert again["promoted"] == 0
    assert len(wg.relations) == rels and len(wg.canonical) == concepts


def test_promote_works_on_graph_reopened_from_disk(tmp_path):
    """Promotion works after restoring provenance from the authoritative store."""
    wd = str(tmp_path / "wd")
    emb = HashingEmbedder()
    wg = WorkingGraph(emb, workdir=wd, merge_threshold=0.99)
    for i in range(2):
        wg.upsert_fragment({
            "source": f"s{i}",
            "outcomes": [{"name": f"Actul {i}", "characteristics": []}],
            "relations": [{"from": f"Actul {i}", "type": "emit", "to": "GUVERNUL"}],
        }, f"s{i}")
    wg.save()
    wg.close()

    reopened = WorkingGraph.open(wd, emb, merge_threshold=0.99)
    assert set(reopened._chunk_contrib) == {"s0", "s1"}
    assert sum(1 for p in reopened.parked if p["reason"] == "ungrounded") == 2

    res = reopened.promote_parked_endpoints(min_refs=2)
    assert res["promoted"] == 1
    assert res["relations_recovered"] == 2
    assert res["still_parked"] == 0


def test_promotion_is_amortized_by_max_promote(tmp_path):
    """A bulk promotion blows the adoption churn budget and, because a rejected
    proposal never becomes active, the gate then stays tripped forever. Capping
    per run keeps each increment inside the budget; the rest stay parked."""
    wg = _wg(tmp_path, merge_threshold=0.99)
    # 10 distinct endpoints, each referenced twice -> all eligible
    for i in range(10):
        for j in range(2):
            wg.upsert_fragment({
                "source": f"s{i}-{j}",
                "outcomes": [{"name": f"Actul {i}-{j}", "characteristics": []}],
                "relations": [{"from": f"Actul {i}-{j}", "type": "emit", "to": f"ORGAN {i}"}],
            }, f"s{i}-{j}")

    res = wg.promote_parked_endpoints(min_refs=2, max_promote=3)
    assert res["promoted"] == 3, res
    assert res["still_parked"] > 0, "un-promoted endpoints must remain parked, not vanish"

    # a later run picks up more, so nothing is lost permanently
    res2 = wg.promote_parked_endpoints(min_refs=2, max_promote=3)
    assert res2["promoted"] == 3


# ---- dead-letter queue -----------------------------------------------------
#
# `20300#2` failed extraction in every run for weeks: a failed chunk's hash is
# not recorded, so it was re-sent to the LLM on every batch forever. These pin
# the bound — and the exemption that keeps an outage from quarantining the corpus.

def test_deterministic_failure_is_quarantined_after_max_attempts(tmp_path):
    wg = _wg(tmp_path)
    assert wg.record_chunk_failure("20300#2", "h1", "unparsed", max_attempts=3) is False
    assert wg.record_chunk_failure("20300#2", "h1", "unparsed", max_attempts=3) is False
    assert wg.record_chunk_failure("20300#2", "h1", "unparsed", max_attempts=3) is True
    q = wg.quarantined_chunks()
    assert [r["chunk_id"] for r in q] == ["20300#2"]
    assert q[0]["attempts"] == 3 and q[0]["reason"] == "unparsed"


def test_edited_chunk_gets_a_fresh_set_of_attempts(tmp_path):
    wg = _wg(tmp_path)
    for _ in range(3):
        wg.record_chunk_failure("c#0", "old", "unparsed", max_attempts=3)
    assert wg.quarantined_chunks()
    # same chunk id, new content -> the verdict was about the old text
    assert wg.record_chunk_failure("c#0", "new", "unparsed", max_attempts=3) is False
    assert wg.quarantined_chunks() == []


def test_success_clears_the_dead_letter(tmp_path):
    wg = _wg(tmp_path)
    wg.record_chunk_failure("c#0", "h", "empty", max_attempts=3)
    wg.clear_chunk_failure("c#0")
    assert wg.chunk_failures == {}


def test_quarantine_survives_save_and_reopen(tmp_path):
    wg = _wg(tmp_path)
    wg.upsert_fragment({"source": "a#0", "outcomes": [{"name": "Thing", "characteristics": []}],
                        "relations": []}, "a#0")
    for _ in range(3):
        wg.record_chunk_failure("bad#1", "h", "unparsed", max_attempts=3)
    wg.save()
    wg.close()
    wg2 = WorkingGraph.open(str(tmp_path), HashingEmbedder())
    try:
        assert [r["chunk_id"] for r in wg2.quarantined_chunks()] == ["bad#1"]
        assert wg2.chunk_failures["bad#1"]["attempts"] == 3
    finally:
        wg2.close()


# ---- characteristic-token memo ---------------------------------------------
#
# to_candidate_ontology called _dedup_strings for EVERY concept on EVERY batch:
# 6,233 embedding round-trips at 0.226s = 23.5 minutes per batch, re-deriving
# 32,528 characteristics of which ~50 were new. It was also the term that made
# per-chunk cost grow with ontology size.

class _CountingEmbedder(HashingEmbedder):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.texts = 0

    def embed(self, texts):
        self.calls += 1
        self.texts += len(texts)
        return super().embed(texts)


def test_candidate_ontology_does_not_re_embed_unchanged_characteristics(tmp_path):
    emb = _CountingEmbedder()
    wg = WorkingGraph(emb, workdir=str(tmp_path), merge_threshold=0.9)
    try:
        for i in range(5):
            wg.upsert_fragment({"source": f"s#{i}",
                                "outcomes": [{"name": f"Concept {i}",
                                              "characteristics": [f"alpha {i}", f"beta {i}"]}],
                                "relations": []}, f"s#{i}")
        wg.to_candidate_ontology("v1")
        after_first = emb.calls
        assert after_first > 0
        # Nothing changed: the second export must not touch the embedder at all.
        wg.to_candidate_ontology("v2")
        assert emb.calls == after_first, (
            f"re-derived unchanged characteristics: {emb.calls - after_first} extra call(s)")
    finally:
        wg.close()


def test_memo_survives_reopen(tmp_path):
    """The cache is a sqlite sidecar, so a fresh process still skips the work —
    the loop driver restarts every launch, so an in-memory-only memo would pay
    the full cost on the first batch of every run."""
    emb = _CountingEmbedder()
    wg = WorkingGraph(emb, workdir=str(tmp_path), merge_threshold=0.9)
    try:
        wg.upsert_fragment({"source": "s#0",
                            "outcomes": [{"name": "Thing", "characteristics": ["x", "y"]}],
                            "relations": []}, "s#0")
        wg.to_candidate_ontology("v1")
        wg.save()
    finally:
        wg.close()
    emb2 = _CountingEmbedder()
    wg2 = WorkingGraph.open(str(tmp_path), emb2, merge_threshold=0.9)
    try:
        before = emb2.calls
        wg2.to_candidate_ontology("v2")
        assert emb2.calls == before, "reopened graph re-embedded cached characteristics"
    finally:
        wg2.close()


def test_changed_characteristics_are_re_derived(tmp_path):
    """The memo is keyed on content, so a real change must miss it."""
    emb = _CountingEmbedder()
    wg = WorkingGraph(emb, workdir=str(tmp_path), merge_threshold=0.9)
    try:
        wg.upsert_fragment({"source": "s#0",
                            "outcomes": [{"name": "Thing", "characteristics": ["x"]}],
                            "relations": []}, "s#0")
        wg.to_candidate_ontology("v1")
        base = emb.calls
        wg.upsert_fragment({"source": "s#1",
                            "outcomes": [{"name": "Thing", "characteristics": ["brand new"]}],
                            "relations": []}, "s#1")
        wg.to_candidate_ontology("v2")
        assert emb.calls > base, "changed characteristics were served from the memo"
    finally:
        wg.close()


def test_promotion_prefetches_every_endpoint_it_will_resolve(tmp_path):
    """_resolve_endpoint falls through to a SINGLE-TEXT embed for any name the
    alias index misses. Measured on the live pilot: 6,727 uncached endpoints at
    0.178s each = 1,194s, matching the 1,241s the phase actually took. Batched,
    the same names cost 74s."""
    emb = _CountingEmbedder()
    wg = WorkingGraph(emb, workdir=str(tmp_path), merge_threshold=0.99)
    try:
        # Park a pile of relations whose endpoints never appear as outcomes.
        for i in range(40):
            wg.upsert_fragment(
                {"source": f"s#{i}",
                 "outcomes": [{"name": f"Act {i}", "characteristics": []}],
                 "relations": [{"from": f"Act {i}", "type": "emis de",
                                "to": f"Body {i % 7}"}]}, f"s#{i}")
        assert any(p.get("reason") == "ungrounded" for p in wg.parked)
        emb.calls = 0
        wg.promote_parked_endpoints(min_refs=2, max_promote=5)
        # The endpoints are embedded in bounded batches, not one request each.
        distinct = len({p["to"] for p in wg.parked} | {"Body %d" % i for i in range(7)})
        assert emb.calls <= 4, (
            f"{emb.calls} embed calls for ~{distinct} endpoints — prefetch is not covering "
            "the re-attempt loop")
    finally:
        wg.close()


def test_structural_labels_excluded_from_export_but_kept_in_the_graph(tmp_path):
    """Intake filtering stops NEW structural outcomes; it does nothing about the
    ones already there, and those were the ones doing the damage — `anexa_1`
    produced the same 41 regressions across three consecutive canary failures.
    Excluding at export (rather than retracting) keeps the gathered data and
    makes the decision reversible."""
    wg = WorkingGraph(HashingEmbedder(), workdir=str(tmp_path), merge_threshold=0.99)
    try:
        wg.upsert_fragment(
            {"source": "s#0",
             "outcomes": [{"name": "Anexa 1", "characteristics": ["I. Total dobânzi"]},
                          {"name": "Legea nr. 9/1998", "characteristics": ["publicată"]}],
             "relations": [{"from": "Anexa 1", "type": "face parte din",
                            "to": "Legea nr. 9/1998"}]}, "s#0")
        onto = wg.to_candidate_ontology("v")
        assert [o["name"] for o in onto["process_outcomes"]] == ["Legea nr. 9/1998"]
        assert onto["structural_excluded"] == 1
        # an edge to a non-exported outcome would dangle
        assert onto["relations"] == []
        # ...and nothing was destroyed
        assert "Anexa 1" in set(wg.canonical.values())
        assert wg.chars[[n for n, v in wg.canonical.items() if v == "Anexa 1"][0]]
    finally:
        wg.close()


@pytest.mark.parametrize("left,right", [
    ("Normele nr. 2/1999", "Normelor nr. 2/1999"),
    ("art. 5 alin. 2", "art. 5 alin. (2)"),
    ("Banca Comerciala S.A.", "Băncii Comerciale S.A."),
])
def test_prose_with_dots_and_digits_is_not_an_identifier(left, right):
    """No domain wording is special-cased: ordinary phrases still merge by similarity."""
    from grap_mcp.kee.working_graph import _contains_identifier
    assert not _contains_identifier(left)
    assert not _contains_identifier(right)


@pytest.mark.parametrize("name", [
    "VehSpdFL", "BrakePedalPosition", "vehicle_speed", "/ISignal/SigA", "Com_SendSignal",
    "the Com_SendSignal function", "signal VehSpdFL of the brake ECU", "see [SWS_Com_00123]",
    "path /ISignal/SigA here",
])
def test_identifiers_alone_and_inside_phrases(name):
    from grap_mcp.kee.working_graph import _contains_identifier
    assert _contains_identifier(name)


def test_seventy_free_verbs_do_not_exhaust_the_engine_names(tmp_path):
    """F27: the engine allows 64 property names. Free verbs must not use them up."""
    from grap_mcp.kee.working_graph import _relation_filter
    wg = _wg(tmp_path)
    for i in range(70):
        wg.upsert_fragment({
            "source": f"d#{i}",
            "outcomes": [{"name": f"Thing{i}a", "characteristics": []},
                         {"name": f"Thing{i}b", "characteristics": []}],
            "relations": [{"from": f"Thing{i}a", "type": f"verb number {i}", "to": f"Thing{i}b"}],
        }, f"d#{i}")
    assert len(wg.relations) == 70
    for edge in wg.relations:
        name, value = _relation_filter(edge["type"])
        assert name == "role"
        src = wg._concept_to_node[edge["from_id"]]
        dst = wg._concept_to_node[edge["to_id"]]
        assert dst in [nid for nid, _strength in wg.kg.neighbors_by_relation(src, name, value)]
        assert dst in [nid for nid, _strength in wg.kg.neighbors_by_relation(src, "refers_to", True)]


def test_schema_relation_is_its_own_property_name(tmp_path):
    from grap_mcp.kee.working_graph import _relation_filter, _relation_properties
    assert _relation_properties("part_of") == {"part_of": True}
    assert _relation_filter("part_of") == ("part_of", True)
    assert _relation_properties("emite") == {"refers_to": True, "role": "emite"}
