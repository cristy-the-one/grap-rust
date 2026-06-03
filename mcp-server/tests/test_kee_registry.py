"""Persistent concept registry: dedup within a run and across runs."""

from grap_mcp.kee.registry import ConceptRegistry
from grap_mcp.kee.embeddings import HashingEmbedder


def test_registry_dedups_within_and_across_runs(tmp_path):
    emb = HashingEmbedder(dim=128)
    reg = ConceptRegistry(str(tmp_path / "reg"), emb, threshold=0.95)

    r1 = reg.resolve_many(["Test Plan", "Test Plan", "Quality Assurance"])
    assert r1["Test Plan"]["id"] == r1["Test Plan"]["id"]            # stable
    assert r1["Test Plan"]["id"] != r1["Quality Assurance"]["id"]    # distinct
    assert reg.stats()["concepts"] == 2                              # the dup collapsed
    reg.save()

    # cross-run: reopen the same dir; existing concepts must be reused
    reg2 = ConceptRegistry(str(tmp_path / "reg"), emb, threshold=0.95)
    r2 = reg2.resolve_many(["Test Plan", "Brand New Concept Zzz"])
    assert r2["Test Plan"]["new"] is False                          # matched persisted
    assert r2["Brand New Concept Zzz"]["new"] is True
    assert reg2.stats()["concepts"] == 3
