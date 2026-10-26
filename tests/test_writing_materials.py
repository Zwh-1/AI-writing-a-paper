from test_workbench import client, project, section, file
from test_writing_agent import env
from app import db, documents
from app.writing_agent import contracts, evidence
from app.writing_agent.common import state


def url(pid, sid):
    return f'/api/v1/projects/{pid}/sections/{sid}/materials'


def test_published_task_evidence_resolves_to_original_source_ids(env):
    original = file(env['client'], env['pid'], '本节关联的原文，不需要按章节标题命中。')
    source = db.one('SELECT * FROM sources WHERE file_id=?', (original['id'],))
    observed = evidence.sync_source(env['pid'], source['id'])
    contracts.save_contract(env['pid'], env['sid'], {'goal': '本节任务', 'evidence_ids': [env['eid'], observed['id']]}, state(env['pid'])['revision'])
    result = env['client'].get(url(env['pid'], env['sid'])).json()
    assert result['mode'] == 'linked'
    assert [item['id'] for item in result['items']] == [source['id']]
    assert result['items'][0]['linked'] and result['items'][0]['text'] == source['text']
    assert result['items'][0]['id'] != observed['id']


def test_current_paragraph_sources_and_task_sources_are_deduplicated(env):
    original = file(env['client'], env['pid'])
    source = db.one('SELECT id FROM sources WHERE file_id=?', (original['id'],))['id']
    observed = evidence.sync_source(env['pid'], source)
    contracts.save_contract(env['pid'], env['sid'], {'goal': '本节任务', 'evidence_ids': [observed['id']]}, state(env['pid'])['revision'])
    doc = documents.text_document('已有正文')
    doc['content'][0]['attrs']['sourceIds'] = [source]
    assert env['client'].put(f'/api/v1/sections/{env["sid"]}/content', json={'base_revision_id': None, 'document': doc}).status_code == 200
    result = env['client'].get(url(env['pid'], env['sid'])).json()
    assert [item['id'] for item in result['items']] == [source]


def test_without_links_recent_material_is_available_on_first_open(client):
    pid = project(client); sid = section(client, pid)
    original = file(client, pid, '已导入的写作范例', role='example')
    result = client.get(url(pid, sid)).json()
    assert result['mode'] == 'recent'
    assert [item['file_id'] for item in result['items']] == [original['id']]
    assert result['items'][0]['role'] == 'example'


def test_material_search_covers_roles_and_keeps_local_project_scope(client):
    pid = project(client); sid = section(client, pid)
    originals = [file(client, pid, '范围检索共同词 ' + role, role=role) for role in ('rules', 'example', 'research', 'data')]
    client.patch('/api/v1/files/' + originals[-1]['id'], json={'egress': 'local_only'})
    foreign = project(client, '其他项目'); file(client, foreign, '范围检索共同词 other')
    excluded = file(client, pid, '范围检索共同词 excluded')
    db.execute("UPDATE pages SET review_state='not_applicable' WHERE file_id=?", (excluded['id'],))
    inactive = file(client, pid, '范围检索共同词 inactive')
    db.execute('UPDATE files SET active=0 WHERE id=?', (inactive['id'],))
    result = client.get(url(pid, sid), params={'q': '范围检索共同词'}).json()
    assert result['mode'] == 'search'
    assert {item['file_id'] for item in result['items']} == {item['id'] for item in originals}
    assert client.get(url(pid, sid), params={'q': 'unmatched_unique_keyword'}).json()['items'] == []
    assert not db.rows('SELECT * FROM generation_runs')


def test_unpublished_contract_and_inactive_original_are_not_loaded(env):
    original = file(env['client'], env['pid'], '尚未发布章节依据')
    source = db.one('SELECT id FROM sources WHERE file_id=?', (original['id'],))['id']
    observed = evidence.sync_source(env['pid'], source)
    contracts.save_contract(env['pid'], env['sid'], {'goal': '未发布任务', 'evidence_ids': [observed['id']]}, state(env['pid'])['revision'], publish=False)
    assert env['client'].get(url(env['pid'], env['sid'])).json()['mode'] == 'recent'
    contracts.save_contract(env['pid'], env['sid'], {'goal': '已发布任务', 'evidence_ids': [observed['id']]}, state(env['pid'])['revision'])
    db.execute('UPDATE files SET active=0 WHERE id=?', (original['id'],))
    assert env['client'].get(url(env['pid'], env['sid'])).json()['items'] == []


def test_other_project_cannot_load_chapter_materials(client):
    pid = project(client); sid = section(client, pid)
    other = project(client, '另一项目')
    assert client.get(url(other, sid)).status_code == 404
