"""Phase 1: embeddings foundation — HashingEmbedder + ingest vector round-trip."""

import io
import json
import tempfile
from pathlib import Path

import pygrap
from grap_mcp.kee import embeddings as emb_mod
from grap_mcp.kee.embeddings import HashingEmbedder, OpenAICompatEmbedder, get_embedder
from grap_mcp import ingest


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


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


def test_openai_compat_embedder_shaping_and_normalization(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        # return two unnormalized vectors, out of order, to test index sorting
        return _FakeResp(json.dumps({"data": [
            {"index": 1, "embedding": [0.0, 3.0]},
            {"index": 0, "embedding": [3.0, 4.0]},
        ]}).encode("utf-8"))

    monkeypatch.setattr(emb_mod.urllib.request, "urlopen", fake_urlopen)
    e = OpenAICompatEmbedder(base_url="http://host/v1", model="nomic-embed-text")
    out = e.embed(["a", "b"])

    assert captured["url"] == "http://host/v1/embeddings"
    assert captured["body"] == {"model": "nomic-embed-text", "input": ["a", "b"]}
    assert e.dim == 2
    assert out[0] == [0.6, 0.8]                # [3,4] L2-normalized, index 0 first
    assert abs(sum(x * x for x in out[1]) - 1.0) < 1e-9


def test_get_embedder_backend_hashing(monkeypatch):
    monkeypatch.setenv("GRAP_EMBED_BACKEND", "hashing")
    assert get_embedder().name == "hashing"


def test_get_embedder_backend_ollama_falls_back_when_unreachable(monkeypatch):
    monkeypatch.setenv("GRAP_EMBED_BACKEND", "ollama")

    def boom(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(emb_mod.urllib.request, "urlopen", boom)
    assert get_embedder().name == "hashing"     # graceful fallback


def test_ingest_without_embedder_is_unchanged(tmp_path):
    (tmp_path / "a.md").write_text("Alpha.\n\nBeta.", encoding="utf-8")
    kgdir = tempfile.mkdtemp(prefix="kee-noemb-kg-")
    kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(kgdir, 2)
    res = ingest.ingest_paths(kg, str(tmp_path))
    assert res["embedded"] is False
    assert res["nodes_added"] == 2


def test_embedder_retries_transient_then_succeeds(monkeypatch):
    """A blip against the embedding endpoint must not kill a whole run."""
    import grap_mcp.kee.embeddings as em
    monkeypatch.setattr(em.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    class _R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): self.close(); return False

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise em.urllib.error.URLError("connection refused")
        return _R(json.dumps({"data": [{"index": 0, "embedding": [1.0, 0.0]}]}).encode())

    monkeypatch.setattr(em.urllib.request, "urlopen", fake_urlopen)
    e = em.OpenAICompatEmbedder(base_url="http://x/v1", model="m")
    out = e.embed(["hello"])
    assert len(out) == 1 and calls["n"] == 3


def test_embedder_gives_up_with_clear_error(monkeypatch):
    import grap_mcp.kee.embeddings as em
    monkeypatch.setenv("GRAP_EMBED_RETRIES", "1")
    monkeypatch.setattr(em.time, "sleep", lambda *_: None)

    def fake_urlopen(req, timeout=None):
        raise em.urllib.error.URLError("refused")

    monkeypatch.setattr(em.urllib.request, "urlopen", fake_urlopen)
    e = em.OpenAICompatEmbedder(base_url="http://x/v1", model="m")
    try:
        e.embed(["hello"])
        assert False, "expected RuntimeError"
    except RuntimeError as err:
        assert "after 2 attempts" in str(err)


def test_default_embed_url_is_ipv4_literal():
    """'localhost' resolves to ::1 first on Windows and Ollama is IPv4-only,
    which hung a run in SYN_SENT for 24 minutes."""
    import grap_mcp.kee.embeddings as em
    assert "127.0.0.1" in em._DEFAULT_EMBED_BASE_URL
    assert "localhost" not in em._DEFAULT_EMBED_BASE_URL
