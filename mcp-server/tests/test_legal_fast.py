import json
from pathlib import Path
import subprocess
import sys

from grap_mcp.kee.legal_rules import extract_rules_fast, extract_rules
from grap_mcp.kee.legal_tables import extract_tables
from grap_mcp.kee.legal_claims import LegalClaimStore
from grap_mcp.kee.llm import StubProvider

TABLE = '┌──────┬──────┐ │ Tip  │ Lei  │ ├──────┼──────┤ │ A    │ 100  │ └──────┴──────┘'


def test_fast_route_omits_only_independently_resolved_units():
    text = 'Banca trebuie să plătească, cu excepția cazurilor prevăzute de lege. Autoritatea există.'
    def reply(system, user):
        units = json.loads(user.split('\n', 1)[1])
        assert list(units.values()) == ['Autoritatea există.']
        return json.dumps({'units': {k: {'kind': 'fact', 'subject': 'Autoritatea'} for k in units}})
    provider = StubProvider(handler=reply)
    result = extract_rules_fast(provider, text)
    assert len(provider.calls) == result['model_calls'] == 1
    assert [c['subject'] for c in result['claims']] == ['Banca', 'Autoritatea']
    assert result['claims'][0]['exceptions'] == ['cu excepția cazurilor prevăzute de lege.']
    assert json.loads(result['raw_reply'])[0]['deterministic_unit_ids'] == ['u0']


def test_all_deterministic_rules_require_no_model():
    provider = StubProvider(default='must not be called')
    result = extract_rules_fast(provider, 'Banca trebuie să plătească.')
    assert result['model_calls'] == 0 and not provider.calls
    assert result['claims'][0]['subject'] == 'Banca'


def test_table_preserves_cell_offsets_and_exports_rows_without_llm(tmp_path):
    text = TABLE
    tables = extract_tables(text)
    assert tables[0]['tsv'] == 'Tip\tLei\nA\t100\n'
    for row in tables[0]['rows']:
        for cell in row:
            assert text[cell['start']:cell['end']] == cell['text']
    source = tmp_path / 'law.json'
    source.write_text(json.dumps({'id': '1', 'titlu': 'Anexa', 'text_complet': text}), encoding='utf-8')
    provider = StubProvider(default='must not be called')
    with LegalClaimStore(tmp_path / 'db.sqlite', extractor=extract_rules_fast) as store:
        result = store.ingest_folder(source, provider, max_batches=1)
        assert result['counts']['ready'] == 1
        assert not provider.calls
        snapshot = store.export_graph(tmp_path / 'graph')
        assert snapshot['table_rows'] == 2 and snapshot['claims'] == 0
        assert store.ingest_folder(source, provider, max_batches=1)['counts']['unchanged'] == 1


def test_ambiguous_table_does_not_disappear():
    broken = TABLE.replace('│ A    │ 100  │', '│ A │ MISSING BORDER 100')
    assert extract_tables(broken) == []


def test_model_nonassertion_and_modal_fact_are_retained_for_review():
    text = 'Tariful este ales liber de consumator. Arbitrii pot fi medici.'
    def reply(system, user):
        units = json.loads(user.split('\n', 1)[1])
        return json.dumps({'units': {k: {'kind': 'other' if 'Tariful' in v else 'fact',
                                         'subject': '' if 'Tariful' in v else 'Arbitrii'} for k,v in units.items()}})
    result = extract_rules_fast(StubProvider(handler=reply), text)
    assert [r['reason'] for r in result['review_queue']] == ['model_nonassertion', 'modal_fact_label']
    assert [r['statement'] for r in result['review_queue']] == ['Tariful este ales liber de consumator.', 'Arbitrii pot fi medici.']
    assert all(text[r['evidence'][0]['start']:r['evidence'][0]['end']] == r['statement'] for r in result['review_queue'])


def test_table_caption_and_following_rule_stay_in_language_path():
    text = 'Tarifele sunt propuse. ' + TABLE + ' Banca trebuie să plătească.'
    def reply(system, user):
        units = json.loads(user.split('\n', 1)[1])
        assert list(units.values()) == ['Tarifele sunt propuse.']
        return json.dumps({'units': {k: {'kind': 'fact', 'subject': 'Tarifele'} for k in units}})
    result = extract_rules_fast(StubProvider(handler=reply), text)
    assert len(result['tables']) == 1
    assert [c['subject'] for c in result['claims']] == ['Tarifele', 'Banca']


def test_large_article_planning_has_bounded_runtime():
    # Regression for growing-prefix scans: real source shape, no model or GPU.
    code = "from grap_mcp.kee.legal_rules import source_units; t='Afirmație simplă. '*30000; assert len(source_units(t)[0])==30000"
    subprocess.run([sys.executable, '-c', code], timeout=5, check=True)


def test_oversized_statement_is_explicit_unfinished_work_not_lost_document(tmp_path):
    text = 'Lung ' * 1300 + '. Banca trebuie să plătească.'
    file = tmp_path / 'law.json'
    file.write_text(json.dumps({'id':'1','titlu':'Act','text_complet':text}),encoding='utf-8')
    provider = StubProvider(default='must not call')
    with LegalClaimStore(tmp_path/'db.sqlite', extractor=extract_rules_fast) as store:
        result = store.ingest_folder(file, provider, max_batches=1)
        assert result['counts']['ready'] == 1 and not provider.calls
        row = store.db.execute('SELECT record FROM revisions').fetchone()
        receipt = json.loads(row['record'])
        assert receipt['source_text'] == text
        assert receipt['review_queue'][0]['reason'] == 'input_budget_exceeded'
        assert receipt['review_queue'][0]['statement'] == text.split('. ')[0]+'.'
        assert receipt['claim_count'] == 1


def test_repeated_context_is_stored_once_and_api_quotes_are_unchanged(tmp_path):
    text = ' '.join(f'Banca {i} trebuie să plătească.' for i in range(24))
    file = tmp_path / 'law.json'
    file.write_text(json.dumps({'id':'1','titlu':'Act','text_complet':text}), encoding='utf-8')
    with LegalClaimStore(tmp_path/'db.sqlite', extractor=extract_rules_fast) as store:
        provider = StubProvider(default='must not call')
        assert store.ingest_folder(file, provider, max_batches=1)['counts']['partial'] == 1
        saved = store.db.execute('SELECT record FROM legal_batches').fetchone()[0]
        assert '"quote_source":"source_text"' in saved
        assert store.ingest_folder(file, provider, max_batches=10)['counts']['ready'] == 1
        stored = json.loads(store.db.execute('SELECT record FROM claims LIMIT 1').fetchone()[0])
        assert 'quote' not in stored['context_evidence'][0]
        public = store.read(limit=100)['claims']
        assert len(public) == 24
        assert all(c['context_evidence'][0]['quote'] == text for c in public)
        assert store.receipt(public[0]['revision_id'])['claims'][0]['context_evidence'][0]['quote'] == text
        assert not provider.calls
