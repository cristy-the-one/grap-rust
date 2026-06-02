"""Phase 1: embeddings foundation — HashingEmbedder + ingest vector round-trip."""

import tempfile
from pathlib import Path

import pygrap
from grap_mcp.kee.embeddings import HashingEmbedder, get_embedder
from grap_mcp import ingest


def test_hashing_embedder_is_deterministic_and_normalized():
    emb = HashingEmbedder(dim=64)
    a = emb.embed(["verification method specified"])[0]
    b = emb.embed(["verification method specified"])[0]
    assert a == b                      # deterministic across calls
    assert len(a) == 64
    norm = sum(x * x for x in a) ** 0.5
    assert abs(norm - 1.0) < 1e-9      # L2-normalized


def test_hashing_embedder_distinguishes_texts():
    emb = HashingEmbedder(dim=128)
    v1, v2 = emb.embed(["test plan and acceptance criteria",
                        "configuration management strategy"])
    cos = sum(x * y for x, y in zip(v1, v2))
    assert cos < 0.5                   # clearly different texts -> low similarity


def test_get_embedder_returns_something_usable():
    emb = get_embedder()               # local if available, else hashing
    out = emb.embed(["hello world"])
    assert len(out) == 1 and len(out[0]) > 0


def test_ingest_with_embedder_populates_vectors(tmp_path):
    # two clearly different paragraphs -> two chunks
    (tmp_path / "doc.md").write_text(
        "The verification method is specified for each requirement.\n\n"
        "Configuration management baselines are established for the project.",
        encoding="utf-8")
    kgdir = tempfile.mkdtemp(prefix="kee-emb-kg-")
    kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(kgdir, 2)
    emb = HashingEmbedder(dim=128)

    res = ingest.ingest_paths(kg, str(tmp_path), embedder=emb)
    assert res["embedded"] is True
    assert res["nodes_added"] == 2

    # querying with the embedding of one chunk should rank that chunk's node top
    q = emb.embed(["verification method specified for each requirement"])[0]
    hits = kg.vector_search(q, 2)
    assert hits, "vector_search returned no hits — embeddings not stored"
    top_node = hits[0][0]
    assert kg.get_node_text(top_node).startswith("The verification method")


def test_ingest_without_embedder_is_unchanged(tmp_path):
    (tmp_path / "a.md").write_text("Alpha.\n\nBeta.", encoding="utf-8")
    kgdir = tempfile.mkdtemp(prefix="kee-noemb-kg-")
    kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(kgdir, 2)
    res = ingest.ingest_paths(kg, str(tmp_path))
    assert res["embedded"] is False
    assert res["nodes_added"] == 2
