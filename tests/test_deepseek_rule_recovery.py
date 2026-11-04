"""DeepSeek remains primary across bounded batch recovery; no paid calls."""
from collections import Counter
import json
import httpx
import pytest
from test_workbench import client,project,file
from test_rule_review_integrity import candidate,submit,choice,fake_response,gpt_ready
from test_rule_ai_batches import fixture as extraction_fixture,response as extraction_response
from app import db,provider,tasks,security,summary_billing


def many_candidates(client,count):
    pid=project(client);f,s,first=candidate(client,pid)
    for index in range(1,count):
        db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),pid,s['id'],'论文应交代采用的研究方法，条目'+chr(0x4e00+index)+'。','pending',db.now()))
    return pid,f,s


@pytest.mark.parametrize('failure',['invalid_json','truncated'])
def test_deepseek_recovers_77_rules_without_stopping_after_two_bad_batches(client,monkeypatch,failure):
    pid,f,s=many_candidates(client,77);seen=[]
    db.set_setting('provider',{'thinking_enabled':True})
    def fake(task,system,user,manifest,**kw):
        ids=[r['rule_id'] for r in db.loads(user)['candidates']]
        seen.append((ids,db.loads(task['payload']).get('provider_profile')))
        assert provider.config()['thinking_enabled'] is False
        if not manifest['recovery_attempt']:
            if failure=='truncated':raise provider.ProviderError('模型输出被截断',response_result={'text':'{"decisions":[','usage':{'output_tokens':128}})
            return {'text':'{"decisions":['}
        assert len(ids)<=2
        return fake_response([choice(rid,s['text']) for rid in ids])
    monkeypatch.setattr(tasks,'model_call',fake)
    result=submit(client,pid,provider_profile='primary')['result']['review']
    assert result['coverage_complete'] and result['semantic_checked']==result['semantic_total']==77
    assert not result['verification_errors'] and not result['pause_reason']
    assert result['recovery_requests']>2 and all(profile=='primary' for _,profile in seen)
    assert max(Counter(rid for ids,_ in seen for rid in ids).values())==2
    assert db.setting('provider')['thinking_enabled'] is True


def test_bad_rule_cannot_stop_independent_following_batches(client,monkeypatch):
    pid,f,s=many_candidates(client,13);bad=db.rows('SELECT id FROM rules ORDER BY created_at,id')[0]['id'];seen=[]
    def fake(task,system,user,manifest,**kw):
        ids=[r['rule_id'] for r in db.loads(user)['candidates']];seen.append(ids)
        if bad in ids:return {'text':'not JSON'}
        return fake_response([choice(rid,s['text']) for rid in ids])
    monkeypatch.setattr(tasks,'model_call',fake);task=submit(client,pid,provider_profile='primary');result=task['result']['review']
    assert task['status']=='incomplete' and not result['pause_reason']
    assert result['semantic_checked']>=11 and result['unreviewed_count']<=2
    assert max(Counter(rid for ids in seen for rid in ids).values())==2
    assert db.loads(db.one('SELECT result FROM tasks WHERE id=?',(task['id'],))['result'])['review']['semantic_checked']==result['semantic_checked']


def test_reasoning_exhaustion_has_specific_pause_instead_of_gpt_switch(client,monkeypatch):
    pid,f,s=many_candidates(client,12);gpt_ready();seen=[]
    def fake(task,*args,**kw):
        seen.append(db.loads(task['payload'])['provider_profile'])
        usage={'output_tokens':8192,'raw':{'completion_tokens_details':{'reasoning_tokens':8192}}}
        raise provider.ProviderError('模型输出被截断',response_result={'text':'','usage':usage})
    monkeypatch.setattr(tasks,'model_call',fake);task=submit(client,pid,provider_profile='primary',allow_gpt=True,gpt_budget=3);result=task['result']['review']
    assert task['status']=='incomplete' and seen==['primary','primary']
    assert result['failure_counts']['reasoning_exhausted']==2 and '推理' in result['pause_reason']
    assert result['semantic_checked']==0 and result['unreviewed_count']==12


def test_cached_unknown_failures_do_not_block_new_independent_rules(client,monkeypatch):
    pid,f,s=many_candidates(client,12);seen=[]
    def unknown(task,system,user,manifest,**kw):
        seen.extend(r['rule_id'] for r in db.loads(user)['candidates'])
        raise provider.ProviderError('timeout',unknown=True)
    monkeypatch.setattr(tasks,'model_call',unknown)
    first=submit(client,pid,provider_profile='primary');assert first['status']=='incomplete'
    assert len(seen)==8 and len(set(seen))==8
    new=[]
    def good(task,system,user,manifest,**kw):
        ids=[r['rule_id'] for r in db.loads(user)['candidates']];new.extend(ids)
        return fake_response([choice(rid,s['text']) for rid in ids])
    monkeypatch.setattr(tasks,'model_call',good);second=submit(client,pid,provider_profile='primary')['result']['review']
    assert not set(new)&set(seen) and len(new)==4
    assert second['semantic_checked']==4 and second['unreviewed_count']==8 and not second['pause_reason']


def transport_setup(client,monkeypatch,limit=5):
    pid=project(client);f,s,rid=candidate(client,pid)
    db.set_setting('provider',{**provider.DEFAULT,'base_url':'https://deepseek.example/v1','model':'deepseek-test','thinking_enabled':True,'max_attempts':1,'max_request_cost':2})
    security.save_key('fake-deepseek-recovery-key')
    monkeypatch.setattr(summary_billing,'reconcile',lambda *a,**kw:{'reconciled':False})
    return pid,s,rid


def test_review_waits_and_retries_explicit_429_on_deepseek(client,monkeypatch):
    pid,s,rid=transport_setup(client,monkeypatch);seen=[]
    db.set_setting('provider',{**provider.config(),'max_attempts':5})
    def handle(request):
        data=json.loads(request.content);seen.append(data)
        assert request.url.host=='deepseek.example' and data['thinking']=={'type':'disabled'}
        if len(seen)<3:return httpx.Response(429,json={'error':'rate limit'},headers={'retry-after':'0'})
        return httpx.Response(200,json={'choices':[{'message':{'content':fake_response([choice(rid,s['text'])])['text']},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':30}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    task=submit(client,pid,provider_profile='primary')
    assert task['status']=='succeeded' and len(seen)==3
    assert [r['status'] for r in db.rows('SELECT status FROM generation_runs ORDER BY created_at')]==['rejected','rejected','completed']


def test_known_output_recovery_cannot_exceed_original_job_budget(client,monkeypatch):
    pid,s,rid=transport_setup(client,monkeypatch);seen=[]
    def handle(request):
        seen.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"decisions":['},'finish_reason':'length'}],'usage':{'prompt_tokens':10,'completion_tokens':128}})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    task=submit(client,pid,provider_profile='primary',budget_limit=2)
    assert task['status']=='budget_blocked' and len(seen)==1
    assert task['result']['review']['budget_blocked'] and db.one('SELECT COUNT(*) n FROM generation_runs')['n']==1


def test_automatic_extraction_recovers_bad_deepseek_batches_without_gpt(client,monkeypatch):
    pid,f=extraction_fixture(client,13);seen=[]
    def fake(task,system,user,manifest,**kw):
        ids=[r['source_id'] for r in db.loads(user)['sources']];seen.append((ids,db.loads(task['payload'])['provider_profile']))
        assert provider.config()['thinking_enabled'] is False
        if not manifest['recovery_attempt']:return {'text':'{"rules":['}
        return extraction_response(user)
    monkeypatch.setattr(tasks,'model_call',fake)
    queued=client.post(f'/api/v1/projects/{pid}/rules/ai-extract',json={'file_ids':[f['id']],'automatic':True,'provider_profile':'primary','budget_limit':5,'semantic_review':False}).json()
    assert tasks.run_one();task=client.get('/api/v1/tasks/'+queued['id']).json()
    assert task['status']=='succeeded' and task['result']['processed_source_units']==13
    assert task['result']['recovery_requests']>2 and not task['result']['pause_reason']
    assert all(profile=='primary' for _,profile in seen) and max(Counter(rid for ids,_ in seen for rid in ids).values())==2


def test_oversized_source_does_not_block_independent_review(client,monkeypatch):
    pid,f,s=many_candidates(client,1)
    long=file(client,pid,'知识背景。'*2000+'摘要必须说明研究方法。',role='rules')
    source=db.one('SELECT * FROM sources WHERE file_id=?',(long['id'],));rid=db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(rid,pid,source['id'],'摘要应交代研究采用的方法。','pending',db.now()))
    db.set_setting('provider',{'context_limit':6000});seen=[]
    def fake(task,system,user,manifest,**kw):
        ids=[r['rule_id'] for r in db.loads(user)['candidates']];seen.extend(ids)
        return fake_response([choice(item,s['text']) for item in ids])
    monkeypatch.setattr(tasks,'model_call',fake);task=submit(client,pid,provider_profile='primary');result=task['result']['review']
    assert task['status']=='incomplete' and result['semantic_checked']==1 and result['unreviewed_count']==1
    assert rid not in seen and result['failure_counts']=={'context_limit':1} and not result['pause_reason']


def test_automatic_permission_pause_is_not_requeued_by_worker(client,monkeypatch):
    pid,f=extraction_fixture(client,9);seen=[]
    def fake(*args,**kw):
        seen.append(1);raise provider.ProviderError('HTTP 401: invalid key',fallback_allowed=True)
    monkeypatch.setattr(tasks,'model_call',fake)
    queued=client.post(f'/api/v1/projects/{pid}/rules/ai-extract',json={'file_ids':[f['id']],'automatic':True,'provider_profile':'primary','budget_limit':5}).json()
    assert tasks.run_one();task=client.get('/api/v1/tasks/'+queued['id']).json()
    assert task['status']=='incomplete' and task['result']['pause_reason'] and seen==[1]
    assert not tasks.run_one()
