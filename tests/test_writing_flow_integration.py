"""Whole application flow with an isolated database and synthetic paid transport."""
import pytest
from test_workbench import client, project, section, manual_package, file, save
from app import db, provider, security, tasks, documents, writing_flow


@pytest.fixture
def writing_project(client, monkeypatch):
    # This fixture verifies the historical whole-candidate protocol. The v2
    # scoped/patch suite selects its task-frozen versions explicitly.
    db.set_setting('ai_cache_policy',{'new_task_verification_scope':'legacy_full',
                                    'new_task_revision_protocol':'full_candidate'})
    pid = project(client)
    sid = section(client, pid)
    manual_package(client, pid)
    security.save_key('synthetic-writing-flow-key')
    db.set_setting('provider', {**provider.config(), 'model': 'fixture-writing', 'context_limit': 160000,
                              'max_output_tokens': 4096, 'input_price': 1., 'output_price': 2.})
    source_file = file(client, pid, '研究资料说明：模块职责清晰有助于维护。接口边界应与授权条件保持一致。')
    ref = client.post(f'/api/v1/projects/{pid}/references', json={
        'file_id': source_file['id'], 'metadata': {'title': '模块设计研究', 'authors': '作者甲', 'year': '2026'}}).json()
    sent = []

    def transport(system, user, images=None):
        payload = db.loads(user)
        sent.append((system, payload))
        if payload.get('operation')=='review_candidate_discourse':
            return response({'coverage_complete':True,'findings':[],
                'rule_reviews':[{'rule_id':r['id'],'status':'satisfied','paragraph_indices':[0],
                    'quote':payload['candidate_paragraphs'][0]['text'],'reason':'合成夹具的规则边界已保留。'}
                    for r in payload.get('rule_applications',[])],
                'summary':'夹具只核对给定连续正文。'})
        if 'claim_reviews' in system:
            sources = payload['actual_sources']
            return response({'claim_reviews': [
                {'claim_id': claim['claim_id'], 'support': 'supported',
                 'source_quotes': [{'source_id': sources[0]['id'], 'quote': sources[0]['text']}],
                 'reason': '原文明确讨论模块职责与维护性，保留适用范围。'} for claim in payload['claims']],
                'point_reviews': [{'point_id': p['id'], 'status': 'complete', 'paragraph_indices': [0],
                                   'evidence_text':payload['candidate_paragraphs'][0]['text'],
                                   'reason': '正文讨论模块职责。'} for p in payload.get('task_points', [])], 'findings': []})
        source = payload['sources'][0]
        text = '文献指出模块职责清晰有助于维护。'
        paragraph = {'text': text, 'source_ids': [source['id']], 'reference_ids': [ref['id']],
                     'claim_spans': [{'local_id': 'c1', 'text': text, 'source_ids': [source['id']], 'role': 'fact'}],
                     'citation_intents': [{'claim_id': 'c1', 'reference_id': ref['id'], 'source_ids': [source['id']]}]}
        return response({'paragraphs': [paragraph]})
    monkeypatch.setattr(provider, 'generate', transport)
    return {'client': client, 'pid': pid, 'sid': sid, 'file': source_file, 'ref': ref,
            'sent': sent, 'transport': transport}


def response(value):
    return {'text': db.dumps(value), 'usage': {'input_tokens': 12, 'output_tokens': 10},
            'cost': .00001, 'remote_id': 'fixture', 'request_hash': 'fixture'}


def write(env):
    task = tasks.enqueue(env['pid'], 'write', {'section_id': env['sid'], 'base_revision_id': None, 'operation': 'generate'})
    assert tasks.run_one()
    stored = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert stored['status'] == 'succeeded', stored['error']
    return stored, db.loads(stored['result'])


def test_normal_writer_reads_library_without_manual_material_selection_and_maps_citations(writing_project):
    e = writing_project
    task, result = write(e)
    from app.writing_expression import guidance, review_guidance
    draft_system, draft_request = next((system, request) for system, request in e['sent'] if 'claim_reviews' not in system)
    assert guidance() in draft_system
    assert draft_request['writing_brief']['paragraph_plan']['advisory'] is True
    assert all(card.get('analysis_goal') and card.get('expected_information_gain') for card in draft_request['argument_plan']['paragraph_cards'])
    assert all(review_guidance() in system for system, request in e['sent'] if 'claim_reviews' in system)
    assert result['evidence_bundle']['primary_evidence']
    assert result['argument_plan']['paragraph_cards']
    assert result['writing_quality']['semantic']['coverage_complete']
    assert result['writing_quality']['semantic']['claims'][0]['support'] == 'supported'
    node = result['document']['content'][0]
    assert node['attrs']['claimEvidence'][0]['text'] == '文献指出模块职责清晰有助于维护。'
    assert node['content'][1]['type'] == 'citation'
    accepted = e['client'].post(f'/api/v1/sections/{e["sid"]}/accept', json={'task_id': task['id'], 'base_revision_id': None})
    assert accepted.status_code == 200, accepted.text
    assert db.one('SELECT * FROM citations WHERE revision_id=?', (accepted.json()['id'],))
    assert db.one('SELECT * FROM sentence_claim_links')
    assert len(db.rows('SELECT * FROM generation_runs WHERE task_id=?', (task['id'],))) == 3


def test_evidence_packet_revalidated_when_accepting_after_revocation(writing_project):
    e = writing_project
    task, _ = write(e)
    e['client'].patch('/api/v1/files/' + e['file']['id'], json={'egress': 'local_only'})
    accepted = e['client'].post(f'/api/v1/sections/{e["sid"]}/accept', json={'task_id': task['id'], 'base_revision_id': None})
    assert accepted.status_code in (403, 409)
    assert not db.rows('SELECT * FROM revisions')


def test_independent_support_failure_triggers_bounded_local_repair(writing_project, monkeypatch):
    e = writing_project
    semantic_count = 0
    def transport(system, user, images=None):
        nonlocal semantic_count
        payload = db.loads(user)
        reply = e['transport'](system, user, images)
        if 'claim_reviews' in system:
            semantic_count += 1
            if semantic_count == 1:
                data = db.loads(reply['text'])
                for review in data['claim_reviews']:
                    review.update(support='partial', reason='应明确该结论来自文献，不是本项目运行结果。')
                return response(data)
        if payload.get('candidate_to_repair'):
            data = db.loads(reply['text'])
            data['paragraphs'][0]['text'] = '相关文献指出模块职责清晰有助于维护。'
            data['paragraphs'][0]['claim_spans'][0]['text'] = data['paragraphs'][0]['text']
            return response(data)
        return reply
    monkeypatch.setattr(provider, 'generate', transport)
    _, result = write(e)
    assert result['writing_quality']['repair_rounds'][0]['accepted']
    assert len(result['writing_quality']['repair_rounds']) <= 2
    assert '相关文献' in result['text']


def test_completion_gets_real_materials_and_returns_structured_inline_reference(writing_project, monkeypatch):
    e = writing_project
    current = save(e['client'], e['sid'], '下面说明模块职责。').json()
    def complete(system, user, images=None):
        payload = db.loads(user)
        if 'claim_reviews' in system:return e['transport'](system,user,images)
        source = payload['sources'][0]
        text = '文献指出模块职责清晰有助于维护。'
        return response({'text': text, 'source_ids': [source['id']],
                         'claim_spans': [{'local_id': 'c1', 'text': text, 'source_ids': [source['id']]}],
                         'citation_intents': [{'claim_id': 'c1', 'reference_id': e['ref']['id'], 'source_ids': [source['id']]}]})
    monkeypatch.setattr(provider, 'generate', complete)
    nid = db.loads(current['document'])['content'][0]['attrs']['nodeId']
    task = tasks.enqueue(e['pid'], 'studio', {'action': 'complete', 'section_id': e['sid'],
                         'revision_id': current['id'], 'node_id': nid, 'prefix': '模块职责', 'suffix': ''})
    tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert task['status'] == 'succeeded', task['error']
    output = db.loads(task['result'])
    assert output['can_insert'] and output['source_refs']
    assert any(node['type'] == 'citation' for node in output['inline_content'])
    assert output['writing_quality']['semantic']['status'] in ('checked','incomplete')


def test_plan_and_settings_api_reject_unknown_and_cyclic_author_plans(writing_project):
    e = writing_project
    c, pid, sid = e['client'], e['pid'], e['sid']
    assert c.put(f'/api/v1/projects/{pid}/writing-flow/settings', json={'max_revision_rounds': 99}).status_code == 422
    settings = c.put(f'/api/v1/projects/{pid}/writing-flow/settings', json={'context_mode': 'argument_scoped'})
    assert settings.status_code == 200
    result = c.get(f'/api/v1/projects/{pid}/writing-plan', params={'section_id': sid})
    assert result.status_code == 200, result.text
    plan = result.json()
    cards = plan['paragraph_cards']
    cards[0]['relation_to_previous'] = {'from': cards[0]['id'], 'relation': 'requires'}
    invalid = c.put(f'/api/v1/projects/{pid}/writing-plan?section_id={sid}', json={
        'base_hash': plan['hash'], 'paragraph_cards': cards})
    assert invalid.status_code == 422


def test_cached_response_does_not_bypass_changed_source_permissions(writing_project):
    e = writing_project
    _, result = write(e)
    e['client'].patch('/api/v1/files/' + e['file']['id'], json={'egress': 'local_only'})
    with pytest.raises(Exception):
        writing_flow._cached_call({'id': 'unused', 'project_id': e['pid']}, 'test', {}, result['manifest'], stage='test')


def test_author_plan_is_used_by_generation_and_repeated_save_keeps_basis(writing_project):
    e=writing_project;c=e['client'];pid=e['pid'];sid=e['sid']
    plan=c.get(f'/api/v1/projects/{pid}/writing-plan?section_id={sid}').json()
    for label in ('作者明确要求解释模块边界。','作者要求比较模块边界与职责。'):
        cards=plan['paragraph_cards'];cards[0]['question']=label
        response=c.put(f'/api/v1/projects/{pid}/writing-plan?section_id={sid}',json={
            'base_hash':plan['hash'],'paragraph_cards':cards,'author_note':'本次采用作者的章节分工。'})
        assert response.status_code==200,response.text
        plan=c.get(f'/api/v1/projects/{pid}/writing-plan?section_id={sid}').json()
        assert plan['paragraph_cards'][0]['question']==label
    _,result=write(e)
    assert result['argument_plan']['paragraph_cards'][0]['question']==label
    draft_context=next(payload for system,payload in e['sent'] if 'claim_reviews' not in system)
    assert draft_context['argument_plan']['paragraph_cards'][0]['question']==label
