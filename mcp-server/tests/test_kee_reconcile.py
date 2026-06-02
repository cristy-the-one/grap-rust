"""Phase 4: reconcile — clustering, candidate ontology, and merge_nodes stress."""

from grap_mcp.kee import reconcile as rc
from grap_mcp.kee.embeddings import HashingEmbedder


class FakeEmb:
    """Embedder returning caller-controlled vectors per exact string."""
    name = "fake"
    dim = 2

    def __init__(self, mapping):
        self.mapping = mapping

    def embed(self, texts):
        return [self.mapping[t] for t in texts]


def test_greedy_cluster_groups_similar_vectors():
    vecs = [[1, 0], [0.99, 0.01], [0, 1]]
    clusters = rc.greedy_cluster(vecs, threshold=0.9)
    assert sorted(len(c) for c in clusters) == [1, 2]


def test_tokenize():
    assert rc.tokenize("Test Plan") == "test_plan"
    assert rc.tokenize("  Review/Record ") == "review_record"


def test_reconcile_merges_duplicate_outcomes_and_records_provenance():
    # "Verification" and "Verification process" embed identically -> one outcome.
    emb = FakeEmb({
        "Verification": [1.0, 0.0],
        "Verification process": [1.0, 0.0],
        "Quality management": [0.0, 1.0],
        "test plan": [1.0, 0.0],
        "test plan document": [1.0, 0.0],
        "quality manual": [0.0, 1.0],
    })
    fragments = [
        {"source": "d#0", "outcomes": [
            {"name": "Verification", "characteristics": ["test plan"]}], "relations": []},
        {"source": "d#1", "outcomes": [
            {"name": "Verification process", "characteristics": ["test plan document"]}],
         "relations": []},
        {"source": "d#2", "outcomes": [
            {"name": "Quality management", "characteristics": ["quality manual"]}],
         "relations": []},
    ]
    onto = rc.reconcile(fragments, emb, sim=0.95)
    names = {o["name"] for o in onto["process_outcomes"]}
    assert names == {"Verification", "Quality management"}      # duplicate merged
    ids = [o["id"] for o in onto["process_outcomes"]]
    assert len(ids) == len(set(ids))                             # ids unique

    verif = next(o for o in onto["process_outcomes"] if o["name"] == "Verification")
    assert verif["required_characteristics"] == ["test_plan"]    # dup chars merged
    assert set(onto["provenance"][verif["id"]]) == {"d#0", "d#1"}  # provenance kept


def test_reconcile_keeps_distinct_outcomes_separate():
    emb = FakeEmb({"Alpha": [1.0, 0.0], "Beta": [0.0, 1.0]})
    fragments = [{"source": "s", "outcomes": [
        {"name": "Alpha", "characteristics": []},
        {"name": "Beta", "characteristics": []}], "relations": []}]
    onto = rc.reconcile(fragments, emb, sim=0.86)
    assert len(onto["process_outcomes"]) == 2


def test_stress_merge_nodes_collapses_duplicates_consistently():
    # three near-identical strings + one distinct -> exercises vector_search +
    # merge_nodes on real pygrap nodes; node count must stay consistent.
    emb = HashingEmbedder(dim=128)
    strings = ["configuration management strategy",
               "configuration management strategy",
               "configuration management strategy",
               "system qualification test specification"]
    res = rc.stress_merge_nodes(strings, emb, threshold=0.999)
    assert res["initial"] == 4
    assert res["merged"] == 2                       # 3 identical -> 2 merges
    assert res["final"] == res["initial"] - res["merged"]   # merge_nodes consistent
    assert res["clusters"] == 2
