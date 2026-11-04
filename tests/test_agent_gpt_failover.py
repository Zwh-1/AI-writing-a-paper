"""Agent frames keep their contract and attribution during safe provider failover."""
import json

import httpx
import pytest

from test_workbench import client, project
from app import db, model_routing, provider, security, tasks
from app.summary_billing import key_hash
from app.writing_agent import billing, profiles, provider_adapter


def configure(client, protocol='openai', reconcile=False):
    pid=project(client)
    primary={**provider.DEFAULT,'base_url':'https://agent-primary.example/v1',
             'model':'deepseek-agent','protocol':protocol,'max_attempts':5,
             'max_output_tokens':1024,'context_limit':32000,'input_price':0.,'output_price':0.}
    backup={**model_routing.DEFAULT,'base_url':'https://agent-backup.example/v1',
            'model':'gpt-agent','protocol':protocol,'max_attempts':5,
            'max_output_tokens':2048,'context_limit':40000,'input_price':0.,'output_price':0.}
    db.set_setting('provider',primary)
    db.set_setting('gpt_fallback_provider',backup)
    security.save_key('fake-agent-primary-key')
    security.save_key('fake-agent-backup-key',model_routing.PROFILE)
    profile=profiles.create(pid,{'role':'draft','configuration':{
        'max_output_tokens':512,'reconcile_billing':reconcile}})
    task=tasks.enqueue(pid,'provider_probe',{})
    return pid,profile,task


def success(protocol,text='{"ok":true}',tool=False):
    usage={'input_tokens':13,'output_tokens':7} if protocol=='responses' else {'prompt_tokens':13,'completion_tokens':7}
    if protocol=='responses':
        output=([{'type':'function_call','call_id':'next-tool','name':'read_document','arguments':'{"id":"doc-2"}'}]
                if tool else [{'type':'message','content':[{'type':'output_text','text':text}]}])
        return httpx.Response(200,json={'id':'agent-response','model':'gpt-agent',
                                      'status':'completed','output':output,'usage':usage})
    message=({'content':None,'tool_calls':[{'id':'next-tool','type':'function',
              'function':{'name':'read_document','arguments':'{"id":"doc-2"}'}}]}
             if tool else {'content':text})
    return httpx.Response(200,json={'id':'agent-response','model':'gpt-agent',
        'choices':[{'message':message,'finish_reason':'tool_calls' if tool else 'stop'}],'usage':usage})


@pytest.mark.parametrize('protocol',['openai','responses'])
def test_five_safe_agent_failures_keep_full_tool_frame_and_actual_capabilities(client,monkeypatch,protocol):
    pid,profile,task=configure(client,protocol)
    tools=[{'type':'function','function':{'name':'read_document','description':'Read the selected original.',
            'parameters':{'type':'object','properties':{'id':{'type':'string'}},'required':['id']}}}]
    messages=[{'role':'user','content':'Use all original evidence and preserve the tool history.'},
              {'role':'assistant','content':None,'tool_calls':[{'id':'prior-tool','type':'function',
                'function':{'name':'read_document','arguments':'{"id":"doc-1"}'}}]},
              {'role':'tool','tool_call_id':'prior-tool','content':'Complete original passage: constraints and results.'}]
    items=[{'type':'message','role':'user','content':[{'type':'input_text','text':messages[0]['content']}]},
           {'type':'function_call','call_id':'prior-tool','name':'read_document','arguments':'{"id":"doc-1"}'},
           {'type':'function_call_output','call_id':'prior-tool','output':messages[2]['content']}]
    seen=[]
    def handle(request):
        seen.append((request.url.host,json.loads(request.content),request.headers['Authorization']))
        if request.url.host=='agent-primary.example':
            return httpx.Response(429,json={'error':'rate limited'},headers={'retry-after':'0'})
        assert request.url.host=='agent-backup.example'
        return success(protocol,tool=True)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    result=provider_adapter.call(task,profile,'Preserve the complete agent frame.',{},
                                 {'stage':'tool_round'},tools,messages,items)
    assert [host for host,_,_ in seen]==['agent-primary.example']*5+['agent-backup.example']
    assert all(credential=='Bearer fake-agent-primary-key' for _,_,credential in seen[:5])
    assert seen[-1][2]=='Bearer fake-agent-backup-key'
    for _,body,_ in seen:
        assert body.get('max_output_tokens',body.get('max_tokens',body.get('max_completion_tokens')))==512
        if protocol=='responses':
            assert body['instructions']=='Preserve the complete agent frame.'
            assert body['input']==items
            assert body['tools']==[{'type':'function',**tools[0]['function']}]
        else:
            assert body['messages']==[{'role':'system','content':'Preserve the complete agent frame.'},*messages]
            assert body['tools']==tools
    assert result['agent_transport'] and result['tool_calls'][0]['id']=='next-tool'
    rows=db.rows('SELECT status,manifest FROM generation_runs WHERE task_id=? ORDER BY rowid',(task['id'],))
    assert [row['status'] for row in rows]==['rejected']*5+['completed']
    meta=db.loads(rows[-1]['manifest'])
    assert meta['provider']['model']=='gpt-agent' and meta['provider']['agent_transport']
    assert meta['credential_hash']==key_hash('fake-agent-backup-key')
    assert meta['agent_requested_profile_id']==profile['id']
    observed=profiles.get(pid,meta['agent_observation_profile_id'])
    assert observed['configuration']['route']=='gpt'
    assert observed['capabilities']['tools']['status']=='supported'
    assert profiles.get(pid,profile['id'])['capabilities']['text_generation']=='unknown'
    assert provider.attempts_used()==0
    following_messages=[*messages,{'role':'user','content':'Continue only with the next original passage.'}]
    following_items=[*items,{'type':'message','role':'user','content':[{'type':'input_text','text':following_messages[-1]['content']}]}]
    provider_adapter.call(task,profile,'Preserve the complete agent frame.',{},
                          {'stage':'tool_round'},tools,following_messages,following_items)
    assert [host for host,_,_ in seen]==['agent-primary.example']*5+['agent-backup.example']*2
    assert provider.attempts_used()==0
    assert db.one('SELECT COUNT(*) AS count FROM writing_profiles WHERE project_id=?',(pid,))['count']==2


def test_agent_aliases_and_completed_frame_are_reused_after_failover(client,monkeypatch):
    _,profile,task=configure(client)
    seen=[]
    def handle(request):
        seen.append(request)
        if request.url.host=='agent-primary.example':
            return httpx.Response(429,json={'error':'rate limited'},headers={'retry-after':'0'})
        user=json.loads(request.content)['messages'][1]['content']
        assert json.loads(user)['references'][0]['id']=='B1'
        return success('openai','{"reference_ids":["B1"]}')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    args=(task,profile,'Return only JSON.',{'references':[{'id':'original-reference-id'}]},{'stage':'draft'})
    result=provider_adapter.call(*args)
    assert json.loads(result['text'])=={'reference_ids':['original-reference-id']}
    assert result['identifier_aliases']=={'B1':'original-reference-id'}
    assert len(seen)==6
    cached=provider_adapter.call(*args)
    assert cached['text']==result['text'] and len(seen)==6
    assert len(db.rows('SELECT id FROM generation_runs WHERE task_id=?',(task['id'],)))==6


def test_agent_does_not_use_primary_billing_baseline_for_a_gpt_response(client,monkeypatch):
    _,profile,task=configure(client,reconcile=True)
    samples=[]
    def sample(cfg):
        credential=provider._REQUEST.get()['key']
        samples.append((cfg['model'],credential))
        return {'model':cfg['model'],'base_url':cfg['base_url'],'credential_hash':key_hash(credential),
                'unit':'USD','requests':1,'input_tokens':13,'output_tokens':7,
                'amounts':{'cost':'0.01'},'remaining':'1.00'}
    monkeypatch.setattr(billing,'sample',sample)
    def handle(request):
        if request.url.host=='agent-primary.example':
            return httpx.Response(429,json={'error':'rate limited'},headers={'retry-after':'0'})
        return success('openai')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    provider_adapter.call(task,profile,'Return JSON.',{'question':'Use the supplied original.'},{})
    assert samples==[('deepseek-agent','fake-agent-primary-key'),('gpt-agent','fake-agent-backup-key')]
    meta=db.loads(db.one("SELECT manifest FROM generation_runs WHERE task_id=? AND status='completed'",(task['id'],))['manifest'])
    interval=meta['billing_interval_observation']
    assert interval['before'] is None and interval['after']['model']=='gpt-agent'
    assert interval['matched'] is False and 'billing_interval_proof' not in meta


def test_unknown_agent_execution_never_sends_gpt_or_replays_the_frame(client,monkeypatch):
    _,profile,task=configure(client)
    seen=[]
    def handle(request):
        seen.append(request.url.host)
        raise httpx.ReadTimeout('unknown execution',request=request)
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    args=(task,profile,'Return JSON.',{'question':'Use the supplied original.'},{})
    with pytest.raises(provider.ProviderError) as captured:provider_adapter.call(*args)
    assert captured.value.unknown and seen==['agent-primary.example']
    with pytest.raises(provider.ProviderError) as captured:provider_adapter.call(*args)
    assert captured.value.unknown and seen==['agent-primary.example']
    assert db.one('SELECT status FROM generation_runs WHERE task_id=?',(task['id'],))['status']=='outcome_unknown'


def test_an_explicit_gpt_agent_profile_keeps_its_own_route_and_frame(client,monkeypatch):
    pid,_,task=configure(client)
    profile=profiles.create(pid,{'role':'draft','configuration':{'route':'gpt','max_output_tokens':512}})
    seen=[]
    def handle(request):
        seen.append(request)
        assert request.url.host=='agent-backup.example'
        assert request.headers['Authorization']=='Bearer fake-agent-backup-key'
        assert json.loads(request.content)['messages'][1]['content']=='{"question":"Use the original."}'
        return success('openai')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    result=provider_adapter.call(task,profile,'Return JSON.',{'question':'Use the original.'},{})
    assert result['agent_transport'] and len(seen)==1
    assert profiles.get(pid,profile['id'])['capabilities']['text_generation']['status']=='supported'
