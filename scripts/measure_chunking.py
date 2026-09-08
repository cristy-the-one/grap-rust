"""Compare exact-source chunking over law JSON files, without loading any model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-server"))
from grap_mcp.ingest import _text_and_label_from_json, chunk_text
from grap_mcp.kee.chunking import chunk_spans, boundaries
from grap_mcp.kee.tabular import is_tabular


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    report = {"documents": 0, "manifest": [], "target": 800,
              "legacy": {"chunks": 0, "extractable": 0, "skipped_characters": 0, "within_unit_cuts": 0},
              "statements": {"chunks": 0, "extractable": 0, "forced_cuts": 0, "skipped_characters": 0},
              "note": "Structural measurement, not semantic accuracy. Source text coverage checked before table filtering."}
    for path in sorted(Path(args.corpus).rglob("*.json")):
        raw = path.read_bytes()
        text, label = _text_and_label_from_json(json.loads(raw.decode("utf-8-sig")), path.stem)
        report["documents"] += 1
        report["manifest"].append({"file": str(path.relative_to(args.corpus)), "sha256": hashlib.sha256(raw).hexdigest()})
        spans = chunk_spans(text)
        # Whitespace-only gaps; exact contiguous source slices with no overlap.
        cursor = 0
        for span in spans:
            assert not text[cursor:span.start].strip() and span.start >= cursor
            assert span.end-span.start <= 2400
            cursor = span.end
        assert not text[cursor:].strip()
        report["statements"]["forced_cuts"] += sum(s.forced_end for s in spans)
        ends = set()
        for boundary in boundaries(text):
            while boundary and text[boundary-1].isspace():
                boundary -= 1
            ends.add(boundary)
        legacy = chunk_text(text)
        cursor = 0
        for piece in legacy[:-1]:
            start = text.index(piece, cursor)
            cursor = start + len(piece)
            report["legacy"]["within_unit_cuts"] += cursor not in ends
        for mode, pieces in (("legacy", legacy), ("statements", [text[s.start:s.end] for s in spans])):
            report[mode]["chunks"] += len(pieces)
            for piece in pieces:
                if is_tabular(piece):
                    report[mode]["skipped_characters"] += len(piece)
                else:
                    report[mode]["extractable"] += 1
    report["seconds"] = round(time.perf_counter()-started, 3)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k != "manifest"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
