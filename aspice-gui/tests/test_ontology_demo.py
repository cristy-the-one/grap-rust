"""Self-contained ontology demo: corpus, snapshot, endpoints."""

from pathlib import Path

CORPUS = Path(__file__).resolve().parents[2] / "demo" / "corpus" / "ro-law"


def test_rolaw_corpus_is_law_shaped_and_readable():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp-server"))
    import json
    from grap_mcp.kee.pipeline import read_chunks

    files = sorted(CORPUS.glob("*.json"))
    assert len(files) >= 3, "expect at least 3 ro-law sample files"
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        assert obj.get("id") and obj.get("titlu") and obj.get("text_complet")

    chunks = read_chunks(str(CORPUS))
    assert chunks, "read_chunks must yield chunks from the ro-law corpus"
    labels = [src for src, _ in chunks]
    assert any(l.split("#")[0] for l in labels)
