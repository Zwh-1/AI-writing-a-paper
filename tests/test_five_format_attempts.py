"""Five bounded format opportunities with isolated, never-network transports."""
import json
import httpx
import pytest

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project, response  # noqa: F401
from test_writing_flow_safety import _basis, _running, _unit_setup
from app import db, provider, security, tasks, writing_flow
from app.summary_billing import BudgetExceeded
from app.writing_agent import provider_adapter
from app.writing_agent.common import unblob

_REAL_GENERATE=provider.generate


def test_argument_units_recover_on_fifth_attempt_and_completed_units_are_reused(writing_project, monkeypatch):
    task, section, context, manifest = _unit_setup(writing_project)
    counts = {}

    def transport(system, user, images=None):
        index = db.loads(user)['argument_unit']['index']
        counts[index] = counts.get(index, 0) + 1
        if index in (1, 2) and counts[index] < 5:
            return response({}) | {'text': '完整响应未按约定返回结构'}
        return response({'paragraphs': [{'text': f'第{index}个完整论证单元。'}]})

    monkeypatch.setattr(provider, 'generate', transport)
    result = writing_flow.generate(task, section, '只返回JSON', context, manifest, tasks.candidate_from_text)
    assert counts == {1: 5, 2: 5, 3: 1}
    assert len(db.loads(result['text'])['paragraphs']) == 3
    rows = db.rows('SELECT manifest FROM generation_runs WHERE task_id=?', (task['id'],))
    repairs = [db.loads(row['manifest']) for row in rows
               if db.loads(row['manifest']).get('writing_stage') == 'unit_format_repair']
    assert len(rows) == 11 and len(repairs) == 8
    assert {meta['writing_unit_index'] for meta in repairs} == set(range(8))
    assert len({meta['writing_flow_request_hash'] for meta in repairs}) == 8
    writing_flow.generate(task, section, '只返回JSON', context, manifest, tasks.candidate_from_text)
    assert counts == {1: 5, 2: 5, 3: 1}
    assert len(db.rows('SELECT id FROM generation_runs WHERE task_id=?', (task['id'],))) == 11


def test_argument_unit_stops_after_five_complete_invalid_responses(writing_project, monkeypatch):
    task, section, context, manifest = _unit_setup(writing_project)
    calls = []

    def transport(*args, **kwargs):
        calls.append(1)
        return response({}) | {'text': '正文没有JSON契约'}

    monkeypatch.setattr(provider, 'generate', transport)
    with pytest.raises(ValueError, match='论证单元响应无效'):
        writing_flow.generate(task, section, '只返回JSON', context, manifest, tasks.candidate_from_text)
    assert len(calls) == 5
    rows = db.rows('SELECT status,manifest FROM generation_runs WHERE task_id=?', (task['id'],))
    assert len(rows) == 5 and all(row['status'] == 'completed' for row in rows)
    assert all(db.loads(row['manifest']).get('output_blob') for row in rows)
    step = db.one("SELECT * FROM writing_steps WHERE task_id=? AND stage='argument_units'", (task['id'],))
    assert step['state'] == 'needs_review' and unblob(step['output_blob'])['units'] == []


def _semantic_setup(e):
    task = _running(e)
    context, manifest = _basis(e)
    paragraph = {'text': '模块职责清晰有助于维护。', 'source_ids': [context['sources'][0]['id']]}
    local = writing_flow._local_quality(task, [paragraph], context, manifest)
    return task, security.require_section(e['sid']), context, manifest, paragraph, local


@pytest.mark.parametrize('invalid_kind', ['non_json', 'missing_claims'])
@pytest.mark.parametrize('layout', ['legacy', 'cache_v2'])
def test_semantic_response_gets_five_total_contract_attempts(writing_project, monkeypatch, invalid_kind, layout):
    task, section, context, manifest, paragraph, local = _semantic_setup(writing_project)
    payload={**db.loads(task['payload'],{}),'prompt_layout_version':layout}
    task['payload']=db.dumps(payload)
    db.execute('UPDATE tasks SET payload=? WHERE id=?',(task['payload'],task['id']))
    calls = []

    def transport(system, user, images=None):
        payload = db.loads(user)
        calls.append((system, payload))
        if len(calls) < 5:
            return response({'claim_reviews': []}) if invalid_kind == 'missing_claims' else response({}) | {'text': '未按约定返回核验JSON'}
        source = payload['actual_sources'][0]
        return response({'claim_reviews': [{'claim_id': claim['claim_id'], 'support': 'supported',
                         'source_quotes': [{'source_id': source['id'], 'quote': source['text']}]} for claim in payload['claims']],
                         'point_reviews': [], 'findings': []})

    monkeypatch.setattr(provider, 'generate', transport)
    result = writing_flow._semantic(task, section, [paragraph], context, manifest, local)
    assert len(calls) == 5 and result['coverage_complete'] is True
    assert [attempt['valid'] for attempt in result['format_attempts']] == [False] * 4 + [True]
    assert len({system for system, _ in calls}) == (1 if layout == 'cache_v2' else 5)
    if layout == 'cache_v2':
        assert [request['request_parameters']['format_attempt'] for _,request in calls[1:]] == [2,3,4,5]
        assert all(request['request_parameters']['prior_validation_error'] for _,request in calls[1:])
    assert all(payload['expected_claim_ids'] == calls[0][1]['expected_claim_ids'] for _, payload in calls)
    rows = db.rows('SELECT manifest FROM generation_runs WHERE task_id=?', (task['id'],))
    assert len(rows) == 5
    stages = [db.loads(row['manifest']).get('writing_stage') for row in rows]
    assert stages.count('semantic_review') == 1 and stages.count('semantic_format_repair') == 4
    assert all(db.loads(row['manifest']).get('output_blob') for row in rows)


def test_semantic_network_failures_and_format_repairs_share_five_attempts(writing_project, monkeypatch):
    task,section,context,manifest,paragraph,local=_semantic_setup(writing_project)
    db.set_setting('provider',{**provider.config(),'max_attempts':5,'protocol':'openai','streaming':False})
    requests=[]

    def transport(request):
        requests.append(request)
        if len(requests) in (1,3):raise httpx.ConnectError('synthetic connection failure',request=request)
        payload=json.loads(json.loads(request.content)['messages'][-1]['content'])
        text='完整响应不是JSON'
        if len(requests)==5:
            source=payload['actual_sources'][0]
            text=db.dumps({'claim_reviews':[{'claim_id':claim['claim_id'],'support':'supported',
                 'source_quotes':[{'source_id':source['id'],'quote':source['text']}]} for claim in payload['claims']],
                 'point_reviews':[],'findings':[]})
        return httpx.Response(200,json={'id':'synthetic','choices':[{
            'message':{'content':text},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':3,'completion_tokens':4}})

    monkeypatch.setattr(provider,'generate',_REAL_GENERATE)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(transport)))
    result=writing_flow._semantic(task,section,[paragraph],context,manifest,local)
    assert len(requests)==5 and result['coverage_complete'] is True
    assert len(result['format_attempts'])==3
    rows=db.rows('SELECT status FROM generation_runs WHERE task_id=?',(task['id'],))
    assert len(rows)==5
    assert sum(row['status']=='failed_before_send' for row in rows)==2
    assert sum(row['status']=='completed' for row in rows)==3


def test_five_invalid_semantic_responses_remain_unverified(writing_project, monkeypatch):
    task, section, context, manifest, paragraph, local = _semantic_setup(writing_project)
    calls = []

    def transport(*args, **kwargs):
        calls.append(1)
        return response({'claim_reviews': []})

    monkeypatch.setattr(provider, 'generate', transport)
    result = writing_flow._semantic(task, section, [paragraph], context, manifest, local)
    assert len(calls) == 5 and result['coverage_complete'] is False
    assert result['status'] == 'incomplete' and result['claims'] == []
    assert len(result['format_attempts']) == 5
    assert result['findings'][0]['kind'] == 'verification_incomplete'
    assert len(result['findings'][0]['validation_attempts']) == 5
    saved = db.loads(db.one('SELECT result FROM tasks WHERE id=?', (task['id'],))['result'])
    checkpoint = saved['writing_flow_checkpoint']
    assert checkpoint['quality_verified'] is False
    assert unblob(checkpoint['candidate_blob'])['candidate']['paragraphs'] == [paragraph]


def test_unsupported_quote_is_not_retried_until_it_looks_supported(writing_project, monkeypatch):
    task, section, context, manifest, paragraph, local = _semantic_setup(writing_project)
    calls = []

    def transport(system, user, images=None):
        payload = db.loads(user)
        calls.append(1)
        return response({'claim_reviews': [{'claim_id': claim['claim_id'], 'support': 'supported',
                         'source_quotes': []} for claim in payload['claims']], 'point_reviews': [], 'findings': []})

    monkeypatch.setattr(provider, 'generate', transport)
    result = writing_flow._semantic(task, section, [paragraph], context, manifest, local)
    assert len(calls) == 1
    assert all(claim['support'] == 'unknown' and not claim['quote_verified'] for claim in result['claims'])
    assert len(result['format_attempts']) == 1


@pytest.mark.parametrize('failure', ['unknown', 'partial', 'budget', 'policy', 'cancel'])
def test_semantic_does_not_treat_request_failures_as_format_repairs(writing_project, monkeypatch, failure):
    task, section, context, manifest, paragraph, local = _semantic_setup(writing_project)
    calls = []
    errors = {'unknown': provider.ProviderError('结果未知', unknown=True),
              'partial': provider.ProviderError('仅收到片段', response_result={'text': 'partial'}),
              'budget': BudgetExceeded('预算不足'),
              'policy': provider.ProviderError('官方API时段限制'),
              'cancel': InterruptedError('已取消')}
    errors['policy'].failure_phase = 'policy_before_http'

    def fail(*args, **kwargs):
        calls.append(1)
        raise errors[failure]

    monkeypatch.setattr(writing_flow, '_cached_call', fail)
    with pytest.raises(type(errors[failure])):
        writing_flow._semantic(task, section, [paragraph], context, manifest, local)
    assert len(calls) == 1


@pytest.mark.parametrize('succeed', [False, True])
def test_agent_complete_json_repair_is_bounded_to_five(succeed, monkeypatch):
    attempts = [0]
    systems = []

    def call(task, profile, system, payload, manifest, tools, messages, items):
        attempt = manifest['structured_json_attempt']
        attempts.append(attempt)
        systems.append(system)
        text = db.dumps({'sentences': []}) if succeed and attempt == 4 else '{invalid JSON'
        return provider_adapter.finish_structured(task, profile, system, payload, manifest,
            {'text': text, 'tool_calls': []}, None, tools, messages, items)

    monkeypatch.setattr(provider_adapter, 'call', call)
    args = ({}, {}, '只返回JSON', {}, {'stage': 'annotation'}, {'text': '{invalid JSON', 'tool_calls': []}, None, None, None, None)
    if succeed:
        result = provider_adapter.finish_structured(*args)
        assert db.loads(result['text']) == {'sentences': []}
    else:
        with pytest.raises(provider.ProviderError):provider_adapter.finish_structured(*args)
    assert attempts == [0, 1, 2, 3, 4] and len(set(systems)) == 4
