from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.working_graph import WorkingGraph


class CountedSources(dict):
    visited = 0

    def items(self):
        for item in super().items():
            self.visited += 1
            yield item


def test_new_chunk_does_not_scan_existing_concept_provenance():
    with WorkingGraph(HashingEmbedder(64), merge_threshold=1.1) as graph:
        graph.upsert_fragment({"outcomes": [{"name": "Existing"}], "relations": []}, "old")
        graph.node_sources = CountedSources(graph.node_sources)
        graph.replace_fragment({"outcomes": [], "relations": []}, "new")
        assert graph.canonical == {0: "Existing"}
        assert graph.node_sources.visited == 0


def test_reopened_source_replacement_retracts_old_and_new_contributions(tmp_path):
    with WorkingGraph(HashingEmbedder(64), workdir=str(tmp_path), merge_threshold=1.1) as graph:
        graph.upsert_fragment({"outcomes": [{"name": "Plan", "characteristics": ["old"]}], "relations": []}, "source")
        graph.save()  # no successful extraction manifest is required to preserve provenance
    with WorkingGraph.open(str(tmp_path), HashingEmbedder(64), merge_threshold=1.1) as graph:
        graph.upsert_fragment({"outcomes": [{"name": "Plan", "characteristics": ["new"]}], "relations": []}, "source")
        graph.upsert_fragment({"outcomes": [{"name": "Plan", "characteristics": ["shared"]}], "relations": []}, "other")
        graph.replace_fragment({"outcomes": [], "relations": []}, "source")
        assert graph.chars == {0: ["shared"]}
        assert graph.node_sources == {0: {"other"}}


def test_promoted_endpoint_is_retracted_with_its_last_supporting_source():
    with WorkingGraph(HashingEmbedder(64), merge_threshold=1.1) as graph:
        for i in range(2):
            graph.upsert_fragment({"outcomes": [{"name": f"Order {i}"}], "relations": [
                {"from": f"Order {i}", "type": "issued_by", "to": "Council"}]}, f"source-{i}")
        assert graph.promote_parked_endpoints(min_refs=2)["promoted"] == 1
        graph.retract_chunk("source-0")
        assert "Council" in graph.canonical.values()
        graph.retract_chunk("source-1")
        assert not graph.canonical
        assert graph.kg.node_count() == 0
        assert graph.kg.edge_count() == 0
