"""Cloudflare failures through real provider/task code with mock HTTP only."""
import httpx
import pytest

from test_workbench import client, project, file
from app import db, provider, provider_stream, tasks


BLOCK = '<!DOCTYPE html><html><title>Attention Required! | Cloudflare</title>fake-block-key</html>'


@pytest.mark.parametrize('protocol', ['openai', 'responses'])
@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('content_type', ['text/html', 'application/octet-stream'])
def test_generation_block_is_readable_and_not_retryable(monkeypatch, protocol, streaming, content_type):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(403, headers={'content-type': content_type, 'server': 'cloudflare'}, text=BLOCK)

    monkeypatch.setattr(provider, 'client', lambda c: httpx.Client(transport=httpx.MockTransport(handler)))
    with provider.request_scope({**provider.DEFAULT, 'protocol': protocol, 'model': 'fake-model'}, 'fake-block-key'):
        with pytest.raises(provider.ProviderError) as error:
            (provider_stream.generate if streaming else provider.generate)('rules', 'question')
    assert len(requests) == 1
    assert 'Cloudflare' in str(error.value) and 'HTTP 403' in str(error.value)
    assert '系统代理' in str(error.value) and '接口与设置' in str(error.value)
    assert '<html' not in str(error.value) and 'fake-block-key' not in str(error.value)
    assert error.value.unknown is False and error.value.retryable is False


def test_cloudflare_served_json_permission_error_is_not_a_web_challenge(monkeypatch):
    monkeypatch.setattr(provider, 'client', lambda c: httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, headers={'server': 'cloudflare'}, json={'error': 'model permission denied'})
    )))
    with provider.request_scope({**provider.DEFAULT, 'model': 'fake-model'}, 'fake-block-key'):
        with pytest.raises(provider.ProviderError, match='model permission denied') as error:
            provider.generate('rules', 'question')
    assert '网页访问防护' not in str(error.value)


def test_rules_block_records_one_rejection_without_charging_reservation(client, monkeypatch):
    pid = project(client)
    source = file(client, pid, '摘要必须说明研究方法。', role='rules')
    client.put('/api/v1/settings', json={'api_key': 'fake-block-key', 'provider': {'model': 'fake-model'}})
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(403, headers={'content-type': 'text/html'}, text=BLOCK)

    monkeypatch.setattr(provider, 'client', lambda c: httpx.Client(transport=httpx.MockTransport(handler)))
    task = client.post(f'/api/v1/projects/{pid}/rules/ai-extract', json={'file_ids': [source['id']]}).json()
    assert tasks.run_one()
    saved = client.get('/api/v1/tasks/' + task['id']).json()
    assert saved['status'] == 'failed' and 'Cloudflare' in saved['error']
    assert '<html' not in saved['error'] and 'fake-block-key' not in saved['error']
    assert len(requests) == 1 and not db.rows('SELECT * FROM rules')
    runs = db.rows('SELECT * FROM generation_runs WHERE task_id=?', (task['id'],))
    assert len(runs) == 1 and runs[0]['status'] == 'rejected'
    assert db.one("SELECT COALESCE(SUM(budget_charge),0) AS used FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')", (pid,))['used'] == 0


def test_historical_task_errors_are_readable_without_rewriting_history(client):
    pid = project(client)
    task = tasks.enqueue(pid, 'rules_ai', {})
    original = '中转站 HTTP 403：' + BLOCK
    db.execute("UPDATE tasks SET status='failed',error=? WHERE id=?", (original, task['id']))
    listed = client.get('/api/v1/tasks', params={'project_id': pid}).json()[0]
    detail = client.get('/api/v1/tasks/' + task['id']).json()
    assert listed['error'] == detail['error']
    assert 'Cloudflare' in detail['error'] and '<html' not in detail['error']
    assert db.one('SELECT error FROM tasks WHERE id=?', (task['id'],))['error'] == original
    assert provider.readable_error(None) is None
    assert provider.readable_error('普通失败') == '普通失败'
