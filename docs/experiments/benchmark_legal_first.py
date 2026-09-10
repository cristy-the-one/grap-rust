"""Measure bounded label-only extraction on the existing 12 legal targets."""
import argparse
import json
from pathlib import Path
import time
from grap_mcp.kee import legal_rules
from evaluate_gemma_runtime import score, native, MODEL
from profile_llm_ingestion import ProfileProvider


def run(output):
    prompts = json.loads(Path('docs/experiments/gemma-runtime-prompts.json').read_bytes())
    native({'model': MODEL, 'keep_alive': '10m'})
    report = {'scope': '12 development targets; label-only path, not full ontology discovery', 'rows': []}
    for row in prompts:
        provider = ProfileProvider(MODEL, options={'temperature': 0, 'num_ctx': 16384})
        started = time.perf_counter()
        result = legal_rules.extract_batches(provider, row['context']['text'], [row['context']['units']], deterministic=True)
        elapsed = time.perf_counter()-started
        fragment = {'qualified_rules': result['claims'], 'outcomes': [],
                    'rule_coverage': {'unresolved': sum(not c['subject'] for c in result['claims'])}}
        record = {'case': row['case']['id'], 'seconds': elapsed, 'score': score(row, fragment),
                  'units': len(row['context']['units']), 'calls': provider.calls, 'result': result}
        report['rows'].append(record)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'case':record['case'],'seconds':elapsed,'correct':record['score']['target_correct']}),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    if args.output.exists(): raise SystemExit('Choose a new output path')
    run(args.output)
