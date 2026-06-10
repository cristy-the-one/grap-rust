"""Phase 2: WorkingGraph persistence + projection consistency."""

import pygrap  # noqa: F401

from grap_mcp.kee.working_graph import WorkingGraph
from grap_mcp.kee.embeddings import HashingEmbedder


def _build(workdir):
    wg = WorkingGraph(HashingEmbedder(), merge_threshold=0.95, workdir=workdir)
    wg.upsert_fragment({"source": "a#0",
                        "outcomes": [{"name": "Verification", "characteristics": ["evidence"]},
                                     {"name": "Test Plan", "characteristics": []}],
                        "relations": [{"from": "Verification", "type": "uses", "to": "Test Plan"}]}, "a#0")
    return wg


def test_save_then_open_round_trips_ontology(tmp_path):
    wd = str(tmp_path / "g")
    wg = _build(wd)
    before = wg.to_candidate_ontology("v")
    wg.save()
    wg.close()

    reopened = WorkingGraph.open(wd, HashingEmbedder(), merge_threshold=0.95)
    after = reopened.to_candidate_ontology("v")
    assert after == before


def test_projection_consistency_after_save(tmp_path):
    wd = str(tmp_path / "g")
    wg = _build(wd)
    wg.save()
    assert wg.kg.node_count() == 2
    assert wg.kg.edge_count() == 1
    wg.close()


def test_compaction_drops_retracted_concept_on_save(tmp_path):
    wd = str(tmp_path / "g")
    wg = _build(wd)
    wg.upsert_fragment({"source": "b#0", "outcomes": [{"name": "Orphan", "characteristics": []}], "relations": []}, "b#0")
    wg.retract_chunk("b#0")
    wg.save()
    wg.close()
    reopened = WorkingGraph.open(wd, HashingEmbedder(), merge_threshold=0.95)
    names = {o["name"] for o in reopened.to_candidate_ontology("v")["process_outcomes"]}
    assert names == {"Verification", "Test Plan"}
    assert reopened.kg.node_count() == 2
