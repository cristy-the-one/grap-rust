#!/usr/bin/env python3
"""Curate a bulk-extracted candidate ontology into a GUI-usable slice (no LLM).

A full-corpus run produces tens of thousands of outcomes/relations — too many to
load for gap detection or render in the graph. This trims it:

  - relations: drop degenerate/stop types (copulas, symbols, generic verbs) and,
    optionally, keep only the top-N most-connected concepts (a connected core).
  - outcomes: drop empty ones and cap to the most-specified N (by characteristic
    count), keeping mapping_hints in sync.

Output stays load_ontology-compatible.

  python scripts/curate_ontology.py out/legal-ontology
  python scripts/curate_ontology.py out/legal-ontology --top-concepts 250 --max-outcomes 600
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))
from grap_mcp.kee import pipeline  # noqa: E402  (for to_yaml — schema-consistent output)

# Generic/degenerate relation types with no graph value (RO copulas + EN generics).
DEFAULT_DROP_TYPES = {
    "are", "este", "sunt", "fi", "fost", "e", "a", "ale", "al",
    "has", "have", "is", "are_a", "of", "the", "and", "or", "to", "in",
}


def _clean_type(t: str) -> str:
    return (t or "").strip().lower()


def filter_relations(rels, drop_types=None, min_type_len=2, top_concepts=0):
    """Drop stop/symbol/short relation types; optionally keep only relations
    whose both endpoints are among the top-N most-connected concepts."""
    drop = {(_clean_type(t)) for t in (drop_types or DEFAULT_DROP_TYPES)}
    kept = []
    for r in rels:
        ty = _clean_type(r.get("type"))
        f, t = (r.get("from") or "").strip(), (r.get("to") or "").strip()
        if not (f and t and ty):
            continue
        if ty in drop or len(ty) < min_type_len:
            continue
        if not re.search(r"[a-z0-9ăâîșțéè]", ty):   # symbol-only types like "=", "=="
            continue
        kept.append({"from": f, "type": r.get("type").strip(), "to": t,
                     "sources": r.get("sources", [])})

    if top_concepts and top_concepts > 0:
        deg = collections.Counter()
        for r in kept:
            deg[r["from"]] += 1
            deg[r["to"]] += 1
        top = {name for name, _ in deg.most_common(top_concepts)}
        kept = [r for r in kept if r["from"] in top and r["to"] in top]
    return kept


def curate_outcomes(process_outcomes, mapping_hints, drop_empty=True, max_outcomes=0):
    """Drop empty-characteristic outcomes; cap to the most-specified N; keep the
    matching mapping_hints."""
    outs = list(process_outcomes)
    if drop_empty:
        outs = [o for o in outs if o.get("required_characteristics")]
    if max_outcomes and max_outcomes > 0:
        outs = sorted(outs, key=lambda o: len(o.get("required_characteristics", [])),
                      reverse=True)[:max_outcomes]
    keep_ids = {o["id"] for o in outs}
    hints = [h for h in (mapping_hints or []) if h.get("outcome") in keep_ids]
    return outs, hints


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("in_dir", help="directory with candidate-ontology.yaml + relations.json")
    ap.add_argument("--out", default=None, help="output dir (default = in_dir)")
    ap.add_argument("--drop-types", default=None,
                    help="comma-separated relation types to drop (default: copulas/generics)")
    ap.add_argument("--min-type-len", type=int, default=2)
    ap.add_argument("--top-concepts", type=int, default=300,
                    help="keep relations among the top-N most-connected concepts (0 = all)")
    ap.add_argument("--max-outcomes", type=int, default=800,
                    help="cap ontology to the N most-specified outcomes (0 = all)")
    ap.add_argument("--keep-empty", action="store_true",
                    help="keep outcomes with no characteristics (default: drop them)")
    args = ap.parse_args(argv)

    import yaml
    in_dir = Path(args.in_dir)
    out_dir = Path(args.out) if args.out else in_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    drop_types = ({t.strip().lower() for t in args.drop_types.split(",") if t.strip()}
                  if args.drop_types else None)

    onto = yaml.safe_load((in_dir / "candidate-ontology.yaml").read_text(encoding="utf-8"))
    rels = json.loads((in_dir / "relations.json").read_text(encoding="utf-8"))

    pos0, rel0 = len(onto.get("process_outcomes", [])), len(rels)
    rels_c = filter_relations(rels, drop_types, args.min_type_len, args.top_concepts)
    outs_c, hints_c = curate_outcomes(onto.get("process_outcomes", []),
                                      onto.get("mapping_hints", []),
                                      drop_empty=not args.keep_empty,
                                      max_outcomes=args.max_outcomes)

    onto_c = {"version": str(onto.get("version", "curated")) + "-curated",
              "process_outcomes": outs_c, "mapping_hints": hints_c}
    yaml_path = out_dir / "candidate-ontology.curated.yaml"
    rel_path = out_dir / "relations.curated.json"
    yaml_path.write_text(pipeline.to_yaml(onto_c), encoding="utf-8")
    rel_path.write_text(json.dumps(rels_c, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"outcomes : {pos0} -> {len(outs_c)}")
    print(f"relations: {rel0} -> {len(rels_c)}")
    print(f"  ontology : {yaml_path}   (GUI: Knowledge -> Load ontology)")
    print(f"  relations: {rel_path}    (POST /api/relations/project, or the Legal graph)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
