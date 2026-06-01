"""Regression tests: freshly-added KG content is searchable in the same session.

These lock the guarantee investigated for the reasoner-determinism work: a node
added to an on-disk-loaded KG is immediately findable by search — in-process,
across threads (the MCP runtime dispatches sync tools on anyio worker threads),
and as reasoner evidence — so gap detection sees real project knowledge added
during the session.
"""

import os
import tempfile
import threading

import pygrap

from grap_mcp import ontology as onto_loader
from grap_mcp import reasoner

ONTOLOGY = os.path.join(os.path.dirname(__file__), "..", "..",
                        "docs", "examples", "minimal-aspice-ontology.yaml")


def _new_kg():
    return pygrap.IndexedKnowledgeGraph.with_merge_policy(
        os.path.join(tempfile.mkdtemp(), "kg"), 2)


def _search(kg, query):
    return [h.node for h in kg.search_and_traverse(query, 5, [], 0.0, 1, 5, 0.7, 0.3)]


def test_added_node_searchable_immediately():
    kg = _new_kg()
    nid = kg.add_node("zzqxk_unique verification method specified", label="req")
    assert nid in _search(kg, "zzqxk_unique")


def test_added_node_searchable_cross_thread():
    kg = _new_kg()
    nid = kg.add_node("zzqxk_unique cross-thread evidence", label="req")
    out = []
    t = threading.Thread(target=lambda: out.append(_search(kg, "zzqxk_unique")))
    t.start()
    t.join()
    assert nid in out[0]


def test_real_evidence_found_as_reasoner_signal_in_session():
    # Project the ontology, then add a real requirement covering a characteristic.
    onto = onto_loader.load_ontology_from_file(ONTOLOGY)
    kg = _new_kg()
    proj = onto_loader.project_ontology_to_grap(onto, kg)
    excluded = set(proj["projected_node_ids"])
    req = kg.add_node(
        "the verification method is specified for each system requirement", label="req-cov")

    signals = reasoner._lexical_signals(
        kg, onto, "SYS.5.BP3", "verification_method_specified",
        reasoner.DEFAULT_WEIGHTS, excluded)

    # The freshly-added real node is searchable as evidence (and not excluded as
    # system scaffolding).
    assert any(s["node"] == req for s in signals)
