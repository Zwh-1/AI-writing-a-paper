import pytest
from fastapi import HTTPException

from test_writing_agent import env, start
from test_workbench import file
from app import db
from app.writing_agent import tools, evidence


def test_agent_can_search_read_and_ground_literature_in_knowledge_base(env, monkeypatch):
    from app import model_install
    original = model_install.status
    monkeypatch.setattr(model_install, 'status', lambda model: {**original(model), 'ready': False})
    original_file = file(env['client'], env['pid'], 'A credential identity study documents prior academic verification methods.')
    session = start(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    result = tools.execute(env['pid'], step['id'], 'search_literature', {'query': 'credential identity'}, 'search-paper', env['snapshot']['id'])
    assert result['data']['hits']
    source = result['data']['hits'][0]
    assert source['file_id'] == original_file['id']
    read = tools.execute(env['pid'], step['id'], 'read_literature', {'source_id': source['id']}, 'read-paper', env['snapshot']['id'])
    observed = evidence.get(env['pid'], read['data']['evidence_id'])
    assert observed['kind'] == 'literature' and observed['origin_type'] == 'source'
    assert observed['version_current']
    assert read['data']['page_number'] == 1
    assert tools.execute(env['pid'], step['id'], 'read_literature', {'source_id': source['id']}, 'read-paper', env['snapshot']['id']) == read
    env['client'].patch('/api/v1/files/' + original_file['id'], json={'egress': 'local_only'})
    with pytest.raises(HTTPException):
        tools.execute(env['pid'], step['id'], 'read_literature', {'source_id': source['id']}, 'read-paper', env['snapshot']['id'])
    with pytest.raises(HTTPException):
        tools.execute(env['pid'], step['id'], 'search_literature', {'query': 'credential identity'}, 'search-paper', env['snapshot']['id'])
