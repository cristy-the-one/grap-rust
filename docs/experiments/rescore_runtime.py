"""Revalidate historical raw replies without issuing new model calls."""
import argparse
import json
from pathlib import Path
import hashlib
from grap_mcp.kee import extract
from grap_mcp.kee.llm import StubProvider
from evaluate_gemma_runtime import ROOT, score


def rescore(evidence_path, prompts_path):
    evidence = json.loads(evidence_path.read_bytes())
    prompts = {r['case']['id']: r for r in json.loads(prompts_path.read_bytes())}
    rows = []
    for experiment, run in evidence.items():
        for old in run['rows']:
            row = prompts[old['case']]
            obj = extract.extract_json(old['calls'][0]['raw'])
            obj['units'] = {**obj.get('units', {}), **old['fragment'].get('classification_repair', {}).get('units', {})}
            provider = StubProvider()
            provider.extra_body = {'response_format': row['response_format']}
            fragment, failure = extract.parse_reply(json.dumps(obj, ensure_ascii=False), row['source'], row['text'], provider, row['context'])
            rows.append({'experiment': experiment, 'config': old['config'], 'condition': old['condition'],
                         'case': old['case'], 'before': old['score'], 'after': score(row, fragment),
                         'failure': failure, 'historical_wall_s': old['wall_s'],
                         'note': 'Revalidation only; historical latency and repair decisions are not current measurements.'})
    from grap_mcp.kee import legal_rules, qualified
    return {'legal_rules_version': legal_rules.VERSION, 'qualified_version': qualified.VERSION,
            'input_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(), 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Choose a fresh output file')
    report = rescore(ROOT / 'docs/experiments/gemma-runtime-evidence.json',
                     ROOT / 'docs/experiments/gemma-runtime-prompts.json')
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'rows': len(report['rows']), 'changed': sum(r['before'] != r['after'] for r in report['rows'])}))
