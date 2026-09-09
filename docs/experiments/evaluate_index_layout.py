"""Model-free retrieval invariance on full Romanian-law source documents.

This tests engine behavior, not legal relevance or interpretation accuracy.
All graphs are temporary. Source files and production graphs are read-only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import tempfile
import time

import pygrap


QUERIES = [
    "credit bancar", "termen", "obligatii", "drepturi", "sanatate",
    "asigurari sociale", "contract", "procedura", "instanta", "impozit",
    "autoritate", "exceptie", "se interzice", "raspundere",
]


def evaluate(corpus, output, count=96):
    files = sorted(corpus.glob("*.json"))
    selected = sorted(random.Random(20260909).sample(files, min(count, len(files))))
    documents, sources = [], []
    for path in selected:
        raw = path.read_bytes()
        text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
        assert isinstance(text, str) and text.strip(), path
        documents.append(text)
        sources.append({"file": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "characters": len(text)})
    assert documents, "No source documents found"

    def results(graph):
        return {
            query: [(h.node, h.text_score) for h in graph.search_and_traverse(
                query, max_seeds=len(documents), max_depth=0,
                max_results=len(documents), min_strength=0.0,
            )] for query in QUERIES
        }

    checks = []

    def compare(name, actual, expected):
        mismatches = [q for q in QUERIES if actual[q] != expected[q]]
        checks.append({"check": name, "mismatched_queries": mismatches})
        if mismatches:
            raise AssertionError(f"{name}: {mismatches}")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="grap-layout-") as tmp:
        root = Path(tmp)
        baseline = pygrap.IndexedKnowledgeGraph(str(root / "single"))
        segmented = pygrap.IndexedKnowledgeGraph.with_merge_policy(str(root / "segmented"), 2)
        for i, text in enumerate(documents):
            assert baseline.add_node(text) == i
            assert segmented.add_node(text) == i
            if (i + 1) % 8 == 0:
                segmented.save()
        expected = results(baseline)
        compare("buffer_vs_tiered_segments", results(segmented), expected)
        baseline.save()
        segmented.save()
        compare("buffer_vs_single_flush", results(baseline), expected)
        segmented = pygrap.IndexedKnowledgeGraph(str(root / "segmented"))
        compare("segmented_reload", results(segmented), expected)

        # Same live revisions in differently laid-out indexes. Replacement must
        # remove old postings and old document lengths from ranking statistics.
        for i in range(0, len(documents), 9):
            baseline.update_node(i, documents[i] + " termen procedural")
            segmented.update_node(i, documents[i] + " termen procedural")
        for i in range(4, len(documents), 11):
            baseline.remove_node(i)
            segmented.remove_node(i)
        revised = results(baseline)
        compare("revisions_and_deletes", results(segmented), revised)
        segmented.save()
        reloaded = pygrap.IndexedKnowledgeGraph(str(root / "segmented"))
        compare("revised_reload", results(reloaded), revised)
        hit_counts = {query: len(rows) for query, rows in expected.items()}
        del baseline, segmented, reloaded

    report = {
        "scope": "Storage-layout and revision invariance; no claim of semantic accuracy or corpus-wide completeness.",
        "selection": "96 documents sampled without replacement from sorted JSON paths using seed 20260909; full text_complet content.",
        "source_count": len(sources), "sources": sources, "queries": QUERIES,
        "hit_counts": hit_counts, "checks": checks,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "sources"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.corpus, args.output)
