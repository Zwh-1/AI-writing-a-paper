"""One strict usage contract across every AI transport and billing consumer."""
from copy import deepcopy
import json

import httpx
import pytest

from test_workbench import client, project
from test_deepseek_pool import pool_endpoint
from app import adapter, db, provider, provider_stream, rule_review_receipts, security, tasks
from app.writing_agent import billing, profiles, provider_adapter


def vendor_usage(shape):
    if shape=='deepseek':
        return {'prompt_tokens':100,'completion_tokens':10,'prompt_cache_hit_tokens':64,'prompt_cache_miss_tokens':36}
    if shape=='chat':
        return {'prompt_tokens':100,'completion_tokens':10,'prompt_tokens_details':{'cached_tokens':64}}
    return {'input_tokens':100,'output_tokens':10,'input_tokens_details':{'cached_tokens':64}}


@pytest.mark.parametrize('shape',['deepseek','chat','responses'])
def test_strict_cache_usage_and_repeated_normalization_preserve_original_raw(shape):
    raw=vendor_usage(shape);original=deepcopy(raw)
    normalized=adapter.normalize_usage(raw)
    assert normalized['input_tokens']==100 and normalized['output_tokens']==10
    assert normalized['cache_hit_tokens']==64 and normalized['cache_miss_tokens']==36
    assert normalized['cache_reporting']==('reported' if shape=='deepseek' else 'derived_miss')
    assert normalized['raw']==original and raw==original
    assert adapter.normalize_usage(normalized)==normalized


@pytest.mark.parametrize('value',[True,False,-1,1.0,'100',{},[]])
@pytest.mark.parametrize('field',['input_tokens','output_tokens','cache_hit_tokens','cache_miss_tokens'])
def test_invalid_counts_and_booleans_are_unknown(field,value):
    usage={'input_tokens':100,'output_tokens':10,'cache_hit_tokens':64,'cache_miss_tokens':36}
    usage[field]=value
    normalized=adapter.normalize_usage(usage)
    if field in ('input_tokens','output_tokens'):assert normalized[field] is None
    if field!='output_tokens':
        assert normalized['cache_hit_tokens'] is None and normalized['cache_miss_tokens'] is None
    assert adapter.normalize_usage(normalized)==normalized


@pytest.mark.parametrize('usage',[
    {'prompt_tokens_details':{'cached_tokens':64},'completion_tokens':10},
    {'input_tokens_details':{'cached_tokens':64},'output_tokens':10},
    {'prompt_cache_hit_tokens':64,'prompt_cache_miss_tokens':36,'completion_tokens':10},
])
def test_cache_subcounts_never_become_a_missing_input_total(usage):
    result=adapter.normalize_usage(usage)
    assert result['input_tokens'] is None and result['output_tokens']==10
    assert result['cache_hit_tokens'] is None and result['cache_miss_tokens'] is None
    assert result['cache_reporting']=='missing_input'


@pytest.mark.parametrize('changes',[
    {'prompt_tokens':99},
    {'cache_hit_tokens':101},
    {'cache_miss_tokens':35},
    {'prompt_cache_hit_tokens':63},
    {'prompt_tokens_details':{'cached_tokens':63}},
    {'input_tokens_details':{'cached_tokens':False}},
    {'prompt_tokens_details':True},
])
def test_conflicting_aliases_or_details_do_not_produce_a_cache_discount(changes):
    usage={'input_tokens':100,'output_tokens':10,'cache_hit_tokens':64,'cache_miss_tokens':36,**changes}
    result=adapter.normalize_usage(usage)
    assert result['cache_hit_tokens'] is None and result['cache_miss_tokens'] is None
    assert result['cache_reporting'] in ('conflict','invalid','conflict_input')
    assert adapter.normalize_usage(result)==result


def test_unknown_cache_stays_unknown_but_explicit_zero_is_known():
    unknown=adapter.normalize_usage({'input_tokens':100,'output_tokens':10})
    assert unknown['cache_hit_tokens'] is None and unknown['cache_miss_tokens'] is None
    assert unknown['cache_reporting']=='missing'
    known=adapter.normalize_usage({'input_tokens':100,'output_tokens':10,'cache_hit_tokens':0})
    assert known['cache_hit_tokens']==0 and known['cache_miss_tokens']==100
    assert adapter.normalize_usage(None)['raw'] is None
    assert adapter.normalize_usage(vendor_usage('chat'),'other')['cache_reporting']=='unsupported_protocol'


def invoke(kind,cfg):
    endpoint=pool_endpoint('usage-endpoint','Usage fixture',cfg['base_url'],
        model=cfg['model'],protocol=cfg['protocol'],max_attempts=1,max_output_tokens=1024,
        input_price=2.,output_price=4.)
    if kind=='plain':return provider.generate('Preserve system.','Preserve original.')
    if kind=='pool':return provider._pool_generate_once('Preserve system.','Preserve original.',None,endpoint)[0]
    if kind=='stream':return provider_stream.generate('Preserve system.','Preserve original.')
    if kind=='stream_pool':return provider_stream._stream_generate_once('Preserve system.','Preserve original.',lambda text:None,endpoint)[0]
    frame={'messages':[{'role':'user','content':'Preserve original.'}],'tools':[]}
    return provider_adapter.transport('Preserve system.',json.dumps(frame),cfg)


def response(wire,usage,partial=False):
    body=json.loads(wire.content)
    if body.get('stream') and wire.url.path.endswith('/responses'):
        events=[{'type':'response.output_text.delta','delta':'{"ok":true}'},
                {'type':'response.completed','response':{'id':'usage-fixture','usage':usage}}]
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
            content=''.join('data: '+json.dumps(event)+'\n\n' for event in events).encode())
    if body.get('stream'):
        data={'id':'usage-fixture','choices':[{'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}],'usage':usage}
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
            content=('data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n').encode())
    if wire.url.path.endswith('/responses'):
        return httpx.Response(200,json={'id':'usage-fixture','status':'incomplete' if partial else 'completed',
            'output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}],'usage':usage})
    return httpx.Response(200,json={'id':'usage-fixture',
        'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'length' if partial else 'stop'}],'usage':usage})


@pytest.mark.parametrize('kind',['plain','pool','stream','stream_pool','agent'])
@pytest.mark.parametrize('protocol',['openai','responses'])
@pytest.mark.parametrize('shape',['deepseek','chat','responses'])
def test_every_transport_returns_the_same_cache_contract_and_actual_destination(client,monkeypatch,kind,protocol,shape):
    raw=vendor_usage(shape);sent=[]
    cfg={**provider.DEFAULT,'base_url':'https://usage-fixture.example/v1','model':'deepseek-usage',
         'protocol':protocol,'input_price':2.,'output_price':4.,'max_output_tokens':1024,'max_attempts':1}
    security.save_key('fake-usage-key')
    def handle(wire):sent.append(wire);return response(wire,raw)
    monkeypatch.setattr(provider,'client',lambda value:httpx.Client(transport=httpx.MockTransport(handle)))
    with provider.request_scope(cfg,'fake-usage-key',route='deepseek'):
        result=invoke(kind,cfg)
    assert len(sent)==1
    assert result['usage']==adapter.normalize_usage(raw,protocol)
    assert result['provider_snapshot']=={name:cfg[name] for name in ('base_url','protocol','model')}
    assert result['actual_provider_credential_hash']==adapter.credential_hash('fake-usage-key')
    assert result['cost']==pytest.approx(0.00024)


@pytest.mark.parametrize('kind',['plain','pool','agent'])
@pytest.mark.parametrize('protocol',['openai','responses'])
def test_partial_completed_responses_keep_normalized_usage_and_destination(client,monkeypatch,kind,protocol):
    cfg={**provider.DEFAULT,'base_url':'https://usage-fixture.example/v1','model':'deepseek-usage',
         'protocol':protocol,'input_price':2.,'output_price':4.,'max_output_tokens':1024,'max_attempts':1}
    security.save_key('fake-usage-key')
    raw=vendor_usage('responses')
    monkeypatch.setattr(provider,'client',lambda value:httpx.Client(transport=httpx.MockTransport(lambda wire:response(wire,raw,partial=True))))
    with provider.request_scope(cfg,'fake-usage-key',route='deepseek'):
        with pytest.raises(provider.ProviderError) as captured:invoke(kind,cfg)
    assert captured.value.response_result['usage']==adapter.normalize_usage(raw,protocol)
    assert captured.value.response_result['provider_snapshot']['base_url']==cfg['base_url']


def pricing_cfg():
    cfg={**provider.DEFAULT,'base_url':'https://usage-fixture.example/v1','model':'deepseek-usage',
         'input_price':2.,'output_price':4.,'currency':'USD'}
    cfg['cache_pricing_evidence']={'verified':True,'version':'fixture-price-v1','unit':'per_million_tokens',
        'base_url':cfg['base_url'],'model':cfg['model'],'currency':'USD',
        'credential_hash':adapter.credential_hash('fake-usage-key'),
        'input_hit_price':0.2,'input_miss_price':2.,'output_price':4.}
    return cfg


@pytest.mark.parametrize('changes',[{'verified':False},{'version':''},{'unit':'per_token'},
    {'base_url':'https://other.example/v1'},{'model':'other'},{'currency':'CNY'},
    {'credential_hash':'another-account'},{'input_hit_price':False},{'input_miss_price':-1},
    {'output_price':float('inf')}])
def test_cache_split_pricing_requires_explicit_valid_matching_evidence(changes):
    cfg=pricing_cfg();cfg['cache_pricing_evidence'].update(changes)
    with provider.request_scope(cfg,'fake-usage-key'):
        assert provider.usage_cost(cfg,vendor_usage('deepseek'))==pytest.approx(0.00024)


def test_valid_cache_split_pricing_is_an_estimate_bound_to_current_credential():
    cfg=pricing_cfg()
    with provider.request_scope(cfg,'fake-usage-key'):
        assert provider.usage_cost(cfg,vendor_usage('deepseek'))==pytest.approx(0.0001248)
        assert provider.usage_cost({**cfg,'input_price':None,'output_price':None},vendor_usage('deepseek'))==pytest.approx(0.0001248)
        assert provider.usage_cost(cfg,{'input_tokens':100,'output_tokens':10})==pytest.approx(0.00024)
    assert provider.usage_cost(cfg,vendor_usage('deepseek'))==pytest.approx(0.00024)


def test_unrepresentable_rate_estimate_remains_unknown():
    cfg=pricing_cfg()
    usage={'input_tokens':10**400,'output_tokens':10,'cache_hit_tokens':0,'cache_miss_tokens':10**400}
    with provider.request_scope(cfg,'fake-usage-key'):
        assert provider.usage_cost(cfg,usage) is None


@pytest.mark.parametrize('shape',['deepseek','chat','responses'])
def test_billing_matches_reported_cache_exclusion_without_inventing_a_missing_cache(shape):
    before={'unit':'USD','model':'fixture','requests':1,'input_tokens':100,'output_tokens':10,'amounts':{'cost':'0.01'}}
    after={**before,'requests':2,'input_tokens':136,'output_tokens':20,'amounts':{'cost':'0.02'}}
    proof=billing.reconcile(before,after,vendor_usage(shape))
    assert proof['usage_match']['cached_tokens_if_reported']==64
    assert proof['usage_match']['input_counting']=='cache_excluded'
    assert billing.reconcile(before,after,{'input_tokens':100,'output_tokens':10}) is None


def test_combining_usage_does_not_replace_unknown_cache_with_zero():
    combined=billing._combined_usage([vendor_usage('responses'),{'input_tokens':100,'output_tokens':10}])
    assert combined['input_tokens']==200 and combined['output_tokens']==20
    assert combined['cache_hit_tokens'] is None and combined['cache_miss_tokens'] is None
    known=billing._combined_usage([vendor_usage('responses'),vendor_usage('chat')])
    assert known['cache_hit_tokens']==128 and known['cache_miss_tokens']==72


def test_rule_receipt_preserves_known_cached_counts_and_marks_missing_usage(client):
    pid=project(client);task=tasks.enqueue(pid,'rules_ai',{})
    for usage in (vendor_usage('responses'),{'input_tokens':100,'output_tokens':10}):
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (db.uid(),task['id'],pid,'completed','fixture','responses','{}',db.dumps(usage),db.now()))
    receipt=rule_review_receipts.receipt(task)
    assert receipt['input_tokens']==200 and receipt['output_tokens']==20
    assert receipt['cached_input_tokens'] is None and receipt['known_cached_input_tokens']==64
    assert receipt['cache_usage_complete'] is False and receipt['cache_known_calls']==1


def test_cache_v2_agent_layout_preserves_aliases_and_the_native_transport_frame(client,monkeypatch):
    from app import prompt_layout
    from app.writing_request import expand_model_context
    from test_agent_gpt_failover import configure, success
    pid,profile,_=configure(client)
    task=tasks.enqueue(pid,'provider_probe',{'prompt_layout_version':'cache_v2'})
    original='该材料仅说明静态设计，不证明系统已部署。'
    payload={'instruction':'只核对给定文献，不推断实现。',
             'references':[{'id':'original-bibliography','title':'完整文献'}],
             'sources':[{'id':'source-one','text':original}],
             'rules':[{'id':'rule-one','statement':'保留条件与否定。'}]}
    before=deepcopy(payload);seen=[]
    def handle(wire):
        body=json.loads(wire.content);seen.append(body)
        assert len(body['messages'])==2 and body['messages'][0]['role']=='system'
        assert prompt_layout.SYSTEM_NOTE in body['messages'][0]['content']
        assert provider_adapter.ALIAS_NOTE in body['messages'][0]['content']
        encoded=json.loads(body['messages'][1]['content'])
        actual=expand_model_context(encoded)
        assert list(actual).index('sources')<list(actual).index('instruction')
        assert actual['sources'][0]['text']==original
        assert actual['references'][0]['id']=='B1'
        assert actual['rules'][0]['id']=='L1'
        assert body['stream'] is False and 'tools' not in body
        return success('openai','{"reference_ids":["B1"],"rule_ids":["L1"]}')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    result=provider_adapter.call(task,profile,'Fixed agent instructions.',payload,{'stage':'draft','annotation_batch':3})
    assert json.loads(result['text'])=={'reference_ids':['original-bibliography'],'rule_ids':['rule-one']}
    assert payload==before and len(seen)==1
    meta=db.loads(db.one("SELECT manifest FROM generation_runs WHERE task_id=? AND status='completed'",(task['id'],))['manifest'])
    assert meta['prompt_layout_version']=='cache_v2' and meta['prompt_stage']=='draft'
    assert meta['prompt_stage_index']==3 and meta['stable_block_bytes']>0
    assert meta['identifier_aliases']=={'L1':'rule-one','B1':'original-bibliography'}


def test_cache_v2_agent_json_repairs_keep_system_and_material_prefix_fixed(client,monkeypatch):
    from app import prompt_layout
    from test_agent_gpt_failover import configure, success
    _,profile,_=configure(client)
    task=tasks.enqueue(profile['project_id'],'provider_probe',{'prompt_layout_version':'cache_v2'})
    systems=[];payloads=[]
    original={'references':[{'id':'original-reference'}],
              'rules':[{'id':'original-rule','statement':'保留原文条件。'}],
              'sources':[{'id':'source','text':'未在生产环境执行；仅是原始观察。'}],
              'instruction':'依据给定材料返回JSON。'}
    def handle(wire):
        body=json.loads(wire.content)
        systems.append(body['messages'][0]['content'])
        payloads.append(json.loads(body['messages'][1]['content']))
        return success('openai','{"reference_ids":["B1"]}' if len(systems)==3 else '{invalid JSON')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    result=provider_adapter.call(task,profile,'Fixed JSON instructions.',original,{'stage':'draft'})
    assert json.loads(result['text'])=={'reference_ids':['original-reference']}
    assert len(systems)==3 and len(set(systems))==1
    assert provider_adapter.JSON_REPAIR_NOTE in systems[0]
    stable=lambda payload:{key:payload[key] for key in prompt_layout.STABLE_FIELDS if key in payload}
    assert stable(payloads[0])==stable(payloads[1])==stable(payloads[2])
    assert prompt_layout.PARAMETERS not in payloads[0]
    assert payloads[1][prompt_layout.PARAMETERS]['structured_json_attempt']==2
    assert payloads[2][prompt_layout.PARAMETERS]['structured_json_attempt']==3
    assert list(payloads[1])[-1]==list(payloads[2])[-1]==prompt_layout.PARAMETERS
    assert all('prior_json_error' not in system for system in systems)


@pytest.mark.parametrize('raw',[None,{},'unreported usage'])
def test_missing_or_malformed_usage_does_not_discard_a_completed_agent_response(client,monkeypatch,raw):
    from test_agent_gpt_failover import configure
    _,profile,task=configure(client)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(lambda wire:response(wire,raw))))
    result=provider_adapter.call(task,profile,'Return JSON.',{'question':'Use original context.'},{})
    assert json.loads(result['text'])=={'ok':True}
    row=db.one('SELECT status,usage,cost FROM generation_runs WHERE task_id=?',(task['id'],))
    normalized=db.loads(row['usage'])
    assert row['status']=='completed' and row['cost'] is None
    assert normalized['input_tokens'] is None and normalized['output_tokens'] is None
    assert normalized['cache_hit_tokens'] is None and normalized['cache_miss_tokens'] is None
    assert normalized['raw']==raw
