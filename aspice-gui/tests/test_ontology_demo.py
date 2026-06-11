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


def test_ontology_snapshot_maps_names_to_oids():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.ontology_view import ontology_snapshot

    export = {
        "status": "ok",
        "ontology": {
            "version": "v",
            "process_outcomes": [
                {"id": "operator_de_date", "name": "Operator de date",
                 "description": "...", "required_characteristics": ["securitate"]},
                {"id": "persoana_vizata", "name": "Persoana vizata",
                 "description": "...", "required_characteristics": []},
            ],
            "provenance": {"operator_de_date": ["L-2001-001#0"],
                           "persoana_vizata": ["L-2010-077#0", "L-2001-001#0"]},
            "relations": [{"from": "Operator de date", "type": "raspunde",
                           "to": "Persoana vizata", "sources": ["L-2010-077#0"]}],
        },
        "added": 3, "changed": 0, "removed": 0, "concepts": 2, "edges": 1,
        "relooks": 0, "parked": 0,
    }
    snap = ontology_snapshot(export)
    ids = {n["id"] for n in snap["nodes"]}
    assert ids == {"operator_de_date", "persoana_vizata"}
    op = next(n for n in snap["nodes"] if n["id"] == "operator_de_date")
    assert op["name"] == "Operator de date"
    assert op["characteristics"] == ["securitate"]
    assert op["provenance"] == ["L-2001-001#0"]
    assert snap["edges"] == [{"source": "operator_de_date", "type": "raspunde",
                              "target": "persoana_vizata", "sources": ["L-2010-077#0"]}]
    assert snap["stats"]["concepts"] == 2 and snap["stats"]["edges"] == 1


def test_ontology_snapshot_drops_unresolvable_edge():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.ontology_view import ontology_snapshot
    export = {"ontology": {"process_outcomes": [{"id": "a", "name": "A",
              "required_characteristics": []}], "provenance": {"a": []},
              "relations": [{"from": "A", "type": "x", "to": "Ghost", "sources": []}]}}
    snap = ontology_snapshot(export)
    assert snap["edges"] == []
