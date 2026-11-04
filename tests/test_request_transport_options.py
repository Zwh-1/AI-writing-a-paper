"""Task-local transport choices reach the actual wire without changing settings."""
import json

import httpx
import pytest

from test_workbench import client, project
from test_deepseek_pool import pool_endpoint, configure_pool
from app import db, model_routing, provider, security, tasks


OPTIONS = {'protocol': 'openai', 'json_mode': True, 'streaming': True}


def test_direct_connection_option_is_isolated_and_uses_the_same_destination(monkeypatch):
    captured=[]
    class Connection:
        def __init__(self,**kwargs):captured.append(kwargs)
    monkeypatch.setattr(provider.httpx,'Client',Connection)
    monkeypatch.setattr(provider,'proxy_for',lambda url:'http://configured-proxy.example:8080')
    cfg={**provider.DEFAULT,'base_url':'https://saved-endpoint.example/v1'}
    direct=provider.apply_request_options(cfg,{'proxy_mode':'direct','connect_timeout_seconds':30})
    provider.client(direct)
    provider.client(cfg)
    assert captured[0]['proxy'] is None and captured[0]['timeout'].connect==30
    assert captured[1]['proxy']=='http://configured-proxy.example:8080'
    assert direct['base_url']==cfg['base_url'] and 'proxy_mode' not in cfg


@pytest.mark.parametrize('options',[{'proxy_mode':'other'},{'connect_timeout_seconds':0},
                                   {'connect_timeout_seconds':True},{'connect_timeout_seconds':121}])
def test_invalid_network_options_fail_before_connecting(options):
    with pytest.raises(ValueError):provider.apply_request_options(provider.DEFAULT,options)


def test_research_tool_profile_keeps_task_endpoint_credential_and_pricing_scope(monkeypatch):
    from app.writing_agent import profiles
    monkeypatch.setattr(security,'get_key',lambda profile='':'different-global-key')
    cfg={**provider.DEFAULT,'base_url':'https://task-endpoint.example',
         'model':'deepseek-task','provider_endpoint_id':'saved-task',
         'input_price':0.3,'output_price':1.2,'max_output_tokens':16384}
    with provider.request_scope(cfg,'task-endpoint-key',route='deepseek'):
        actual,key=profiles.resolve_config({'route':'primary','max_output_tokens':6144})
    assert key=='task-endpoint-key'
    assert actual['provider_endpoint_id']=='saved-task' and actual['base_url']==cfg['base_url']
    assert actual['input_price']==0.3 and actual['output_price']==1.2
    assert actual['max_output_tokens']==6144


def sse():
    payload = {'id': 'mock-response', 'choices': [{'delta': {'content': '{"ok":true}'},
                'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 7, 'completion_tokens': 5}}
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                          content=('data: ' + json.dumps(payload) + '\n\ndata: [DONE]\n\n').encode())


@pytest.mark.parametrize('pooled', [False, True])
def test_task_local_chat_json_stream_survives_disabled_endpoint_defaults(client, monkeypatch, pooled):
    pid = project(client)
    legacy = {**provider.DEFAULT, 'base_url': 'https://legacy.example/v1',
              'model': 'deepseek-legacy', 'protocol': 'responses',
              'json_mode': False, 'streaming': False, 'max_attempts': 1}
    security.save_key('mock-legacy-key')
    if pooled:
        endpoint = pool_endpoint('selected', 'selected', 'https://pool.example/v1',
                                 model='deepseek-pool', protocol='responses',
                                 streaming=False, json_mode=False, max_output_tokens=700)
        configure_pool(client, pid, [endpoint], {'mode': 'priority', 'retry_same_endpoint': 0})
    else:
        db.set_setting('provider', legacy)
    original = provider.config()
    seen = []

    def transport(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url.path == '/v1/chat/completions'
        assert request.url.host == ('pool.example' if pooled else 'legacy.example')
        assert body['model'] == ('deepseek-pool' if pooled else 'deepseek-legacy')
        assert body['stream'] is True and body['response_format'] == {'type': 'json_object'}
        assert body['max_tokens'] == (700 if pooled else 1000)
        return sse()

    monkeypatch.setattr(provider, 'client', lambda c: httpx.Client(transport=httpx.MockTransport(transport)))
    task = tasks.enqueue(pid, 'provider_probe', {'request_options': OPTIONS})
    with model_routing.task_scope(task):
        assert provider.config()['protocol'] == 'openai'
        result = tasks.model_call(task, 'Return JSON.', 'hello',
                                  {'request_options': {'max_output_tokens': 1000}})
    assert result['streamed'] and result['text'] == '{"ok":true}' and len(seen) == 1
    assert provider.config() == original
    assert db.one('SELECT COUNT(*) n FROM stream_chunks')['n'] == 1
    meta = db.loads(db.one('SELECT manifest FROM generation_runs')['manifest'])
    assert all(meta['request_options'][key] == value for key, value in OPTIONS.items())


def test_manifest_can_select_stream_without_task_scope(client, monkeypatch):
    pid = project(client)
    security.save_key('mock-key')
    db.set_setting('provider', {**provider.DEFAULT, 'base_url': 'https://model.example/v1',
                              'model': 'deepseek-test', 'streaming': False, 'json_mode': False})
    seen = []

    def transport(request):
        body = json.loads(request.content); seen.append(body)
        assert body['stream'] is True and body['response_format']['type'] == 'json_object'
        return sse()

    monkeypatch.setattr(provider, 'client', lambda c: httpx.Client(transport=httpx.MockTransport(transport)))
    task = tasks.enqueue(pid, 'provider_probe', {})
    tasks.model_call(task, 'Return JSON.', 'hello', {'request_options': OPTIONS})
    assert len(seen) == 1 and not provider.config()['streaming']


@pytest.mark.parametrize('options', [{'streaming': 'true'}, {'json_mode': 1},
                                     {'protocol': 'file'}, {'max_output_tokens': -1}])
def test_invalid_task_transport_options_are_rejected_before_call(client, options):
    pid = project(client)
    task = tasks.enqueue(pid, 'provider_probe', {'request_options': options})
    with pytest.raises(ValueError):
        with model_routing.task_scope(task):
            pytest.fail('Invalid request choices must not enter a provider scope')
    assert not db.rows('SELECT * FROM generation_runs')
