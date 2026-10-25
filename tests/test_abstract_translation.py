import pytest

from test_workbench import client, project, manual_package, save, file, section
from app import db, documents, tasks, security


def abstracts(client, text='本文研究服务管理系统，采用模块化设计，验证业务流程。\n关键词：模块化；服务管理'):
    pid = project(client)
    manual_package(client, pid)
    zh = client.post(f'/api/v1/projects/{pid}/sections', json={'title': '摘要', 'kind': 'abstract'}).json()['id']
    en = client.post(f'/api/v1/projects/{pid}/sections', json={'title': 'Abstract', 'kind': 'abstract_en'}).json()['id']
    revision = save(client, zh, text).json() if text is not None else None
    return pid, zh, en, revision


def request_translation(client, en, **extra):
    return client.post(f'/api/v1/sections/{en}/writing-tasks', json={
        'operation': 'generate', 'base_revision_id': security.require_section(en)['current_revision_id'], **extra,
    })


def run_translation(client, monkeypatch, en, capture=None):
    def model(task, system, user, manifest):
        if capture is not None:
            capture.update(system=system, context=db.loads(user), manifest=manifest)
        return {'text': db.dumps({'paragraphs': [{'text': 'This study examines a service management system using a modular design.'}, {'text': 'Keywords: modular design; service management'}]})}
    monkeypatch.setattr(tasks, 'model_call', model)
    monkeypatch.setattr('app.parsers.search', lambda *args, **kwargs: pytest.fail('Translation must not retrieve research for independent generation'))
    response = request_translation(client, en)
    assert response.status_code == 200, response.text
    task = response.json()
    assert tasks.run_one()
    result = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert result['status'] == 'succeeded', result['error']
    return result, db.loads(result['result'])


@pytest.mark.parametrize('text', [None, '  \n  '])
def test_translation_requires_nonempty_saved_chinese_abstract(client, text):
    pid, zh, en, revision = abstracts(client, text)
    response = request_translation(client, en)
    assert response.status_code == 422
    assert '先完成并保存中文摘要' in response.text
    assert not db.rows('SELECT * FROM tasks')
    assert not db.rows('SELECT * FROM generation_runs')


def test_missing_chinese_abstract_never_generates_independently(client):
    pid = project(client)
    manual_package(client, pid)
    en = client.post(f'/api/v1/projects/{pid}/sections', json={'title': 'Abstract', 'kind': 'abstract_en'}).json()['id']
    assert request_translation(client, en).status_code == 422
    assert not db.rows('SELECT * FROM tasks')


@pytest.mark.parametrize('operation', ['generate', 'revise', 'expand', 'compress', 'translate'])
def test_english_abstract_operations_always_become_translation(client, operation):
    pid, zh, en, revision = abstracts(client)
    response = request_translation(client, en, operation=operation)
    assert response.status_code == 200, response.text
    payload = db.loads(response.json()['payload'])
    assert payload['operation'] == 'translate'
    assert payload['translation_source_section_id'] == zh
    assert payload['translation_source_revision_id'] == revision['id']


def test_translation_reads_full_chinese_revision_without_research_generation(client, monkeypatch):
    text = '研究目的与方法必须完整翻译。' * 150 + '\n研究结果为42项。\n关键词：模块化；服务管理'
    pid, zh, en, revision = abstracts(client, text)
    assert client.post(f'/api/v1/projects/{pid}/terms', json={'zh': '模块化', 'en': 'modular design'}).status_code == 200
    capture = {}
    task, result = run_translation(client, monkeypatch, en, capture)
    assert capture['context']['operation'] == 'translate'
    assert capture['context']['source_abstract']['text'] == text
    assert capture['context']['source_abstract']['revision_id'] == revision['id']
    assert {'zh': '模块化', 'en': 'modular design'} in capture['context']['terminology']
    assert 'facts' not in capture['context'] and 'sources' not in capture['context'] and 'writing_examples' not in capture['context']
    assert '不独立生成摘要' in capture['system']
    assert capture['manifest']['writing_state']['related_revisions'] == {zh: revision['id']}
    assert result['document'] and not result['stale']
    assert security.require_section(en)['current_revision_id'] is None
    assert not db.rows('SELECT * FROM generation_runs')


def test_queued_translation_rejects_changed_chinese_source_before_model_call(client, monkeypatch):
    pid, zh, en, revision = abstracts(client)
    queued = request_translation(client, en).json()
    save(client, zh, '中文摘要已修改', revision['id'])
    monkeypatch.setattr(tasks, 'model_call', lambda *args: pytest.fail('Changed source must fail before model call'))
    tasks.run_one()
    failed = db.one('SELECT * FROM tasks WHERE id=?', (queued['id'],))
    assert failed['status'] == 'failed'
    assert '中文摘要已修改' in failed['error']


def test_translation_rejects_foreign_source_and_ambiguous_source(client):
    pid, zh, en, revision = abstracts(client)
    other_pid = project(client, '其他项目')
    other = client.post(f'/api/v1/projects/{other_pid}/sections', json={'title': '其他摘要', 'kind': 'abstract'}).json()['id']
    assert request_translation(client, en, translation_source_section_id=other).status_code == 422
    extra = client.post(f'/api/v1/projects/{pid}/sections', json={'title': '第二份摘要', 'kind': 'abstract'}).json()['id']
    assert request_translation(client, en).status_code == 422
    client.patch(f'/api/v1/sections/{en}', json={'dependencies': [zh]})
    response = request_translation(client, en)
    assert response.status_code == 200, response.text
    assert db.loads(response.json()['payload'])['translation_source_section_id'] == zh


def test_translation_rejects_partial_scope_and_locked_english_text(client):
    pid, zh, en, revision = abstracts(client)
    assert request_translation(client, en, target_node_ids=['selected-node']).status_code == 422
    english = save(client, en, 'Existing English text.').json()
    node = db.loads(english['document'])['content'][0]['attrs']['nodeId']
    client.put(f'/api/v1/sections/{en}/locks', json={'node_ids': [node]})
    assert request_translation(client, en).status_code == 409


def test_translation_respects_chinese_source_permissions(client, monkeypatch):
    pid, zh, en, revision = abstracts(client)
    original = file(client, pid, '摘要原始研究依据')
    source = db.one('SELECT id FROM sources WHERE file_id=?', (original['id'],))['id']
    document = documents.text_document('依据原始材料写成的中文摘要')
    document['content'][0]['attrs']['sourceIds'] = [source]
    saved = client.put(f'/api/v1/sections/{zh}/content', json={'document': document, 'base_revision_id': revision['id']}).json()
    queued = request_translation(client, en).json()
    client.patch(f"/api/v1/files/{original['id']}", json={'egress': 'local_only'})
    monkeypatch.setattr(tasks, 'model_call', lambda *args: pytest.fail('Local-only Chinese content must not be sent'))
    tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?', (queued['id'],))['status'] == 'failed'
    assert request_translation(client, en).status_code == 403


def test_old_translation_cannot_be_adopted_after_chinese_revision_changes(client, monkeypatch):
    pid, zh, en, revision = abstracts(client)
    task, result = run_translation(client, monkeypatch, en)
    save(client, zh, '新的研究结果和关键词', revision['id'])
    response = client.post(f'/api/v1/sections/{en}/accept', json={'task_id': task['id'], 'base_revision_id': None})
    assert response.status_code == 409, response.text
    assert '中文摘要已修改' in response.text
    assert security.require_section(en)['current_revision_id'] is None


def test_adopted_translation_tracks_chinese_revision_and_becomes_stale_on_edit(client, monkeypatch):
    pid, zh, en, revision = abstracts(client)
    task, result = run_translation(client, monkeypatch, en)
    response = client.post(f'/api/v1/sections/{en}/accept', json={'task_id': task['id'], 'base_revision_id': None})
    assert response.status_code == 200, response.text
    english_revision = response.json()['id']
    assert db.one("SELECT * FROM object_dependencies WHERE upstream_type='revision' AND upstream_id=? AND downstream_type='revision' AND downstream_id=?", (revision['id'], english_revision))
    save(client, zh, '修改中文摘要的研究结论', revision['id'])
    assert security.require_section(en)['stale'] == 1


@pytest.mark.parametrize('text', [None, '已保存的中文摘要'])
def test_batch_waits_for_current_chinese_candidate_to_be_adopted(client, text):
    pid, zh, en, revision = abstracts(client, text)
    body_id = section(client, pid)
    response = client.post(f'/api/v1/projects/{pid}/writing-batch', json={})
    assert response.status_code == 200, response.text
    result = response.json()
    selected = [db.loads(task['payload'])['section_id'] for task in result['tasks']]
    assert set(selected) == {body_id, zh}
    assert result['skipped_sections'][0]['section_id'] == en


def test_batch_can_translate_saved_chinese_when_it_is_not_being_regenerated(client):
    pid, zh, en, revision = abstracts(client)
    response = client.post(f'/api/v1/projects/{pid}/writing-batch', json={'section_ids': [en]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['skipped_sections'] == []
    assert len(result['tasks']) == 1
    assert db.loads(result['tasks'][0]['payload'])['operation'] == 'translate'


def test_batch_does_not_leave_partial_tasks_when_preflight_fails(client):
    pid, zh, en, revision = abstracts(client)
    body_id = section(client, pid)
    english = save(client, en, 'Existing English text.').json()
    node = db.loads(english['document'])['content'][0]['attrs']['nodeId']
    client.put(f'/api/v1/sections/{en}/locks', json={'node_ids': [node]})
    assert client.post(f'/api/v1/projects/{pid}/writing-batch', json={'section_ids': [body_id, en]}).status_code == 409
    assert not db.rows('SELECT * FROM tasks')


def test_agent_pipeline_cannot_independently_generate_english_abstract(client):
    pid, zh, en, revision = abstracts(client)
    response = client.post(f'/api/v1/projects/{pid}/writing-sessions', json={
        'section_id': en, 'expected_project_state_revision': 0,
        'contract_version_id': 'unused', 'profile_id': 'unused',
    })
    assert response.status_code == 422, response.text
    assert 'ABSTRACT_TRANSLATION_REQUIRED' in response.text
    assert not db.rows('SELECT * FROM tasks')


def test_independently_generated_legacy_english_candidate_cannot_be_adopted(client):
    pid, zh, en, revision = abstracts(client)
    task = tasks.enqueue(pid, 'write', {'section_id': en, 'operation': 'generate'})
    result = {
        'section_id': en, 'base_revision_id': None,
        'document': documents.normalize(documents.text_document('An independently generated abstract.')),
        'manifest': {'rule_package_id': security.require_project(pid)['rule_package_id']},
    }
    db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?", (db.dumps(result), task['id']))
    response = client.post(f'/api/v1/sections/{en}/accept', json={'task_id': task['id'], 'base_revision_id': None})
    assert response.status_code == 409, response.text
    assert 'ABSTRACT_TRANSLATION_REQUIRED' in response.text


def test_translation_cannot_be_adopted_after_source_permission_revocation(client, monkeypatch):
    pid, zh, en, revision = abstracts(client)
    original = file(client, pid, '仅允许外发时使用的原始材料')
    source = db.one('SELECT id FROM sources WHERE file_id=?', (original['id'],))['id']
    document = documents.text_document('具有原始资料依赖的中文摘要')
    document['content'][0]['attrs']['sourceIds'] = [source]
    client.put(f'/api/v1/sections/{zh}/content', json={'document': document, 'base_revision_id': revision['id']})
    task, result = run_translation(client, monkeypatch, en)
    client.patch(f"/api/v1/files/{original['id']}", json={'egress': 'local_only'})
    response = client.post(f'/api/v1/sections/{en}/accept', json={'task_id': task['id'], 'base_revision_id': None})
    assert response.status_code == 403, response.text
    assert security.require_section(en)['current_revision_id'] is None


def test_translation_rejects_fabricated_fact_references(client, monkeypatch):
    pid, zh, en, revision = abstracts(client)
    monkeypatch.setattr(tasks, 'model_call', lambda *args: {'text': db.dumps({
        'paragraphs': [{'text': 'An English translation.', 'fact_ids': ['new-unsupported-fact']}],
    })})
    task = request_translation(client, en).json()
    tasks.run_one()
    failed = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert failed['status'] == 'failed'
    assert '不能添加新来源' in failed['error']
    assert security.require_section(en)['current_revision_id'] is None
