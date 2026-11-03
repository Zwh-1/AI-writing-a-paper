"""Verify the writing release with the selected application's own runtime.

All data and credentials are synthetic and stored under --qa. No model or
external network requests are permitted. No browser automation is performed.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--qa', required=True, type=Path)
    args = parser.parse_args()
    app_root, qa = args.app.resolve(), args.qa.resolve()
    if Path(sys.executable).resolve() != app_root / '.runtime/python/python.exe':
        raise RuntimeError('Run this verification with the selected portable Python')
    qa.mkdir(parents=True, exist_ok=True)
    data = qa / 'writing-data'
    if data.exists():
        raise RuntimeError('Use a fresh QA directory; existing test records are preserved')
    os.environ['PAPER_DATA_DIR'] = str(data)
    os.environ['PAPER_PORT'] = '18979'
    runpy.run_path(str(app_root / 'runner.py'), run_name='release_runtime_bootstrap')
    from fastapi.testclient import TestClient
    from app import config, db, security, parsers, provider, tasks, knowledge_units, documents
    from app.main import app
    from app.writing_agent.common import digest
    for module in (config, db, security, provider):
        if not Path(module.__file__).resolve().is_relative_to(app_root):
            raise RuntimeError('Verification imported application code outside its release')
    import fastapi, httpx, pydantic, numpy
    for module in (fastapi, httpx, pydantic, numpy):
        if not Path(module.__file__).resolve().is_relative_to(app_root):
            raise RuntimeError('Application dependency came from outside the release')
    db.init()
    checks, model_calls = [], []
    external_calls = []
    def deny_network(*args, **kwargs):
        external_calls.append(True)
        raise RuntimeError('External model networking is forbidden in portable verification')
    provider.client = deny_network
    def response(value):
        return {'text': db.dumps(value), 'usage': {'input_tokens': 12, 'output_tokens': 10},
                'cost': .00001, 'remote_id': 'synthetic-portable', 'request_hash': digest(value)}
    def generate(system, user, images=None):
        payload = db.loads(user)
        model_calls.append('semantic' if 'claim_reviews' in system else 'draft')
        if 'claim_reviews' in system:
            actual = payload['actual_sources'][0]
            return response({'claim_reviews': [
                {'claim_id': claim['claim_id'], 'support': 'supported',
                 'source_quotes': [{'source_id': actual['id'], 'quote': actual['text']}],
                 'reason': '仅支持本合成原文范围，未声称测试运行或效果提升。'} for claim in payload['claims']],
                'point_reviews': [{'point_id': point['id'], 'status': 'complete', 'paragraph_indices': [0],
                                   'evidence_text': payload['candidate_paragraphs'][0]['text'],
                                   'reason': '本合成候选回答其限定问题。'} for point in payload.get('task_points', [])],
                'findings': []})
        source = payload['sources'][0]
        statement = '模块职责清晰有助于维护。'
        return response({'paragraphs': [{'text': statement, 'source_ids': [source['id']],
            'reference_ids': [book_id],
            'brief_points': [p['id'] for p in payload.get('writing_brief', {}).get('points', [])],
            'claim_spans': [{'local_id': 'c1', 'text': statement, 'role': 'fact', 'source_ids': [source['id']]}],
            'citation_intents': [{'claim_id': 'c1', 'reference_id': book_id, 'source_ids': [source['id']]}]}]})
    provider.generate = generate
    with TestClient(app) as client:
        auth = client.post('/api/v1/session', json={'token': security.BOOTSTRAP_TOKEN})
        assert auth.status_code == 200, auth.text
        client.headers['X-CSRF-Token'] = auth.json()['csrf']
        health = client.get('/api/v1/health').json()
        assert health['version'] == config.APP_VERSION
        assert not health['key_configured']
        checks.append('fresh_release_has_expected_version_and_no_bundled_credential')
        project = client.post('/api/v1/projects', json={'title': '便携版写作闭环合成验收'}).json()['id']
        client.post(f'/api/v1/projects/{project}/rules', json={
            'statement': '仅说明已读原文的模块维护结论，不编造项目测试结果。', 'modality': 'mandatory', 'scope': 'global'})
        published = client.post(f'/api/v1/projects/{project}/rule-packages')
        assert published.status_code == 200, published.text
        sections = [client.post(f'/api/v1/projects/{project}/sections', json={
            'title': title, 'kind': kind, 'goal': '说明原文的模块职责与维护边界。', 'target_words': 100}).json()
            for title, kind in [('实现说明', 'implementation'), ('结论', 'conclusion')]]
        original, _ = parsers.register_file(project, '合成研究原文.txt',
            '模块职责清晰有助于维护。原文仅讨论模块职责的限定结论，不是本项目的测试或部署记录。'.encode('utf-8'), 'research')
        parsers.parse_file(original['id'])
        knowledge_units.build(project, original['id'])
        book = client.post(f'/api/v1/projects/{project}/references', json={
            'file_id': original['id'], 'metadata': {'title': '合成模块职责研究', 'authors': '合成作者', 'year': '2026'}})
        assert book.status_code == 200, book.text
        book_id = book.json()['id']
        sid = sections[0]['id']
        settings = client.put(f'/api/v2/projects/{project}/writing/flow/settings', json={'max_revision_rounds': 0})
        assert settings.status_code == 200, settings.text
        plan = client.get(f'/api/v1/projects/{project}/writing-plan', params={'section_id': sid})
        assert plan.status_code == 200, plan.text
        assert plan.json()['paragraph_cards']
        checks.append('v2_writing_settings_and_argument_plan_are_routed')
        materials = client.post(f'/api/v1/projects/{project}/writing-retrieval/prepare', json={
            'section_id': sid, 'query': '模块职责维护边界'})
        assert materials.status_code == 200, materials.text
        assert materials.json()['primary_evidence'] and materials.json()['constraints']
        checks.append('automatic_retrieval_returns_actual_original_and_required_rules')
        security.save_key('synthetic-portable-verification-key')
        db.set_setting('provider', {**provider.config(), 'model': 'synthetic-portable', 'context_limit': 180000,
                                   'max_output_tokens': 4096, 'input_price': 1., 'output_price': 2.})
        db.set_setting('expense_policy:' + project, {'require_pricing_basis': False, 'block_on_unresolved': False})
        preflight = client.get(f'/api/v1/projects/{project}/paper-writing/preflight').json()
        launched = client.post(f'/api/v1/projects/{project}/paper-writing', json={
            'instruction': '只据已读合成原文写作。', 'outline_revision': preflight['outline_revision'],
            'section_versions': preflight['section_versions']})
        assert launched.status_code == 200, launched.text
        tid = launched.json()['id']
        assert tasks.run_one()
        completed = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
        assert completed['status'] == 'succeeded', completed['error']
        output = db.loads(completed['result'])
        assert output['complete'] and len(output['paper_sections']) == 2
        for item in output['paper_sections']:
            assert item['evidence_bundle']['primary_evidence'] and item['argument_plan']['paragraph_cards']
            assert item['writing_quality']['semantic']['coverage_complete']
            assert all(item['manifest']['knowledge_unit_versions'].values())
            assert any(n['type'] == 'citation' for n in documents.nodes(item['document']))
        checks.append('whole_paper_uses_real_read_scopes_and_structured_claim_citations')
        adopted = client.post(f'/api/v1/projects/{project}/paper-writing/{tid}/accept', json={
            'paper_hash': output['paper_hash'], 'reviewed': True, 'accept_policy': 'draft'})
        assert adopted.status_code == 200, adopted.text
        assert db.one('SELECT * FROM citations') and db.one('SELECT * FROM sentence_claim_links')
        checks.append('atomic_adoption_preserves_original_citations_and_sentence_claim_links')
        report = client.get(f'/api/v1/projects/{project}/writing-quality/{tid}')
        assert report.status_code == 200, report.text
        assert len(report.json()['sections']) == 2
        checks.append('candidate_quality_report_is_available_in_portable_release')
        base = security.require_section(sid)['current_revision_id']
        retry = tasks.enqueue(project, 'write', {'section_id': sid, 'base_revision_id': base, 'operation': 'generate'})
        assert tasks.run_one()
        assert db.one('SELECT status FROM tasks WHERE id=?', (retry['id'],))['status'] == 'succeeded'
        revoked = client.patch('/api/v1/files/' + original['id'], json={'egress': 'local_only'})
        assert revoked.status_code == 200
        rejected = client.post('/api/v1/sections/' + sid + '/accept', json={'task_id': retry['id'], 'base_revision_id': base})
        assert rejected.status_code in (403, 409), rejected.text
        assert security.require_section(sid)['current_revision_id'] == base
        checks.append('revoked_material_permissions_block_adoption_without_changing_manuscript')
    assert not external_calls
    report = {'passed': True, 'version': config.APP_VERSION, 'application_root': str(app_root),
              'checks': checks, 'checks_passed': len(checks), 'synthetic_model_calls': len(model_calls),
              'external_model_calls': 0, 'user_data_used': False,
              'scope': 'Application and dependencies resolve inside selected release; API and worker use isolated synthetic data.'}
    (qa / 'writing-portable-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
