"""Independent provider attempts and task-local failover, using mock HTTP only."""
from collections import Counter
import json

import httpx
import pytest

from test_workbench import client, file
from test_model_routing import configured, mock, ok
from test_deepseek_pool import configure_pool, pool_endpoint
from app import db, model_routing, official_api_policy, provider, tasks
from app.summary_billing import BudgetExceeded


@pytest.fixture
def routed(client):
    pid = configured(client, max_attempts=5)
    db.set_setting('provider', {**provider.config(), 'max_output_tokens': 1024,
                               'context_limit': 32000})
    db.set_setting('gpt_fallback_provider', {**model_routing.config(),
                   'max_output_tokens': 1024, 'context_limit': 32000, 'max_attempts': 5})
    return pid


def task(pid, payload=None):
    return tasks.enqueue(pid, 'provider_probe', payload or {})


def request(current, user='same logical input', manifest=None, images=None):
    return tasks.model_call(current, 'Preserve this exact system instruction.', user,
                            manifest or {'file_ids': []}, images)


def limited():
    return httpx.Response(429, json={'error': 'rate limit'}, headers={'retry-after': '0'})


def completed(request, text='{"ok":true}'):
    body = json.loads(request.content)
    if body.get('stream'):
        data = {'id': 'mock-stream', 'choices': [{'delta': {'content': text}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 5}}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=('data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n').encode())
    if request.url.path.endswith('/responses'):
        return httpx.Response(200, json={'id': 'mock-response', 'status': 'completed',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}],
            'usage': {'input_tokens': 7, 'output_tokens': 5}})
    return ok(text)


@pytest.mark.parametrize('failure', ['rate_limit', 'connect_timeout', 'connect_error'])
def test_five_safe_primary_failures_get_a_fresh_gpt_attempt_budget(routed, monkeypatch, failure):
    current = task(routed); sent = []
    def handle(wire):
        sent.append(wire)
        if wire.url.host == 'ds.example':
            if failure == 'connect_timeout':raise httpx.ConnectTimeout('before send', request=wire)
            if failure == 'connect_error':raise httpx.ConnectError('before send', request=wire)
            return limited()
        assert wire.headers['Authorization'] == 'Bearer fake-gpt-key'
        return ok()
    mock(monkeypatch, handle)
    result = request(current)
    assert [wire.url.host for wire in sent] == ['ds.example'] * 5 + ['www.ecoapi.cc']
    assert result['provider_snapshot']['model'] == 'gpt-6.1-sol'
    runs = db.rows('SELECT status,manifest FROM generation_runs WHERE task_id=? ORDER BY created_at,rowid', (current['id'],))
    assert [row['status'] for row in runs] == [
        'rejected' if failure == 'rate_limit' else 'failed_before_send'] * 5 + ['completed']
    assert [db.loads(row['manifest'])['provider_route'] for row in runs] == ['deepseek'] * 5 + ['gpt']
    assert db.loads(runs[-1]['manifest'])['fallback_reason']


def test_primary_success_on_fifth_attempt_does_not_call_gpt(routed, monkeypatch):
    sent = []
    def handle(wire):
        sent.append(wire)
        assert wire.url.host == 'ds.example'
        return ok() if len(sent) == 5 else limited()
    mock(monkeypatch, handle)
    assert request(task(routed))['provider_snapshot']['model'] == 'deepseek-test'
    assert len(sent) == 5


@pytest.mark.parametrize('primary_limit', [1, 3])
def test_smaller_primary_limit_is_respected_before_safe_fallback(routed, monkeypatch, primary_limit):
    db.set_setting('provider', {**provider.config(), 'max_attempts': primary_limit})
    sent = []
    def handle(wire):
        sent.append(wire)
        return limited() if wire.url.host == 'ds.example' else ok()
    mock(monkeypatch, handle)
    assert request(task(routed, {'provider_profile': 'primary'}))['provider_snapshot']['model'] == 'gpt-6.1-sol'
    assert [wire.url.host for wire in sent] == ['ds.example'] * primary_limit + ['www.ecoapi.cc']


def test_primary_success_resets_the_failure_streak_between_logical_calls(routed, monkeypatch):
    current = task(routed); attempts = Counter(); sent = []
    def handle(wire):
        sent.append(wire)
        assert wire.url.host == 'ds.example'
        user = json.loads(wire.content)['messages'][1]['content']
        attempts[user] += 1
        return limited() if attempts[user] <= (4 if user == 'first' else 1) else ok()
    mock(monkeypatch, handle)
    request(current, 'first')
    request(current, 'second')
    assert attempts == {'first': 5, 'second': 2}
    assert len(sent) == 7


@pytest.mark.parametrize('gpt_succeeds_on_fifth', [False, True])
def test_gpt_has_five_independent_bounded_attempts_after_five_primary_failures(routed, monkeypatch, gpt_succeeds_on_fifth):
    sent = []; counts = Counter()
    def handle(wire):
        sent.append(wire); counts[wire.url.host] += 1
        if wire.url.host == 'www.ecoapi.cc' and gpt_succeeds_on_fifth and counts[wire.url.host] == 5:
            return ok()
        return limited()
    mock(monkeypatch, handle)
    if gpt_succeeds_on_fifth:
        assert request(task(routed))['provider_snapshot']['model'] == 'gpt-6.1-sol'
    else:
        with pytest.raises(provider.ProviderError):request(task(routed))
    assert [wire.url.host for wire in sent] == ['ds.example'] * 5 + ['www.ecoapi.cc'] * 5
    assert len(db.rows('SELECT id FROM generation_runs')) == 10


@pytest.mark.parametrize('gpt_limit', [1, 3])
def test_gpt_respects_its_own_smaller_attempt_limit(routed, monkeypatch, gpt_limit):
    db.set_setting('gpt_fallback_provider', {**model_routing.config(), 'max_attempts': gpt_limit})
    sent = []
    mock(monkeypatch, lambda wire: sent.append(wire) or limited())
    with pytest.raises(provider.ProviderError):request(task(routed))
    assert [wire.url.host for wire in sent] == ['ds.example'] * 5 + ['www.ecoapi.cc'] * gpt_limit


def test_nested_format_calls_share_the_existing_gpt_stage_allowance(routed, monkeypatch):
    current = task(routed); sent = []
    def handle(wire):
        sent.append(wire)
        return httpx.Response(401, json={'error': 'explicit rejection'}) if wire.url.host == 'ds.example' else ok('format still needs repair')
    mock(monkeypatch, handle)
    with provider.attempt_budget(5):
        for index in range(5):request(current, user='format attempt ' + str(index))
        with pytest.raises(provider.ProviderError):request(current, user='sixth format attempt')
    assert [wire.url.host for wire in sent] == ['ds.example'] + ['www.ecoapi.cc'] * 5
    # A later independent stage starts with its own budget, keeping this task's route.
    assert request(db.one('SELECT * FROM tasks WHERE id=?', (current['id'],)))['provider_snapshot']['model'] == 'gpt-6.1-sol'
    assert sent[-1].url.host == 'www.ecoapi.cc' and len(sent) == 7


def test_successful_fallback_stays_on_gpt_only_for_the_same_saved_task(routed, monkeypatch):
    first = task(routed); sent = []; primary_attempts = []
    def handle(wire):
        sent.append(wire)
        if wire.url.host == 'ds.example':
            primary_attempts.append(wire)
            if len(primary_attempts) <= 5:return limited()
        return ok()
    mock(monkeypatch, handle)
    request(first, 'first logical call')
    reloaded = db.one('SELECT * FROM tasks WHERE id=?', (first['id'],))
    with model_routing.task_scope(reloaded):
        assert provider.config()['model'] == 'gpt-6.1-sol'
        request(reloaded, 'continue the same task')
    assert provider.config()['model'] == 'deepseek-test'
    assert request(task(routed), 'another task')['provider_snapshot']['model'] == 'deepseek-test'
    assert [wire.url.host for wire in sent] == ['ds.example'] * 5 + ['www.ecoapi.cc'] * 2 + ['ds.example']


@pytest.mark.parametrize('nested_scope', [False, True])
def test_another_task_does_not_inherit_a_live_task_scope_gpt_route(routed, monkeypatch, nested_scope):
    first = task(routed); second = task(routed); sent = []
    def handle(wire):
        sent.append(wire)
        user = json.loads(wire.content)['messages'][1]['content']
        if user == 'first must fall back' and wire.url.host == 'ds.example':return limited()
        return ok()
    mock(monkeypatch, handle)
    with model_routing.task_scope(first):
        request(first, 'first must fall back')
        assert provider.config()['model'] == 'gpt-6.1-sol'
        if nested_scope:
            with model_routing.task_scope(second):
                assert provider.config()['model'] == 'deepseek-test'
                assert request(second, 'second begins on primary')['provider_snapshot']['model'] == 'deepseek-test'
        else:
            assert request(second, 'second begins on primary')['provider_snapshot']['model'] == 'deepseek-test'
        assert provider.config()['model'] == 'gpt-6.1-sol'
        assert request(first, 'first continues on backup')['provider_snapshot']['model'] == 'gpt-6.1-sol'
    assert [wire.url.host for wire in sent] == ['ds.example'] * 5 + ['www.ecoapi.cc', 'ds.example', 'www.ecoapi.cc']


@pytest.mark.parametrize('protocol', ['openai', 'responses'])
@pytest.mark.parametrize('with_images', [False, True])
def test_fallback_keeps_exact_logical_inputs_images_and_request_options(routed, monkeypatch, protocol, with_images):
    db.set_setting('provider', {**provider.config(), 'supports_images': True})
    images = [{'mime': 'image/png', 'data': 'mock-image-bytes'}] if with_images else None
    options = {'protocol': protocol, 'max_output_tokens': 384, 'json_mode': True,
               'streaming': False, 'connect_timeout_seconds': 19, 'proxy_mode': 'direct'}
    sent = []; snapshots = []
    def handle(wire):
        sent.append(wire)
        return limited() if wire.url.host == 'ds.example' else completed(wire)
    def connection(cfg):
        snapshots.append(dict(cfg))
        return httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(provider, 'client', connection)
    request(task(routed), 'Exact user context with original evidence.',
            {'file_ids': [], 'request_options': options}, images)
    assert [wire.url.host for wire in sent] == ['ds.example'] * 5 + ['www.ecoapi.cc']
    assert all(cfg['connect_timeout_seconds'] == 19 and cfg['proxy_mode'] == 'direct'
               and cfg['streaming'] is False for cfg in snapshots)
    for wire in sent:
        body = json.loads(wire.content)
        assert body.get('stream', False) is False
        if protocol == 'responses':
            assert body['instructions'] == 'Preserve this exact system instruction.'
            content = body['input'][0]['content']
            assert content[0]['text'] == 'Exact user context with original evidence.'
            assert body['max_output_tokens'] == 384
            if with_images:assert content[1]['image_url'] == 'data:image/png;base64,mock-image-bytes'
        else:
            assert body['messages'][0]['content'] == 'Preserve this exact system instruction.'
            content = body['messages'][1]['content']
            assert (content[0]['text'] if with_images else content) == 'Exact user context with original evidence.'
            assert body.get('max_completion_tokens', body.get('max_tokens')) == 384
            if with_images:assert content[1]['image_url']['url'] == 'data:image/png;base64,mock-image-bytes'


def test_old_successful_stream_chunks_do_not_block_fallback_for_a_later_call(routed, monkeypatch):
    db.set_setting('provider', {**provider.config(), 'streaming': True})
    current = task(routed); sent = []
    def handle(wire):
        sent.append(wire)
        user = json.loads(wire.content)['messages'][1]['content']
        if user != 'historical success' and wire.url.host == 'ds.example':return limited()
        return completed(wire)
    mock(monkeypatch, handle)
    previous = request(current, 'historical success')
    assert db.one('SELECT 1 FROM stream_chunks WHERE run_id=?', (previous['run_id'],))
    result = request(current, 'new logical call')
    assert result['provider_snapshot']['model'] == 'gpt-6.1-sol'
    assert [wire.url.host for wire in sent] == ['ds.example'] * 6 + ['www.ecoapi.cc']


@pytest.mark.parametrize('failure', ['read_timeout', 'write_timeout', '503', 'partial', 'invalid_json'])
def test_unknown_or_executed_current_request_never_retries_or_falls_back(routed, monkeypatch, failure):
    sent = []
    def handle(wire):
        sent.append(wire)
        if failure == 'read_timeout':raise httpx.ReadTimeout('execution unknown', request=wire)
        if failure == 'write_timeout':raise httpx.WriteTimeout('execution unknown', request=wire)
        if failure == '503':return httpx.Response(503, json={'error': 'upstream unavailable'})
        if failure == 'invalid_json':return httpx.Response(200, text='not JSON')
        return httpx.Response(200, json={'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}]})
    mock(monkeypatch, handle)
    with pytest.raises(provider.ProviderError):request(task(routed))
    assert len(sent) == 1 and sent[0].url.host == 'ds.example'


def test_current_partial_stream_is_kept_without_any_gpt_call(routed, monkeypatch):
    db.set_setting('provider', {**provider.config(), 'streaming': True})
    current = task(routed); sent = []
    def handle(wire):
        sent.append(wire)
        data = {'choices': [{'delta': {'content': 'saved partial'}, 'finish_reason': None}]}
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=('data: ' + json.dumps(data) + '\n\ndata: {"error":"interrupted"}\n\n').encode())
    mock(monkeypatch, handle)
    with pytest.raises(provider.ProviderError) as captured:request(current)
    assert captured.value.unknown
    assert len(sent) == 1 and sent[0].url.host == 'ds.example'
    assert db.one('SELECT 1 FROM stream_chunks s JOIN generation_runs g ON g.id=s.run_id WHERE g.task_id=?', (current['id'],))


@pytest.mark.parametrize('failure', ['read_timeout', '503', 'partial'])
def test_unknown_gpt_result_stops_its_fresh_allowance_without_another_send(routed, monkeypatch, failure):
    sent = []
    def handle(wire):
        sent.append(wire)
        if wire.url.host == 'ds.example':return limited()
        if failure == 'read_timeout':raise httpx.ReadTimeout('backup execution unknown', request=wire)
        if failure == '503':return httpx.Response(503, json={'error': 'backup unavailable'})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'partial GPT'}, 'finish_reason': 'length'}]})
    mock(monkeypatch, handle)
    with pytest.raises(provider.ProviderError):request(task(routed))
    assert [wire.url.host for wire in sent] == ['ds.example'] * 5 + ['www.ecoapi.cc']


@pytest.mark.parametrize('status', [401, 402])
def test_explicit_rejection_can_fall_back_even_with_one_primary_opportunity(routed, monkeypatch, status):
    db.set_setting('provider', {**provider.config(), 'max_attempts': 1})
    sent = []
    def handle(wire):
        sent.append(wire)
        return httpx.Response(status, json={'error': 'explicit rejection'}) if wire.url.host == 'ds.example' else ok()
    mock(monkeypatch, handle)
    assert request(task(routed))['provider_snapshot']['model'] == 'gpt-6.1-sol'
    assert [wire.url.host for wire in sent] == ['ds.example', 'www.ecoapi.cc']


def test_disabled_official_api_cannot_be_bypassed_by_automatic_gpt(routed, monkeypatch):
    db.set_setting('provider', {**provider.config(), 'base_url': 'https://api.deepseek.com/v1'})
    mock(monkeypatch, lambda wire: pytest.fail('Disabled official API must not send or route to GPT'))
    with pytest.raises(provider.ProviderError) as captured:request(task(routed))
    assert captured.value.code == official_api_policy.CODE
    assert not db.rows('SELECT id FROM generation_runs')


def test_cancelled_task_never_sends_to_either_route(routed, monkeypatch):
    current = task(routed)
    db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?', (current['id'],))
    mock(monkeypatch, lambda wire: pytest.fail('Cancelled task reached a model'))
    with pytest.raises(InterruptedError):request(current)
    assert not db.rows('SELECT id FROM generation_runs')


def test_cancellation_after_zero_delay_rejection_stops_the_next_primary_attempt(routed, monkeypatch):
    current = task(routed); sent = []
    def handle(wire):
        sent.append(wire)
        db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?', (current['id'],))
        return limited()
    mock(monkeypatch, handle)
    with pytest.raises(InterruptedError):request(current)
    assert len(sent) == 1 and sent[0].url.host == 'ds.example'


@pytest.mark.parametrize('streaming', [False, True])
def test_cancellation_after_first_pool_rejection_stops_internal_retries_and_gpt(routed, client, monkeypatch, streaming):
    from app.summary_billing import budget
    endpoints = [pool_endpoint('cancel-first', 'First mock endpoint', 'https://first-pool.example/v1',
                               priority=1, max_attempts=5, streaming=streaming),
                 pool_endpoint('cancel-second', 'Second mock endpoint', 'https://second-pool.example/v1',
                               priority=2, max_attempts=5, streaming=streaming)]
    configure_pool(client, routed, endpoints, {'mode': 'priority', 'retry_same_endpoint': 4,
                                              'circuit_breaker': {'enabled': False}})
    db.set_setting('provider', {**provider.config(), 'streaming': streaming})
    current = task(routed); sent = []; before = budget(routed)['used']
    def handle(wire):
        sent.append(wire)
        db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?', (current['id'],))
        return limited()
    mock(monkeypatch, handle)
    with pytest.raises(InterruptedError):request(current)
    assert [wire.url.host for wire in sent] == ['first-pool.example']
    runs = db.rows('SELECT status FROM generation_runs WHERE task_id=?', (current['id'],))
    assert runs and all(run['status'] == 'rejected' for run in runs)
    assert budget(routed)['used'] == before
    assert not db.one("SELECT 1 FROM generation_runs WHERE task_id=? AND status='outcome_unknown'", (current['id'],))
    assert not db.one("SELECT 1 FROM generation_runs WHERE task_id=? AND json_extract(manifest,'$.provider_route')='gpt'",
                      (current['id'],))


@pytest.mark.parametrize('block', ['permission', 'budget', 'cancel'])
def test_a_new_gate_after_fifth_failure_blocks_gpt_before_http(routed, client, monkeypatch, block):
    original = file(client, routed)
    current = task(routed); sent = []
    def handle(wire):
        sent.append(wire)
        assert wire.url.host == 'ds.example'
        if len(sent) == 5:
            if block == 'permission':db.execute("UPDATE files SET egress='local_only' WHERE id=?", (original['id'],))
            elif block == 'budget':db.execute('UPDATE projects SET budget=0 WHERE id=?', (routed,))
            else:db.execute('UPDATE tasks SET cancel_requested=1 WHERE id=?', (current['id'],))
        return limited()
    mock(monkeypatch, handle)
    expected = InterruptedError if block == 'cancel' else BudgetExceeded if block == 'budget' else ValueError
    with pytest.raises(expected):request(current, manifest={'file_ids': [original['id']]})
    assert len(sent) == 5
