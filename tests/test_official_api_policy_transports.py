"""Official permission and peak-hour restrictions apply at real send boundaries."""
from datetime import datetime
import json

import httpx
import pytest

from test_workbench import client, project
from test_deepseek_pool import configure_pool, pool_endpoint
from app import db, official_api_policy as policy, provider, provider_stream, security, tasks
from app.summary_billing import budget
from app.writing_agent import provider_adapter


@pytest.fixture
def peak(client, monkeypatch):
    monkeypatch.setattr(policy, 'current_time',
                        lambda: datetime(2026, 11, 3, 10, 30, tzinfo=policy.SHANGHAI))
    security.save_key('fake-official-policy-key')
    return client


def configuration(protocol='openai', host='https://api.deepseek.com'):
    return {**provider.DEFAULT, 'base_url': host, 'model': 'deepseek-flash',
            'protocol': protocol, 'max_output_tokens': 1024, 'max_attempts': 1,
            'input_price': 0.3, 'output_price': 1.2}


def completion(request):
    if request.url.path.endswith('/models'):
        return httpx.Response(200, json={'data': [{'id': 'deepseek-flash'}]})
    body = json.loads(request.content)
    if body.get('stream'):
        data = {'id': 'fake-stream', 'choices': [{'delta': {'content': '{"ok":true}'},
                  'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 5}}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=('data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n').encode())
    if request.url.path.endswith('/responses'):
        return httpx.Response(200, json={'id': 'fake-response', 'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"ok":true}'}]}],
            'usage': {'input_tokens': 7, 'output_tokens': 5}})
    return httpx.Response(200, json={'id': 'fake-chat',
        'choices': [{'message': {'content': '{"ok":true}'}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 7, 'completion_tokens': 5}})


def mock_wire(monkeypatch, handler=completion):
    real_client = httpx.Client
    sent = []

    def transport(request):
        sent.append(request)
        return handler(request)

    def create(**kwargs):
        kwargs['transport'] = httpx.MockTransport(transport)
        return real_client(**kwargs)

    monkeypatch.setattr(provider.httpx, 'Client', create)
    return sent


def invoke(kind, cfg):
    endpoint = pool_endpoint('official', '官方', cfg['base_url'], model=cfg['model'],
                             protocol=cfg['protocol'], max_output_tokens=1024)
    if kind == 'plain':
        return provider.generate('Return JSON.', 'hello')
    if kind == 'pool_once':
        return provider._pool_generate_once('Return JSON.', 'hello', None, endpoint)[0]
    if kind == 'stream':
        return provider_stream.generate('Return JSON.', 'hello')
    if kind == 'stream_once':
        return provider_stream._stream_generate_once('Return JSON.', 'hello', lambda text: None, endpoint)[0]
    if kind == 'agent':
        return provider_adapter.transport('Return JSON.', json.dumps({
            'messages': [{'role': 'user', 'content': 'hello'}], 'tools': []}), cfg)
    raise AssertionError(kind)


@pytest.mark.parametrize('kind,protocol', [
    ('plain', 'openai'), ('plain', 'responses'), ('pool_once', 'openai'),
    ('stream', 'openai'), ('stream_once', 'openai'), ('agent', 'openai'), ('agent', 'responses'),
])
def test_default_permission_blocks_every_generation_transport_before_http(peak, monkeypatch, kind, protocol):
    sent = mock_wire(monkeypatch)
    cfg = configuration(protocol)
    with provider.request_scope(cfg, 'fake-official-policy-key', route='deepseek'):
        with pytest.raises(provider.ProviderError) as captured:
            invoke(kind, cfg)
    assert captured.value.code == policy.CODE
    assert not captured.value.unknown and not captured.value.retryable
    assert not captured.value.fallback_allowed
    assert sent == []


@pytest.mark.parametrize('kind', ['plain', 'pool_once', 'stream', 'stream_once', 'agent'])
def test_explicit_manual_override_allows_mock_generation_without_changing_the_endpoint(peak, monkeypatch, kind):
    policy.save({'allow_official_api': True, 'allow_official_peak_hours': True})
    sent = mock_wire(monkeypatch)
    cfg = configuration()
    with provider.request_scope(cfg, 'fake-official-policy-key', route='deepseek'):
        result = invoke(kind, cfg)
    assert len(sent) == 1 and sent[0].url.host == 'api.deepseek.com'
    assert result['text']
    assert policy.status()['allow_official_api'] is True
    assert policy.status()['allow_official_peak_hours'] is True


@pytest.mark.parametrize('streaming', [False, True])
def test_blocked_background_call_has_no_generation_run_or_budget_reservation(peak, monkeypatch, streaming):
    pid = project(peak)
    cfg = {**configuration(), 'streaming': streaming}
    db.set_setting('provider', cfg)
    task = tasks.enqueue(pid, 'provider_probe', {})
    before = budget(pid)
    sent = mock_wire(monkeypatch)
    with pytest.raises(provider.ProviderError) as captured:
        tasks.model_call(task, 'Return JSON.', 'hello', {})
    assert captured.value.code == policy.CODE
    assert not db.rows('SELECT id FROM generation_runs WHERE task_id=?', (task['id'],))
    assert not db.rows('SELECT run_id FROM stream_chunks')
    assert budget(pid)['used'] == before['used']
    assert sent == []


def test_models_detection_also_requires_general_consent(peak, monkeypatch):
    sent = mock_wire(monkeypatch)
    with provider.request_scope(configuration(), 'fake-official-policy-key'):
        with pytest.raises(provider.ProviderError) as captured:
            provider.models()
    assert captured.value.code == policy.CODE and sent == []
    policy.save({'allow_official_api': True, 'allow_official_peak_hours': True})
    with provider.request_scope(configuration(), 'fake-official-policy-key'):
        models = provider.models()
    assert [model['id'] for model in models] == ['deepseek-flash']
    assert len(sent) == 1 and sent[0].method == 'GET'


@pytest.mark.parametrize('method,path', [
    ('GET', '/v1/models'), ('GET', '/user/balance'), ('HEAD', '/v1/models'),
    ('OPTIONS', '/v1/chat/completions'), ('POST', '/v1/chat/completions'),
])
def test_no_official_http_method_bypasses_the_disabled_switch(peak, monkeypatch, method, path):
    sent = mock_wire(monkeypatch)
    cfg = configuration()
    with provider.request_scope(cfg, 'fake-official-policy-key'):
        with provider.client(cfg) as connection:
            with pytest.raises(provider.ProviderError) as captured:
                connection.request(method, provider.endpoint(cfg['base_url'], path))
    assert captured.value.code == policy.CODE and sent == []


@pytest.mark.parametrize('stamp', ['2026-11-03T12:30:00', '2026-11-03T20:00:00', '2026-11-08T15:00:00'])
def test_default_cannot_send_outside_peak_hours_or_on_weekends(peak, monkeypatch, stamp):
    monkeypatch.setattr(policy, 'current_time', lambda: datetime.fromisoformat(stamp).replace(tzinfo=policy.SHANGHAI))
    sent = mock_wire(monkeypatch)
    with provider.request_scope(configuration(), 'fake-official-policy-key'):
        with pytest.raises(provider.ProviderError) as captured:
            provider.generate('Return JSON.', 'hello')
    assert captured.value.code == policy.CODE and sent == []


@pytest.mark.parametrize('streaming', [False, True])
def test_general_consent_alone_still_blocks_peak_generation_before_http(peak, monkeypatch, streaming):
    policy.save({'allow_official_api': True})
    sent = mock_wire(monkeypatch)
    with provider.request_scope(configuration(), 'fake-official-policy-key'):
        with pytest.raises(provider.ProviderError) as captured:
            (provider_stream.generate if streaming else provider.generate)('Return JSON.', 'hello')
    assert captured.value.code == policy.PEAK_CODE and sent == []


def test_a_client_created_before_peak_cannot_send_after_nine_am(peak, monkeypatch):
    policy.save({'allow_official_api': True})
    moment = [datetime(2026, 11, 3, 8, 59, 59, tzinfo=policy.SHANGHAI)]
    monkeypatch.setattr(policy, 'current_time', lambda: moment[0])
    sent = mock_wire(monkeypatch)
    cfg = configuration()
    with provider.request_scope(cfg, 'fake-official-policy-key'):
        with provider.client(cfg) as connection:
            moment[0] = datetime(2026, 11, 3, 9, 0, tzinfo=policy.SHANGHAI)
            with pytest.raises(provider.ProviderError) as captured:
                connection.post(provider.endpoint(cfg['base_url'], '/v1/chat/completions'),
                                json={'model': cfg['model']})
    assert captured.value.code == policy.PEAK_CODE and sent == []


@pytest.mark.parametrize('field,code', [
    ('allow_official_api', policy.CODE), ('allow_official_peak_hours', policy.PEAK_CODE),
])
def test_an_open_client_observes_either_permission_being_switched_off(peak, monkeypatch, field, code):
    policy.save({'allow_official_api': True, 'allow_official_peak_hours': True})
    sent = mock_wire(monkeypatch)
    cfg = configuration()
    with provider.request_scope(cfg, 'fake-official-policy-key'):
        with provider.client(cfg) as connection:
            policy.save({field: False})
            with pytest.raises(provider.ProviderError) as captured:
                connection.post(provider.endpoint(cfg['base_url'], '/v1/responses'), json={})
    assert captured.value.code == code and sent == []


@pytest.mark.parametrize('streaming', [False, True])
def test_override_switched_off_after_reservation_releases_the_hold_and_sends_nothing(peak, monkeypatch, streaming):
    pid = project(peak)
    policy.save({'allow_official_api': True, 'allow_official_peak_hours': True})
    db.set_setting('provider', {**configuration(), 'streaming': streaming})
    task = tasks.enqueue(pid, 'provider_probe', {})
    before = budget(pid)['used']
    sent = mock_wire(monkeypatch)
    validate = tasks._validate_model_presend

    def disable_before_send(parent, manifest):
        assert db.one('SELECT status FROM generation_runs WHERE task_id=?', (task['id'],))['status'] == 'pending'
        policy.save({'allow_official_api': False})
        return validate(parent, manifest)

    monkeypatch.setattr(tasks, '_validate_model_presend', disable_before_send)
    with pytest.raises(provider.ProviderError) as captured:
        tasks.model_call(task, 'Return JSON.', 'hello', {})
    assert captured.value.code == policy.CODE
    run = db.one('SELECT status,reserved,manifest FROM generation_runs WHERE task_id=?', (task['id'],))
    assert run['status'] == 'failed_before_send' and run['reserved'] > 0
    assert db.loads(run['manifest'])['transport_failure']['http_request_sent'] is False
    assert db.loads(run['manifest'])['transport_failure']['phase'] == 'policy_before_http'
    assert budget(pid)['used'] == before
    assert sent == []


def test_deepseek_model_at_a_relay_is_not_mistaken_for_an_official_request(peak, monkeypatch):
    sent = mock_wire(monkeypatch)
    with provider.request_scope(configuration(host='https://relay.example/v1'), 'fake-relay-key'):
        result = provider.generate('Return JSON.', 'hello')
    assert result['text'] == '{"ok":true}'
    assert len(sent) == 1 and sent[0].url.host == 'relay.example'


@pytest.mark.parametrize('streaming', [False, True])
def test_automatic_pool_failover_never_sends_to_a_restricted_official_endpoint(peak, monkeypatch, streaming):
    pid = project(peak)
    endpoints = [pool_endpoint('relay-one', '中转一', 'https://first.example/v1', priority=1),
                 pool_endpoint('official', '官方', 'https://api.deepseek.com', priority=2),
                 pool_endpoint('relay-two', '中转二', 'https://last.example/v1', priority=3)]
    configure_pool(peak, pid, endpoints, {'mode': 'priority', 'retry_same_endpoint': 0})

    def handle(request):
        assert request.url.host != 'api.deepseek.com', 'Disabled official request reached the wire'
        if request.url.host == 'first.example':
            return httpx.Response(429, json={'error': 'rate limit'}, headers={'retry-after': '0'})
        return completion(request)

    sent = mock_wire(monkeypatch, handle)
    try:
        if streaming:
            result = provider_stream.pool_generate('Return JSON.', 'hello')
        else:
            result = provider.pool_generate('Return JSON.', 'hello')
        assert result['text'] == '{"ok":true}'
    except provider.ProviderError as error:
        assert getattr(error, 'code', None) == policy.CODE
        assert not error.unknown and not error.retryable
    assert sent and sent[0].url.host == 'first.example'
    assert all(request.url.host != 'api.deepseek.com' for request in sent)


@pytest.mark.parametrize('streaming', [False, True])
def test_all_official_pool_cannot_fall_back_to_official_legacy_configuration(peak, monkeypatch, streaming):
    pid = project(peak)
    endpoints = [pool_endpoint('official', '官方', 'https://api.deepseek.com')]
    configure_pool(peak, pid, endpoints, {'mode': 'priority', 'retry_same_endpoint': 0})
    sent = mock_wire(monkeypatch)
    with provider.request_scope(configuration(), 'fake-official-policy-key'):
        with pytest.raises(provider.ProviderError) as captured:
            (provider_stream.pool_generate if streaming else provider.pool_generate)('Return JSON.', 'hello')
    assert captured.value.code == policy.CODE
    assert sent == []
