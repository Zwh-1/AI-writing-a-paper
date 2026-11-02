"""Multi-endpoint DeepSeek pool: failover, retry, circuit breaker and key isolation."""
import json
import time
import httpx
import pytest
from test_workbench import client, project
from app import db, provider, security, tasks, deepseek_pool


def pool_endpoint(endpoint_id, name, url, model='deepseek-test', priority=1,
                  enabled=True, credential_ref='primary', **extra):
    return {
        'id': endpoint_id, 'name': name, 'enabled': enabled, 'priority': priority,
        'base_url': url, 'protocol': 'openai', 'model': model,
        'max_output_tokens': 8192, 'timeout_seconds': 60, 'context_limit': 100000,
        'max_request_cost': 2.0, 'input_price': None, 'output_price': None,
        'currency': 'USD', 'json_mode': True, 'supports_images': False,
        'thinking_enabled': False, 'streaming': False, 'max_attempts': 3,
        'credential_ref': credential_ref, **extra,
    }


def configure_pool(client, pid, endpoints, strategy=None):
    """Configure the pool via the API and set the primary key."""
    assert client.put('/api/v1/settings', json={'api_key': 'fake-primary-key',
                                                'provider': {'base_url': 'https://ds.example/v1',
                                                             'model': 'deepseek-test',
                                                             'max_attempts': 1}}).status_code == 200
    body = {'endpoints': endpoints}
    if strategy:
        body['strategy'] = strategy
    r = client.put('/api/v1/deepseek-pool/settings', json=body)
    assert r.status_code == 200, r.text
    return r.json()


def mock(monkeypatch, callback):
    monkeypatch.setattr(provider, 'client', lambda c: httpx.Client(transport=httpx.MockTransport(callback)))


def ok(text='{"ok":true}', **kwargs):
    return httpx.Response(200, json={'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
                                     'usage': {'prompt_tokens': 7, 'completion_tokens': 5}, **kwargs})


def call(pid, images=None):
    task = tasks.enqueue(pid, 'provider_probe', {})
    return tasks.model_call(task, 'Return JSON with ok true.', 'hello', {'file_ids': []}, images)


def test_pool_settings_round_trip(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    data = configure_pool(client, pid, eps, {'mode': 'round_robin', 'retry_same_endpoint': 1,
                                     'circuit_breaker': {'enabled': True, 'failure_threshold': 2,
                                                         'cooldown_seconds': 30}})
    assert len(data['endpoints']) == 2
    assert data['strategy']['mode'] == 'round_robin'
    assert data['strategy']['retry_same_endpoint'] == 1
    assert data['strategy']['circuit_breaker']['failure_threshold'] == 2
    # Primary-ref endpoints share the primary key and are therefore configured.
    assert all(e['credential']['configured'] for e in data['endpoints'])
    assert db.setting('deepseek_pool')['endpoints'][0]['id'] == 'ep1'


def test_pool_endpoint_key_is_isolated(client, monkeypatch):
    pid = project(client)
    eps = [pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', credential_ref='pool')]
    configure_pool(client, pid, eps)
    r = client.post('/api/v1/deepseek-pool/endpoints/ep1/key', json={'api_key': 'pool-key-123'})
    assert r.status_code == 200, r.text
    ep = next(e for e in r.json()['endpoints'] if e['id'] == 'ep1')
    assert ep['credential']['configured'] and ep['credential']['masked'].endswith('123')
    assert deepseek_pool.endpoint_key(deepseek_pool.config()['endpoints'][0]) == 'pool-key-123'
    # Primary key is unchanged.
    assert security.get_key() == 'fake-primary-key'


def test_pool_failover_switches_to_next_endpoint(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 0})
    requests = []
    def handle(r):
        requests.append(r)
        if r.url.host == 'ds-one.example':
            return httpx.Response(429, json={'error': 'rate limit'}, headers={'retry-after': '0'})
        assert r.url.host == 'ds-two.example'
        assert r.headers['Authorization'] == 'Bearer fake-primary-key'
        return ok()
    mock(monkeypatch, handle)
    result = call(pid)
    assert len(requests) == 2
    assert result['pool_used'] == 'pool'
    assert result['pool_endpoint'] == '备端点'
    assert result['pool_attempt'] == 1
    # Endpoint 1 has a failure recorded.
    ep1 = next(e for e in deepseek_pool.config()['endpoints'] if e['id'] == 'ep1')
    assert ep1['health']['consecutive_failures'] >= 1


def test_pool_same_endpoint_retry_then_failover(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 2})
    requests = []
    def handle(r):
        requests.append(r)
        if r.url.host == 'ds-one.example':
            return httpx.Response(429, json={'error': 'rate limit'})
        assert r.url.host == 'ds-two.example'
        return ok()
    mock(monkeypatch, handle)
    result = call(pid)
    # 1 initial + 2 retries on ep1 = 3, then 1 on ep2 = 4 total.
    assert len(requests) == 4
    assert [r.url.host for r in requests] == ['ds-one.example'] * 3 + ['ds-two.example']
    assert result['pool_endpoint'] == '备端点'
    assert result['pool_attempt'] == 1


def test_pool_all_endpoints_exhausted_raises(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 0})
    def handle(r):
        return httpx.Response(429, json={'error': 'rate limit'})
    mock(monkeypatch, handle)
    with pytest.raises(provider.ProviderError) as exc:
        call(pid)
    assert getattr(exc.value, 'pool_endpoints_used', None) == ['ep1', 'ep2']


def test_pool_unknown_outcome_never_replayed(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 2})
    requests = []
    def handle(r):
        requests.append(r)
        return httpx.Response(503, json={'error': 'service unavailable'})
    mock(monkeypatch, handle)
    with pytest.raises(provider.ProviderError):
        call(pid)
    # 503 is unknown -> never replayed to any endpoint.
    assert len(requests) == 1
    assert requests[0].url.host == 'ds-one.example'


def test_pool_partial_output_never_replayed(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 2})
    requests = []
    def handle(r):
        requests.append(r)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}],
                                         'usage': {'prompt_tokens': 7, 'completion_tokens': 5}})
    mock(monkeypatch, handle)
    with pytest.raises(provider.ProviderError):
        call(pid)
    assert len(requests) == 1
    assert requests[0].url.host == 'ds-one.example'


def test_pool_circuit_breaker_opens_after_threshold(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 0,
                              'circuit_breaker': {'enabled': True, 'failure_threshold': 2,
                                                  'cooldown_seconds': 120}})
    requests = []
    def handle(r):
        requests.append(r)
        if r.url.host == 'ds-one.example':
            return httpx.Response(429, json={'error': 'rate limit'})
        return ok()
    mock(monkeypatch, handle)
    # First call: ep1 fails (1), ep2 succeeds.
    assert call(pid)['pool_endpoint'] == '备端点'
    # Second call: ep1 fails (2 -> threshold -> open), ep2 succeeds.
    assert call(pid)['pool_endpoint'] == '备端点'
    ep1 = next(e for e in deepseek_pool.config()['endpoints'] if e['id'] == 'ep1')
    assert ep1['health']['status'] == 'open'
    # Third call: ep1 is open (skipped), ep2 used directly.
    assert call(pid)['pool_endpoint'] == '备端点'
    assert requests[-1].url.host == 'ds-two.example'


def test_pool_circuit_breaker_recovers_after_cooldown(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 0,
                                      'circuit_breaker': {'enabled': True, 'failure_threshold': 1,
                                                          'cooldown_seconds': 1}})
    requests = []
    def handle(r):
        requests.append(r)
        if r.url.host == 'ds-one.example':
            return httpx.Response(429, json={'error': 'rate limit'})
        return ok()
    mock(monkeypatch, handle)
    assert call(pid)['pool_endpoint'] == '备端点'
    ep1 = next(e for e in deepseek_pool.config()['endpoints'] if e['id'] == 'ep1')
    assert ep1['health']['status'] == 'open'
    # Cooldown 1s means ep1 is still open on the immediate next call.
    assert call(pid)['pool_endpoint'] == '备端点'
    ep1 = next(e for e in deepseek_pool.config()['endpoints'] if e['id'] == 'ep1')
    assert ep1['health']['status'] == 'open'


def test_pool_round_robin_rotates(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'round_robin', 'retry_same_endpoint': 0})
    requests = []
    def handle(r):
        requests.append(r)
        return ok()
    mock(monkeypatch, handle)
    # First call uses ep1 (index 0), second uses ep2 (index 1), third back to ep1.
    call(pid)
    assert requests[0].url.host == 'ds-one.example'
    call(pid)
    assert requests[1].url.host == 'ds-two.example'
    call(pid)
    assert requests[2].url.host == 'ds-one.example'


def test_pool_legacy_fallback_when_no_endpoints(client, monkeypatch):
    pid = project(client)
    assert client.put('/api/v1/settings', json={'api_key': 'fake-primary-key',
                                                'provider': {'base_url': 'https://ds.example/v1',
                                                             'model': 'deepseek-test',
                                                             'max_attempts': 1}}).status_code == 200
    requests = []
    def handle(r):
        requests.append(r)
        assert r.url.host == 'ds.example'
        return ok()
    mock(monkeypatch, handle)
    result = call(pid)
    assert len(requests) == 1
    assert result['pool_used'] == 'legacy'
    assert result['provider_snapshot']['base_url'] == 'https://ds.example/v1'


def test_pool_connect_error_is_retryable_and_switches(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 1})
    requests = []
    def handle(r):
        requests.append(r)
        if r.url.host == 'ds-one.example':
            raise httpx.ConnectTimeout('connect timeout', request=r)
        assert r.url.host == 'ds-two.example'
        return ok()
    mock(monkeypatch, handle)
    result = call(pid)
    assert result['pool_endpoint'] == '备端点'
    # retry_same_endpoint=1 means ep1 gets 1 retry (2 attempts) then ep2 succeeds.
    assert len(requests) == 3


def test_pool_disabled_endpoint_is_skipped(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '停用端点', 'https://ds-one.example/v1', priority=1, enabled=False),
        pool_endpoint('ep2', '启用端点', 'https://ds-two.example/v1', priority=2),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 0})
    requests = []
    def handle(r):
        requests.append(r)
        assert r.url.host == 'ds-two.example'
        return ok()
    mock(monkeypatch, handle)
    result = call(pid)
    assert result['pool_endpoint'] == '启用端点'
    assert len(requests) == 1


def test_pool_validation_rejects_bad_url(client, monkeypatch):
    pid = project(client)
    r = client.put('/api/v1/deepseek-pool/settings',
                   json={'endpoints': [pool_endpoint('ep1', 'bad', 'http://insecure.example/v1')]})
    assert r.status_code == 422


def test_pool_validation_rejects_too_many_endpoints(client, monkeypatch):
    pid = project(client)
    eps = [pool_endpoint(f'ep{i}', f'端点{i}', f'https://ds-{i}.example/v1') for i in range(7)]
    r = client.put('/api/v1/deepseek-pool/settings', json={'endpoints': eps})
    assert r.status_code == 422


def test_pool_health_does_not_leak_keys(client, monkeypatch):
    pid = project(client)
    eps = [pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', credential_ref='pool')]
    configure_pool(client, pid, eps)
    client.post('/api/v1/deepseek-pool/endpoints/ep1/key', json={'api_key': 'super-secret-key'})
    r = client.get('/api/v1/deepseek-pool/settings')
    assert r.status_code == 200
    assert 'super-secret-key' not in r.text
    assert r.json()['endpoints'][0]['credential']['configured'] is True


def _sse_response(text):
    """Build an SSE stream response with a single delta frame and completion."""
    frame = b'data: ' + json.dumps({
        'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 7, 'completion_tokens': 5},
    }).encode() + b'\n\n'
    done = b'data: [DONE]\n\n'
    return httpx.Response(200, content=frame + done,
                          headers={'content-type': 'text/event-stream'})


def test_pool_streaming_failover_before_delta(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1, streaming=True),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2, streaming=True),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 0})
    requests = []
    def handle(r):
        requests.append(r)
        if r.url.host == 'ds-one.example':
            return httpx.Response(429, json={'error': 'rate limit'})
        return _sse_response('流式输出')
    mock(monkeypatch, handle)
    cfg = provider.config()
    cfg['streaming'] = True
    db.set_setting('provider', cfg)
    result = call(pid)
    assert result['pool_used'] == 'pool'
    assert result['pool_endpoint'] == '备端点'
    assert '流式输出' in result['text']
    assert len(requests) == 2


def test_pool_streaming_never_replays_after_delta(client, monkeypatch):
    pid = project(client)
    eps = [
        pool_endpoint('ep1', '主端点', 'https://ds-one.example/v1', priority=1, streaming=True),
        pool_endpoint('ep2', '备端点', 'https://ds-two.example/v1', priority=2, streaming=True),
    ]
    configure_pool(client, pid, eps, {'mode': 'priority', 'retry_same_endpoint': 0})
    requests = []
    def handle(r):
        requests.append(r)
        # First frame delivers output, then connection drops before completion.
        if r.url.host == 'ds-one.example':
            frame = b'data: ' + json.dumps({
                'choices': [{'delta': {'content': '部分'}, 'finish_reason': None}],
            }).encode() + b'\n\n'
            return httpx.Response(200, content=frame,
                                  headers={'content-type': 'text/event-stream'})
        return _sse_response('不应到达')
    mock(monkeypatch, handle)
    cfg = provider.config()
    cfg['streaming'] = True
    db.set_setting('provider', cfg)
    with pytest.raises(provider.ProviderError):
        call(pid)
    # Once a delta was delivered, never failover to ep2.
    assert len(requests) == 1
    assert requests[0].url.host == 'ds-one.example'
