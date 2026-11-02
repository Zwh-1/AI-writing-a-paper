"""Unified DeepSeek switching exercises real routing with synthetic HTTP transports."""
import json
import httpx
import pytest
from test_workbench import client, project
from app import db, deepseek_pool, material_summary, provider, security, tasks


def initialize(client):
    assert client.put('/api/v1/settings', json={'api_key': 'fake-original-key', 'provider': {
        'base_url': 'https://original.example/v1', 'model': 'deepseek-original', 'max_attempts': 1}}).status_code == 200
    response = client.get('/api/v1/deepseek/settings')
    assert response.status_code == 200, response.text
    return response.json()


def create(client, name='备用接口', key='fake-second-key', **extra):
    response = client.post('/api/v1/deepseek/endpoints', json={
        'name': name, 'api_key': key, 'provider': {'base_url': 'https://second.example/v1',
        'model': 'deepseek-second', 'max_attempts': 1, **extra}})
    assert response.status_code == 200, response.text
    return response.json()['endpoints'][-1]


def transport(monkeypatch, callback):
    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(callback)))


def ok():
    return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}, 'finish_reason': 'stop'}],
                                    'usage': {'prompt_tokens': 7, 'completion_tokens': 5}})


def test_migration_preserves_all_keys_and_deduplicates_matching_profiles(client):
    client.put('/api/v1/settings', json={'api_key': 'fake-shared-key', 'provider': {
        'base_url': 'https://api.x5m5x.com', 'model': 'deepseek-chat'}})
    client.put('/api/v1/material-summary/settings', json={'api_key': 'fake-shared-key'})
    first = client.get('/api/v1/deepseek/settings')
    assert len(first.json()['endpoints']) == 1
    assert client.get('/api/v1/deepseek/settings').json() == first.json()
    assert 'fake-shared-key' not in first.text
    assert 'fake-shared-key' not in json.dumps(db.setting('deepseek_pool'))
    assert b'fake-shared-key' not in (security.DATA / deepseek_pool.KEYS_FILE).read_bytes()
    assert security.get_key() == security.get_key('material-summary') == 'fake-shared-key'
    assert first.json()['strategy']['mode'] == 'manual'


def test_different_summary_and_pool_profiles_survive_migration(client):
    client.put('/api/v1/material-summary/settings', json={'api_key': 'fake-summary-key'})
    client.put('/api/v1/deepseek-pool/settings', json={'endpoints': [{
        'id': 'old-pool', 'name': '旧端点', 'base_url': 'https://old.example/v1', 'model': 'deepseek-old', 'credential_ref': 'pool'}],
        'endpoint_keys': {'old-pool': 'fake-old-pool-key'}})
    data = initialize(client)
    assert len(data['endpoints']) == 3
    old_summary = next(ep for ep in data['endpoints'] if ep['name'] == '原资料总结接口')
    client.post(f"/api/v1/deepseek/endpoints/{old_summary['id']}/activate")
    assert security.get_key() == 'fake-summary-key'
    assert security.get_key('material-summary') == 'fake-summary-key'
    old_pool = next(ep for ep in data['endpoints'] if ep['name'] == '旧端点')
    client.post(f"/api/v1/deepseek/endpoints/{old_pool['id']}/activate")
    assert security.get_key() == 'fake-old-pool-key'


def test_switch_changes_writing_summary_and_actual_http_credentials(client, monkeypatch):
    pid = project(client)
    original = initialize(client)['active_endpoint_id']
    second = create(client)
    assert provider.config()['model'] == 'deepseek-original'
    response = client.post(f"/api/v1/deepseek/endpoints/{second['id']}/activate")
    assert response.status_code == 200
    assert provider.config()['model'] == material_summary.config()['model'] == 'deepseek-second'
    assert client.get('/api/v1/settings').json()['provider']['base_url'] == 'https://second.example/v1'
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.host == 'second.example'
        assert request.headers['Authorization'] == 'Bearer fake-second-key'
        assert json.loads(request.content)['model'] == 'deepseek-second'
        return ok()
    transport(monkeypatch, handle)
    tasks.model_call(tasks.enqueue(pid, 'provider_probe', {}), 'Return JSON.', 'hello', {})
    with material_summary.scope():
        tasks.model_call(tasks.enqueue(pid, 'provider_probe', {}), 'Return JSON.', 'summary', {})
    assert len(requests) == 2
    assert client.post(f'/api/v1/deepseek/endpoints/{original}/activate').status_code == 200
    assert security.get_key() == 'fake-original-key'


def test_existing_request_snapshot_survives_switch(client, monkeypatch):
    initialize(client)
    cfg, key = provider.config(), security.get_key()
    second = create(client)
    with provider.request_scope(cfg, key, route='deepseek'):
        client.post(f"/api/v1/deepseek/endpoints/{second['id']}/activate")
        transport(monkeypatch, lambda request: ok() if request.url.host == 'original.example' and request.headers['Authorization'] == 'Bearer fake-original-key' else pytest.fail('In-flight request changed provider'))
        provider.pool_generate('Return JSON.', 'hello')
    assert provider.config()['model'] == 'deepseek-second'


def test_edit_blank_key_preserves_key_and_invalid_edits_do_not_mutate(client):
    initialize(client)
    entry = create(client)
    response = client.put(f"/api/v1/deepseek/endpoints/{entry['id']}", json={'name': '改名', 'provider': {'model': 'deepseek-new'}})
    assert response.status_code == 200
    assert deepseek_pool.endpoint_key(next(ep for ep in deepseek_pool.config()['endpoints'] if ep['id'] == entry['id'])) == 'fake-second-key'
    before = client.get('/api/v1/deepseek/settings').json()
    for payload in ({'provider': {'model': 'gpt-test'}}, {'provider': {'base_url': 'http://unsafe.example'}}, {'api_key': '\n'}, {'priority': 0}):
        assert client.put(f"/api/v1/deepseek/endpoints/{entry['id']}", json=payload).status_code == 422
    assert client.get('/api/v1/deepseek/settings').json() == before


def test_delete_requires_switch_and_removes_encrypted_key(client):
    original = initialize(client)['active_endpoint_id']
    assert client.delete(f'/api/v1/deepseek/endpoints/{original}').status_code == 409
    entry = create(client)
    assert client.delete(f"/api/v1/deepseek/endpoints/{entry['id']}").status_code == 200
    assert entry['id'] not in deepseek_pool._read_keys()
    assert client.post('/api/v1/deepseek/endpoints/missing/activate').status_code == 404
    assert provider.config()['model'] == 'deepseek-original'


def test_model_and_zero_balance_queries_are_scoped_to_the_row(client, monkeypatch):
    initialize(client)
    entry = create(client)
    db.set_setting('account_usage', {'remaining_exact': '100'})
    def handle(request):
        assert request.url.host == 'second.example'
        assert request.headers['Authorization'] == 'Bearer fake-second-key'
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'deepseek-second'}, {'id': 'gpt-test'}]})
        return httpx.Response(200, json={'remaining': 0, 'unit': 'USD', 'is_valid': False})
    transport(monkeypatch, handle)
    assert client.post(f"/api/v1/deepseek/endpoints/{entry['id']}/models").json()['models'] == [{'id': 'deepseek-second', 'name': 'deepseek-second'}]
    receipt = client.post(f"/api/v1/deepseek/endpoints/{entry['id']}/balance").json()
    assert receipt['balance_known'] and float(receipt['remaining_exact']) == 0
    assert db.setting('account_usage') == {'remaining_exact': '100'}
    assert provider.config()['model'] == 'deepseek-original'


def test_gpt_fallback_cannot_be_replaced_by_deepseek_pool(client, monkeypatch):
    pid = project(client)
    initialize(client)
    create(client)
    client.put('/api/v1/gpt-fallback/settings', json={'api_key': 'fake-gpt-key', 'provider': {
        'base_url': 'https://gpt.example/v1', 'model': 'gpt-test', 'enabled': True}})
    assert client.put('/api/v1/deepseek/strategy', json={'mode': 'priority'}).status_code == 200
    requests = []
    def handle(request):
        requests.append(request)
        assert request.url.host == 'gpt.example'
        assert request.headers['Authorization'] == 'Bearer fake-gpt-key'
        return ok()
    transport(monkeypatch, handle)
    task = tasks.enqueue(pid, 'provider_probe', {'provider_profile': 'gpt'})
    tasks.model_call(task, 'Return JSON.', 'hello', {})
    assert len(requests) == 1


def test_legacy_settings_update_the_same_active_profile(client):
    initialize(client)
    response = client.put('/api/v1/material-summary/settings', json={'api_key': 'fake-updated-key', 'provider': {'model': 'deepseek-unified'}})
    assert response.status_code == 200
    assert provider.config()['model'] == material_summary.config()['model'] == 'deepseek-unified'
    assert security.get_key() == 'fake-updated-key'
    assert client.put('/api/v1/settings', json={'provider': {'model': 'deepseek-primary-update'}}).status_code == 200
    assert material_summary.config()['model'] == 'deepseek-primary-update'


def test_v2_session_alias_and_unified_settings_use_current_frontend_contract(client):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as fresh:
        response = fresh.post('/api/v2/session', json={'token': security.BOOTSTRAP_TOKEN})
        assert response.status_code == 200, response.text
        data = response.json()['data']
        fresh.headers['X-CSRF-Token'] = data['csrf']
        settings = fresh.get('/api/v2/deepseek/settings')
        assert settings.status_code == 200, settings.text
        assert settings.json()['data']['strategy']['mode'] == 'manual'
