"""Phase 5/6: end-to-end pipeline — deterministic (stub provider + hashing emb)."""

import json

from grap_mcp.kee import pipeline
from grap_mcp.kee.embeddings import HashingEmbedder
from grap_mcp.kee.llm import StubProvider
from grap_mcp import ontology as onto_loader


def _handler(system, user):
    """Return a canned fragment keyed by which doc's text is in the prompt."""
    if "qualification" in user.lower():
        return json.dumps({"outcomes": [
            {"name": "System Qualification Test",
             "characteristics": ["test plan", "test specification"]}],
            "relations": []})
    if "quality" in user.lower():
        return json.dumps({"outcomes": [
            {"name": "Quality Assurance",
             "characteristics": ["quality plan"]}],
            "relations": []})
    return "{}"


def test_pipeline_end_to_end_and_candidate_is_loadable(tmp_path):
    (tmp_path / "a.md").write_text(
        "This section is about system qualification testing and its evidence.",
        encoding="utf-8")
    (tmp_path / "b.md").write_text(
        "This section is about quality assurance in the project.",
        encoding="utf-8")

    provider = StubProvider(handler=_handler)
    embedder = HashingEmbedder(dim=128)
    result = pipeline.extract_ontology(str(tmp_path), provider, embedder, sim=0.9)

    onto = result["ontology"]
    names = {o["name"] for o in onto["process_outcomes"]}
    assert names == {"System Qualification Test", "Quality Assurance"}
    assert result["chunks"] == 2 and result["fragments"] == 2

    # provenance points back to the source chunks
    qa = next(o for o in onto["process_outcomes"] if o["name"] == "Quality Assurance")
    assert onto["provenance"][qa["id"]] == ["b#0"]

    # the candidate YAML must load as a real ontology (closes the loop)
    yaml_text = pipeline.to_yaml(onto)
    assert "provenance" not in yaml_text          # provenance stripped from loadable file
    out = tmp_path / "candidate.yaml"
    out.write_text(yaml_text, encoding="utf-8")
    loaded = onto_loader.load_ontology_from_file(str(out))
    loaded_names = {o.name for o in loaded.process_outcomes}
    assert loaded_names == {"System Qualification Test", "Quality Assurance"}


def test_pipeline_empty_when_provider_yields_nothing(tmp_path):
    (tmp_path / "a.md").write_text("Some unrelated text.", encoding="utf-8")
    provider = StubProvider(default="{}")     # no fragments
    onto = pipeline.extract_ontology(str(tmp_path), provider, HashingEmbedder())["ontology"]
    assert onto["process_outcomes"] == []
