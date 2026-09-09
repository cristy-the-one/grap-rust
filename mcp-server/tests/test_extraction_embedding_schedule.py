import json

import pytest

from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.graph_extract import graph_extract_ontology
from grap_mcp.kee.llm import StubProvider


class CountedEmbedder(HashingEmbedder):
    def __init__(self):
        super().__init__(64)
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return super().embed(texts)


@pytest.mark.parametrize("width,expected_calls", [(1, 5), (3, 2)])
def test_extraction_combines_names_and_next_window_queries(tmp_path, width, expected_calls):
    embedder = CountedEmbedder()
    names = ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"]
    chunks = [(f"doc#{i}", f"  source-{i} describes {name}\n") for i, name in enumerate(names)]
    def reply(system, user):
        # Inspect the source text, not names in the retrieved graph context.
        i = next(i for i in range(5) if f"source-{i}" in user)
        return json.dumps({"outcomes": [{"name": names[i]}], "relations": []})
    result = graph_extract_ontology(chunks, StubProvider(handler=reply), embedder,
                                    sim=1.1, concurrency=width, relook_ratio=0,
                                    workdir=str(tmp_path))
    assert not result["failures"]
    assert len(result["ontology"]["process_outcomes"]) == 5
    assert len(embedder.calls) == expected_calls
    # Exact query strings must be cached, including leading/trailing whitespace.
    flattened = [text for batch in embedder.calls for text in batch]
    assert len(flattened) == len(set(flattened))


@pytest.mark.parametrize("width", [1, 3])
def test_schedule_preserves_prompts_receipts_and_relooks(tmp_path, monkeypatch, width):
    from grap_mcp.kee.working_graph import WorkingGraph
    chunks = [(f"doc#{i}", f"source-{i}: Alpha uses Beta.") for i in range(6)]
    def run(path):
        provider = StubProvider(handler=lambda s, u: json.dumps({
            "outcomes": [{"name": "Alpha"}, {"name": "Beta"}],
            "relations": [{"from": "Alpha", "type": "uses" if "source-0" in u else "blocks", "to": "Beta"}]}))
        result = graph_extract_ontology(chunks, provider, CountedEmbedder(),
                                        concurrency=width, relook_ratio=1, sim=1.1,
                                        workdir=str(path))
        return result, sorted(provider.calls)
    actual, prompts = run(tmp_path / "new")
    monkeypatch.setattr(WorkingGraph, "prefetch_extraction", lambda *args: None)
    expected, old_prompts = run(tmp_path / "old")
    assert actual == expected
    assert prompts == old_prompts
    assert actual["relooks"] > 0


def test_incomplete_prefetch_never_caches_misaligned_vectors():
    from grap_mcp.kee.working_graph import WorkingGraph
    class ShortEmbedder(CountedEmbedder):
        def embed(self, texts):
            return super().embed(texts)[:-1]
    with WorkingGraph(ShortEmbedder()) as graph:
        graph.prefetch_extraction([{"outcomes": [{"name": "Alpha"}, {"name": "Beta"}]}])
        assert graph._vec_cache == {}
