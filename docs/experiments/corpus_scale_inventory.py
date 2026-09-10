"""Read-only inventory of actual legal statements and avoidable labeling work."""
import argparse
import collections
import faulthandler
import hashlib
import json
from pathlib import Path
import time
from grap_mcp.kee import legal_rules as legal


def run(root, output):
    counts = collections.Counter()
    texts, units = collections.Counter(), collections.Counter()
    rows = []
    started = time.perf_counter()
    for p in sorted(root.rglob('*.json')):
        raw = p.read_bytes()
        try:
            data = json.loads(raw)
            text = data.get('text_complet', '')
        except (ValueError, AttributeError):
            counts['invalid_files'] += 1
            continue
        if not isinstance(text, str) or not text:
            counts['empty_files'] += 1
            continue
        if len(rows) % 100 == 0:
            print('PLAN', len(rows), p.name, len(text), flush=True)
        t = time.perf_counter()
        statements, definitions = legal.source_units(text)
        row = {'file': str(p), 'sha256': hashlib.sha256(raw).hexdigest(),
               'characters': len(text), 'units': len(statements), 'definitions': len(definitions),
               'deterministic': 0, 'unit_characters': 0, 'deterministic_characters': 0}
        texts[hashlib.sha256(text.encode()).hexdigest()] += 1
        for unit in statements:
            s = unit['statement']
            units[hashlib.sha256(s.encode()).hexdigest()] += 1
            row['unit_characters'] += len(s)
            kind = legal._strong_kind(s)
            span = legal._explicit_subject(s, kind) if kind else None
            if span and not legal._invalid_subject(s[span[0]:span[1]]) and legal._subject_boundaries(s, *span):
                row['deterministic'] += 1
                row['deterministic_characters'] += len(s)
        row['seconds'] = time.perf_counter() - t
        rows.append(row)
        counts.update({k: row[k] for k in ('characters', 'units', 'definitions', 'deterministic', 'unit_characters', 'deterministic_characters')})
        report = {'documents': len(rows), 'counts': dict(counts), 'unique_documents': len(texts),
                  'unique_units': len(units), 'seconds': time.perf_counter()-started, 'rows': rows}
        if len(rows) % 100 == 0:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('real-data-law-ro'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    faulthandler.dump_traceback_later(30, repeat=True)
    run(args.root, args.output)
