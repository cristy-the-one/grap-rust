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


def run(output, escalate_from=None):
    os.environ['GRAP_KEE_STRUCTURED_OUTPUT'] = '1'
    prompts_path = ROOT / 'docs/experiments/gemma-runtime-prompts.json'
    evidence_path = ROOT / 'docs/experiments/gemma-runtime-evidence.json'
    prompts = {r['case']['id']: r for r in json.loads(prompts_path.read_bytes())}
    evidence = json.loads(evidence_path.read_bytes())
    report = {'design': 'Identical saved first outputs; alternate normal/thinking focused repair order. No production graph writes.',
              'prompt_sha256': hashlib.sha256(prompts_path.read_bytes()).hexdigest(),
              'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(), 'rows': []}
    report['preload'] = native({'model': MODEL, 'keep_alive': '10m'})
    if escalate_from:
        previous = json.loads(escalate_from.read_bytes())
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
    for dataset in ('order-full', 'order-confirm'):
        for saved in evidence[dataset]['rows']:
            if saved['config'] != 'greedy':
                continue
            row = prompts[saved['case']]
            raw = saved['calls'][0]['raw']
            arms = (False, True) if index % 2 == 0 else (True, False)
            index += 1
            for think in arms:
                provider = ProfileProvider(MODEL, options=copy.deepcopy(saved['calls'][0]['options']), think=think)
                provider.extra_body['response_format'] = copy.deepcopy(row['response_format'])
                started = time.perf_counter()
                fragment, failure = extract.finalize_reply(raw, row['source'], row['text'], provider, row['context'])
                elapsed = time.perf_counter() - started
                result = {'dataset': dataset, 'case': saved['case'], 'thinking_repair': think,
                          'first_pass': saved['first_pass'], 'repair_wall_s': elapsed,
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
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Choose a fresh output file')
    run(args.output, args.escalate_from)
