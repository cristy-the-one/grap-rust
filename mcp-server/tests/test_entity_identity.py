"""Similarity can retrieve candidates; incompatible identities cannot merge."""
import pytest

from grap_mcp.kee.working_graph import WorkingGraph
from grap_mcp.kee.reconcile import _dedup_strings


class SimilarEmbedder:
    name = "adversarial-similarity"
    dim = 3

    def embed(self, texts):
        return [[1., 0., 0.] for _ in texts]


@pytest.mark.parametrize("a,b", [
    ("Legea nr. 101/1998", "Legea nr. 58/1998"),
    ("Normele nr. 4/1992", "Normele nr. 4/1998"),
    ("Ministerului Educației Naționale", "ministrului educației naționale"),
    ("Legea nr. 2/1999", "Normele nr. 2/1999"),
])
def test_similar_distinct_identities_keep_nodes_and_relation(a, b):
    with WorkingGraph(SimilarEmbedder()) as graph:
        graph.upsert_fragment({"outcomes": [{"name": n, "characteristics": []} for n in (a,b)],
                               "relations": [{"from": a, "type": "references", "to": b}]}, "source")
        assert graph.node_count() == 2
        assert len(graph.relations) == 1
    assert len(_dedup_strings([a,b], SimilarEmbedder(), .86)) == 2


def test_alias_cannot_bridge_incompatible_numbered_names():
    with WorkingGraph(SimilarEmbedder()) as graph:
        for i, name in enumerate(("Legea", "Legea nr. 101/1998", "Legea nr. 58/1998")):
            graph.upsert_fragment({"outcomes": [{"name": name, "characteristics": []}], "relations": []}, str(i))
        assert graph.node_count() == 2
