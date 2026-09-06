"""Build and audit the full local citation/text graph without any LLM calls."""
import collections
import json
from pathlib import Path
import time

from grap_mcp.application import GrapApplication
from grap_mcp.kee import legal


def main():
    out = Path("docs/experiments")
    started = time.perf_counter()
    workspace = Path(".logs/real-law-product-workspace").resolve()
    assert Path.cwd().resolve() in workspace.parents
    app = GrapApplication(str(workspace), role="operator")
    result = app.build_legal_graph("real-data-law-ro", max_files=0, with_relations=False)
    assert result["status"] == "ok", result
    result["seconds"] = round(time.perf_counter() - started, 3)
    # Check a body-only term against the actual persisted Rust index, after reopen.
    import pygrap
    kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(str(Path(result["citation_report"]).parent), 2)
    hits = kg.search_and_traverse("vărsămintele", max_seeds=5, required_properties=[],
                                  min_strength=0., max_depth=0, max_results=5,
                                  text_weight=1., graph_weight=0.)
    assert hits, "body text must remain searchable after native graph reload"
    result["reopened_body_search_hits"] = len(hits)
    acts = legal.load_acts("real-data-law-ro")
    report = legal.citation_report(acts)
    by_id = {a["id"]:a for a in acts}
    for ref in report["references"]:
        assert by_id[ref["source"]]["text"][ref["start"]:ref["end"]] == ref["quote"]
    audit = {"acts":len(acts), "resolved_pairs":len(report["pairs"]),
             "reference_statuses":dict(collections.Counter(r["status"] for r in report["references"])),
             "ambiguous_keys":report["ambiguous_keys"], "unidentified_acts":report["unidentified_acts"],
             "heading_effective_year_differences":sum(bool(a["year"] and a["effective_date"] and a["year"] != a["effective_date"][:4]) for a in acts),
             "in_force_fields":dict(collections.Counter(str(a["in_force"]) for a in acts)),
             "sample_references":report["references"][:40]}
    for name, data in (("real-law-full-graph-2026-09.json", result), ("real-law-citations-after-2026-09.json", audit)):
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding="utf-8")
    print(json.dumps({key:value for key,value in result.items() if key not in ("nodes","edges")}))


if __name__ == "__main__":
    main()
