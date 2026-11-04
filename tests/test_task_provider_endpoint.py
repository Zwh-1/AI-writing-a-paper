"""A task may pin a saved DeepSeek endpoint without changing global selection."""
from contextlib import nullcontext
import json

import httpx
import pytest

from test_workbench import client, project
from test_deepseek_pool import pool_endpoint
from app import db, deepseek_pool, expense_evidence, model_routing, provider, security, tasks
from app.summary_billing import BudgetExceeded, key_hash


def configure(mode='round_robin', streaming=False):
    endpoints = [pool_endpoint('global', '全局接口', 'https://global.example/v1',
                              model='deepseek-global', credential_ref='pool', max_attempts=1),
                 pool_endpoint('task', '任务接口', 'https://task.example/v1',
                              model='deepseek-task', credential_ref='pool', max_attempts=1,
                              streaming=streaming, context_limit=128000,
                              input_price=1.0, output_price=2.0)]
    endpoints[1]['expense_pricing_basis'] = {
        'kind': 'endpoint_configured', 'base_url': 'https://task.example',
        'model': 'deepseek-task', 'credential_hash': key_hash('fake-task-key'),
        'input_price': 1.0, 'output_price': 2.0, 'currency': 'USD'}
    endpoints[1]['pricing_source'] = 'configured'
    deepseek_pool._write_keys({'global': 'fake-global-key', 'task': 'fake-task-key'})
    db.set_setting('deepseek_pool', {'unified': True, 'active_endpoint_id': 'global',
        'strategy': {**deepseek_pool.DEFAULT_STRATEGY, 'mode': mode, 'retry_same_endpoint': 0},
        'endpoints': endpoints})
    return endpoints


def response(streaming):
    message = {'content': '{"ok":true}'}
    if streaming:
        body = {'choices': [{'delta': message, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 5}}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              content=('data: ' + json.dumps(body) + '\n\ndata: [DONE]\n\n').encode())
    return httpx.Response(200, json={'choices': [{'message': message, 'finish_reason': 'stop'}],
                                     'usage': {'prompt_tokens': 7, 'completion_tokens': 5}})


@pytest.mark.parametrize('mode', ['manual', 'priority', 'round_robin'])
@pytest.mark.parametrize('scoped', [False, True])
@pytest.mark.parametrize('streaming', [False, True])
def test_task_uses_saved_endpoint_and_credential_without_global_activation(client, monkeypatch, mode, scoped, streaming):
    pid = project(client)
    configure(mode, streaming)
    original = db.setting('deepseek_pool')
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url.host == 'task.example'
        assert request.headers['Authorization'] == 'Bearer fake-task-key'
        assert json.loads(request.content)['model'] == 'deepseek-task'
        return response(streaming)

    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(handle)))
    task = tasks.enqueue(pid, 'provider_probe', {'provider_profile': 'primary',
        'provider_endpoint_id': 'task', 'base_url': 'https://untrusted.example',
        'api_key': 'untrusted-key', 'model': 'untrusted-model'})
    with model_routing.task_scope(task) if scoped else nullcontext():
        result = tasks.model_call(task, 'Return JSON.', 'hello', {})
        if scoped:
            assert provider.config()['context_limit'] == 128000
            assert provider.config()['expense_pricing_basis']['kind'] == 'endpoint_configured'
    assert len(seen) == 1 and result['text'] == '{"ok":true}'
    assert db.setting('deepseek_pool') == original
    assert not db.setting('deepseek_pool_rotation')
    assert provider.config()['model'] == 'deepseek-global'
    assert security.get_key() == 'fake-global-key'
    run = db.one('SELECT model,cost,manifest FROM generation_runs')
    assert run['model'] == 'deepseek-task'
    assert run['cost'] == pytest.approx(0.000017)
    assert db.loads(run['manifest'])['provider_endpoint_id'] == 'task'


@pytest.mark.parametrize('change, expected', [
    ('missing', '不存在'), ('disabled', '已关闭'), ('model', '尚未选择模型'),
    ('key', '尚未配置密钥'), ('invalid', '标识无效')])
@pytest.mark.parametrize('scoped', [False, True])
def test_invalid_task_endpoint_fails_before_generation(client, monkeypatch, change, expected, scoped):
    pid = project(client)
    endpoints = configure()
    endpoint_id = 'task'
    if change == 'missing':endpoint_id = 'missing'
    elif change == 'invalid':endpoint_id = {'base_url': 'https://untrusted.example'}
    elif change == 'key':deepseek_pool._write_keys({'global': 'fake-global-key'})
    else:
        endpoints[1]['enabled' if change == 'disabled' else 'model'] = False if change == 'disabled' else ''
        cfg = db.setting('deepseek_pool');cfg['endpoints'] = endpoints
        db.set_setting('deepseek_pool', cfg)
    monkeypatch.setattr(provider, 'client', lambda cfg: pytest.fail('Invalid endpoint reached transport'))
    task = tasks.enqueue(pid, 'provider_probe', {'provider_endpoint_id': endpoint_id})
    with pytest.raises(provider.ProviderError, match=expected):
        if scoped:
            with model_routing.task_scope(task):pytest.fail('Invalid endpoint entered request scope')
        else:tasks.model_call(task, 'Return JSON.', 'hello', {})
    assert not db.rows('SELECT * FROM generation_runs')


def test_nested_task_scope_keeps_frozen_endpoint_and_price_basis(client):
    pid = project(client)
    configure()
    outer = tasks.enqueue(pid, 'provider_probe', {'provider_endpoint_id': 'task'})
    inner = tasks.enqueue(pid, 'provider_probe', {'provider_endpoint_id': 'global'})
    with model_routing.task_scope(outer):
        frozen = provider.config()
        with model_routing.task_scope(inner):
            assert provider.config() == frozen
            assert provider._REQUEST.get()['key'] == 'fake-task-key'
        assert provider.config() == frozen
    assert provider.config()['model'] == 'deepseek-global'


def test_task_endpoint_rejection_does_not_rotate_primary_profile(client, monkeypatch):
    pid = project(client)
    configure()
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(429, json={'error': 'rate limit'})

    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(handle)))
    task = tasks.enqueue(pid, 'provider_probe', {'provider_profile': 'primary', 'provider_endpoint_id': 'task'})
    with pytest.raises(provider.ProviderError):tasks.model_call(task, 'Return JSON.', 'hello', {})
    assert len(seen) == 1 and seen[0].url.host == 'task.example'
    assert db.setting('deepseek_pool')['active_endpoint_id'] == 'global'
    assert not db.setting('deepseek_pool_rotation')


@pytest.mark.parametrize('resolved', [False, True])
def test_task_endpoint_resolves_pricing_without_saving_or_bypassing_preflight(client, monkeypatch, resolved):
    pid = project(client)
    endpoints = configure()
    endpoints[1].update(base_url='https://api.x5m5x.com', model='deepseek-v4.1-flash-1017',
                        input_price=None, output_price=None)
    endpoints[1].pop('expense_pricing_basis')
    endpoints[1].pop('pricing_source')
    original = db.setting('deepseek_pool');original['endpoints'] = endpoints
    db.set_setting('deepseek_pool', original)
    monkeypatch.setattr(expense_evidence, 'DEFAULT_POLICY',
                        {'require_pricing_basis': True, 'block_on_unresolved': True})
    monkeypatch.setattr(provider, 'client', lambda cfg: pytest.fail('Pricing must remain local'))
    if not resolved:
        monkeypatch.setattr(provider, 'auto_bind_pricing', lambda cfg, key: (cfg, 'unresolved'))
    task = tasks.enqueue(pid, 'provider_probe', {'provider_endpoint_id': 'task'})
    with model_routing.task_scope(task):
        cfg = provider.config()
        if resolved:
            assert cfg['expense_pricing_basis']['estimate_only']
            assert cfg['expense_pricing_basis']['authenticity'] == 'family_default_conservative_estimate'
            assert expense_evidence.preflight(pid, cfg, provider._REQUEST.get()['key'])
        else:
            with pytest.raises(BudgetExceeded, match='计价依据'):
                expense_evidence.preflight(pid, cfg, provider._REQUEST.get()['key'])
    assert db.setting('deepseek_pool') == original
    assert not db.rows('SELECT * FROM generation_runs')


def test_gpt_fallback_keeps_own_identity_after_task_endpoint_rejection(client, monkeypatch):
    pid = project(client)
    configure()
    # Retry limits apply within a route; GPT receives its own bounded allowance.
    saved=db.setting('deepseek_pool')
    for endpoint in saved['endpoints']:
        if endpoint['id']=='task':endpoint['max_attempts']=2
    db.set_setting('deepseek_pool',saved)
    security.save_key('fake-gpt-key', model_routing.PROFILE)
    db.set_setting('gpt_fallback_provider', {**model_routing.DEFAULT, 'model': 'gpt-6.1-sol'})
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.host == 'task.example':return httpx.Response(401, json={'error': 'rejected'})
        assert request.url.host == 'www.ecoapi.cc'
        assert request.headers['Authorization'] == 'Bearer fake-gpt-key'
        assert not provider.config().get('provider_endpoint_id')
        return response(False)

    monkeypatch.setattr(provider, 'client', lambda cfg: httpx.Client(transport=httpx.MockTransport(handle)))
    task = tasks.enqueue(pid, 'provider_probe', {'provider_endpoint_id': 'task'})
    result = tasks.model_call(task, 'Return JSON.', 'hello', {})
    assert result['provider_snapshot']['model'] == 'gpt-6.1-sol'
    assert [request.url.host for request in seen] == ['task.example', 'www.ecoapi.cc']
    assert db.setting('deepseek_pool')['active_endpoint_id'] == 'global'
