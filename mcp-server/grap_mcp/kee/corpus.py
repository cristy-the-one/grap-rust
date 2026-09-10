"""Resumable legal-first folder processing, with optional Rust graph export.

Run `python -m grap_mcp.kee.corpus --help`. Original documents are never edited.
"""
import argparse
import json
from pathlib import Path
import time
from .legal_claims import LegalClaimStore
from .legal_rules import extract_rules_fast
from .llm import OpenAICompatProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--batches', type=int, default=100, help='Planned-batch budget per invocation; completed work resumes')
    parser.add_argument('--max-chars', type=int, default=2_000_000, help='Whole-document limit; larger files are reported, not truncated')
    parser.add_argument('--model', default='grap-legal-gemma4')
    parser.add_argument('--export', type=Path, help='New directory for a Rust snapshot and table TSV sidecars')
    args = parser.parse_args()
    if args.batches < 0:
        parser.error('--batches must be nonnegative')
    if args.export and args.export.exists():
        parser.error('--export must name a new directory; omit it to resume ingestion only')
    provider = OpenAICompatProvider(base_url='http://127.0.0.1:11434/v1', model=args.model,
                                   extra_body={'reasoning_effort': 'none', 'seed': 42}, timeout=180)
    provider.retries = 0
    provider.max_tokens = 2048
    started = time.perf_counter()
    with LegalClaimStore(args.database, extractor=extract_rules_fast) as store:
        result = store.ingest_folder(args.folder, provider, max_files=1_000_000,
                                    max_chars=args.max_chars, max_batches=args.batches)
        result['coverage'] = store.coverage()
        accepted_query = 'SELECT r.revision_id, r.record FROM revisions r JOIN sources s ON s.accepted_revision=r.revision_id'
        result['review_queue_units'] = sum(len(json.loads(row['record']).get('review_queue', [])) for row in store.db.execute(accepted_query))
        result['quality_scope'] = 'Ready means an extraction receipt exists, not that every assertion is classified correctly. Review queue is unresolved work.'
        if args.export:
            result['snapshot'] = store.export_graph(args.export)
            tables_dir = args.export / 'tables'
            tables_dir.mkdir()
            for record in store.db.execute(accepted_query):
                receipt = json.loads(record['record'])
                for index, table in enumerate(receipt.get('tables', [])):
                    (tables_dir / f"{record['revision_id']}-{index}.tsv").write_text(table['tsv'], encoding='utf-8')
        result['elapsed_s'] = time.perf_counter() - started
        result['scope'] = 'Proposed legal claims and explicit table cells; does not perform full entity/ontology discovery.'
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
