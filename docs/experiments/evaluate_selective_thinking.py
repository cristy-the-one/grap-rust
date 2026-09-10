"""Paired replay: change only thinking on the existing focused repair call."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import time

from grap_mcp.kee import extract
from evaluate_gemma_runtime import MODEL, ROOT, native, score
from profile_llm_ingestion import ProfileProvider
from runtime_provenance import provenance


def validate_escalation(previous, current):
    if (previous['evidence_sha256'] != current['evidence_sha256']
            or previous['prompt_sha256'] != current['prompt_sha256']):
        raise ValueError('Escalation requires the same first responses and prompts as its baseline')
    if previous.get('provenance', {}).get('code_fingerprint') != current['provenance']['code_fingerprint']:
        raise ValueError('Code changed since normal repair; rerun the paired baseline before escalation')


def run(output, escalate_from=None, evidence_path=None, prompts_path=None, datasets=None):
    os.environ['GRAP_KEE_STRUCTURED_OUTPUT'] = '1'
    prompts_path = prompts_path or ROOT / 'docs/experiments/gemma-runtime-prompts.json'
    evidence_path = evidence_path or ROOT / 'docs/experiments/gemma-runtime-evidence.json'
    prompts = {r['case']['id']: r for r in json.loads(prompts_path.read_bytes())}
    evidence = json.loads(evidence_path.read_bytes())
    if 'rows' in evidence:
        evidence = {'fresh': evidence}
    datasets = datasets.split(',') if datasets else ('fresh',) if 'fresh' in evidence else ('order-full', 'order-confirm')
    report = {'design': 'Identical saved first outputs; alternate normal/thinking focused repair order. No production graph writes.',
              'prompt_sha256': hashlib.sha256(prompts_path.read_bytes()).hexdigest(),
              'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
              'provenance': provenance(), 'rows': []}
    if escalate_from:
        previous = json.loads(escalate_from.read_bytes())
        validate_escalation(previous, report)
        report['preload'] = native({'model': MODEL, 'keep_alive': '10m'})
        report['escalate_from_sha256'] = hashlib.sha256(escalate_from.read_bytes()).hexdigest()
        for baseline in previous['rows']:
            if baseline['thinking_repair']:
                continue
            row = prompts[baseline['case']]
            saved = next(r for r in evidence[baseline['dataset']]['rows']
                         if r['config'] == 'greedy' and r['case'] == baseline['case'])
            obj = extract.extract_json(saved['calls'][0]['raw'])
            repaired = baseline['fragment'].get('classification_repair', {}).get('units', {})
            obj['units'] = {**obj.get('units', {}), **repaired}
            provider = ProfileProvider(MODEL, options=copy.deepcopy(saved['calls'][0]['options']), think=True)
            provider.extra_body['response_format'] = copy.deepcopy(row['response_format'])
            started = time.perf_counter()
            fragment, failure = extract.finalize_reply(json.dumps(obj, ensure_ascii=False), row['source'], row['text'], provider, row['context'])
            elapsed = time.perf_counter() - started
            result = {'dataset': baseline['dataset'], 'case': baseline['case'],
                      'baseline_score': baseline['score'], 'score': score(row, fragment),
                      'additional_wall_s': elapsed, 'estimated_total_s': baseline['estimated_total_s'] + elapsed,
                      'calls': provider.calls, 'fragment': fragment, 'failure': failure}
            report['rows'].append(result)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return
    index = 0
    report['preload'] = native({'model': MODEL, 'keep_alive': '10m'})
    for dataset in datasets:
        if evidence[dataset]['prompt_file_sha256'] != report['prompt_sha256']:
            raise ValueError('First responses were generated with different prompts')
        for saved in evidence[dataset]['rows']:
            if saved['config'] != 'greedy':
                continue
            row = prompts[saved['case']]
            raw = saved['calls'][0]['raw']
            first_provider = ProfileProvider(MODEL, options=copy.deepcopy(saved['calls'][0]['options']))
            first_provider.extra_body['response_format'] = copy.deepcopy(row['response_format'])
            first, _ = extract.parse_reply(raw, row['source'], row['text'], first_provider, row['context'])
            arms = (False, True) if index % 2 == 0 else (True, False)
            index += 1
            for think in arms:
                provider = ProfileProvider(MODEL, options=copy.deepcopy(saved['calls'][0]['options']), think=think)
                provider.extra_body['response_format'] = copy.deepcopy(row['response_format'])
                started = time.perf_counter()
                fragment, failure = extract.finalize_reply(raw, row['source'], row['text'], provider, row['context'])
                elapsed = time.perf_counter() - started
                result = {'dataset': dataset, 'case': saved['case'], 'thinking_repair': think,
                          'first_pass': score(row, first), 'historical_first_pass': saved['first_pass'], 'repair_wall_s': elapsed,
                          'estimated_total_s': saved['calls'][0]['wall_s'] + elapsed,
                          'first_call_wall_s': saved['calls'][0]['wall_s'],
                          'score': score(row, fragment), 'failure': failure,
                          'calls': provider.calls, 'fragment': fragment}
                report['rows'].append(result)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                print(json.dumps({k: result[k] for k in ('dataset', 'case', 'thinking_repair', 'repair_wall_s', 'score')}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--escalate-from', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--prompts', type=Path)
    parser.add_argument('--datasets')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Choose a fresh output file')
    run(args.output, args.escalate_from, args.evidence, args.prompts, args.datasets)
