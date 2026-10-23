"""Document-sized extraction, durable continuation and known invalid outputs."""
import pytest
from test_workbench import client,project,file
from app import db,rule_ai,tasks,provider,security,model_routing


def fixture(client,count=9):
    pid=project(client);f=file(client,pid,'论文必须给出真实依据。',role='rules')
    first=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    for i in range(1,count):
        text=f'第{i}条：必须记录真实测试结果。'
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),f['id'],first['page_id'],db.dumps({'kind':'paragraph','page':1}),text,text,'rule_candidate','native',0))
    return pid,f


def response(user,model='deepseek-test'):
    items=db.loads(user)['sources']
    return {'text':db.dumps({'rules':[{'source_id':s['source_id'],'quote':s['text'],'statement':s['text'],'modality':'mandatory','scope':'global','category':'content'} for s in items]}),'provider_snapshot':{'model':model}}


def enqueue(client,pid,f):
    r=client.post(f'/api/v1/projects/{pid}/rules/ai-extract',json={'file_ids':[f['id']],'source_limit':40});assert r.status_code==200,r.text
    return r.json()


def test_partial_failure_keeps_completed_batches_and_retry_only_sends_remaining(client,monkeypatch):
    pid,f=fixture(client);seen=[]
    def fake(task,system,user,manifest,**kwargs):
        seen.append(manifest['batch'])
        if manifest['batch']==1:raise provider.ProviderError('upstream unknown',unknown=True)
        return response(user)
    monkeypatch.setattr(tasks,'model_call',fake)
    t=enqueue(client,pid,f);assert tasks.run_one()
    failed=client.get('/api/v1/tasks/'+t['id']).json()
    assert failed['status']=='outcome_unknown' and failed['result']['completed_batches']==1
    assert failed['result']['processed_source_units']==4 and len(db.rows('SELECT * FROM rules'))==4
    assert seen==[0,1]
    monkeypatch.setattr(tasks,'model_call',lambda task,system,user,manifest,**kwargs:(seen.append(manifest['batch']) or response(user)))
    r=client.post('/api/v1/tasks/'+t['id']+'/retry',json={'acknowledge_possible_duplicate_charge':True});assert r.status_code==200
    assert tasks.run_one()
    completed=client.get('/api/v1/tasks/'+r.json()['id']).json()
    assert completed['status']=='succeeded',completed['error']
    assert completed['result']['processed_source_units']==9 and completed['result']['reused_batches']==1
    assert completed['result']['added_this_attempt']==5 and completed['result']['added']==9
    assert seen==[0,1,1,2] and len(db.rows('SELECT * FROM rules'))==9


def test_missing_rules_field_is_an_error_not_false_success(client,monkeypatch):
    pid,f=fixture(client,1);monkeypatch.setattr(tasks,'model_call',lambda *args,**kwargs:{'text':'{"ok":true}'})
    t=enqueue(client,pid,f);assert tasks.run_one()
    assert client.get('/api/v1/tasks/'+t['id']).json()['status']=='failed'
    assert not db.rows('SELECT * FROM rules')


def test_reasoning_exhaustion_repairs_each_batch_without_sticky_gpt(client,monkeypatch):
    pid,f=fixture(client);security.save_key('fake-gpt-key','gpt-fallback')
    db.set_setting('gpt_fallback_provider',{**model_routing.DEFAULT,'model':'gpt-6.1-sol'})
    calls=[]
    def fake(task,system,user,manifest,**kwargs):
        gpt=db.loads(task['payload']).get('provider_profile')=='gpt';calls.append((manifest['batch'],gpt))
        if not gpt:raise provider.ProviderError('output truncated',response_result={'text':'','usage':{'output_tokens':8192}})
        return response(user,'gpt-6.1-sol')
    monkeypatch.setattr(tasks,'model_call',fake)
    t=enqueue(client,pid,f);assert tasks.run_one()
    saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['status']=='succeeded',saved['error']
    assert calls==[(0,False),(0,True),(1,False),(1,True),(2,False),(2,True)]
    assert saved['result']['models_used']==['gpt-6.1-sol'] and saved['result']['processed_source_units']==9


def test_unknown_response_never_triggers_rule_specific_gpt_repair(client,monkeypatch):
    pid,f=fixture(client,1);security.save_key('fake-gpt-key','gpt-fallback')
    db.set_setting('gpt_fallback_provider',{**model_routing.DEFAULT,'model':'gpt-6.1-sol'});seen=[]
    def fake(*args,**kwargs):seen.append(1);raise provider.ProviderError('timeout',unknown=True)
    monkeypatch.setattr(tasks,'model_call',fake);t=enqueue(client,pid,f);tasks.run_one()
    assert len(seen)==1 and client.get('/api/v1/tasks/'+t['id']).json()['status']=='outcome_unknown'


def test_pdf_whitespace_alignment_returns_exact_source_and_keeps_negation():
    text='摘要必须说明研究\n方法，不能编造数据。'
    assert rule_ai.exact_quote(text,'摘要必须说明研究方法')=='摘要必须说明研究\n方法'
    assert rule_ai.exact_quote(text,'可以编造数据') is None
    assert rule_ai.exact_quote(text,'不能编造数据')=='不能编造数据'


def test_batches_bound_chars_and_preserve_repeated_long_source_parts():
    sources=[{'id':str(i),'text':'甲'*1000,'locator':'{}'} for i in range(36)]
    chunks=rule_ai.batches(sources)
    assert sum(map(len,chunks))==36 and all(len(c)<=4 and sum(len(s['text']) for s in c)<=3500 for c in chunks)
    long=rule_ai.batches([{'id':'long','text':'甲'*6000,'locator':'{}'}])
    assert len({rule_ai.batch_key(c) for c in long})==len(long)==2


def test_task_progress_never_moves_backwards(client):
    pid=project(client);t=tasks.enqueue(pid,'rules_ai',{})
    tasks.event(t['id'],'saved',70);tasks.event(t['id'],'next request',50)
    assert db.one('SELECT progress FROM tasks WHERE id=?',(t['id'],))['progress']==70
    assert [db.loads(r['payload'])['progress'] for r in db.rows('SELECT payload FROM task_events WHERE task_id=?',(t['id'],))]==[70,70]


def test_process_recovery_keeps_saved_batch_progress(client):
    pid=project(client);t=tasks.enqueue(pid,'rules_ai',{})
    db.execute("UPDATE tasks SET status='running',lease_until=0,result=? WHERE id=?",(db.dumps({'completed_batches':2,'processed_source_units':8,'added':20}),t['id']))
    output=security.put_blob(b'{"rules":[]}')
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),t['id'],pid,'completed','deepseek-test','openai',db.dumps({'output_blob':output}),2,db.now()))
    tasks.recover()
    saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['status']=='incomplete' and saved['result']['completed_batches']==2
    assert saved['result']['processed_source_units']==8 and saved['result']['raw_text']=='{"rules":[]}'


@pytest.mark.parametrize('request_count',[1,2])
def test_gpt_billing_requires_matching_scope_and_keeps_deepseek_receipt(client,monkeypatch,request_count):
    import httpx
    from app import summary_billing
    pid=project(client);security.save_key('fake-gpt-billing-key','gpt-fallback')
    cfg={**model_routing.DEFAULT,'model':'gpt-6.1-sol'};db.set_setting('gpt_fallback_provider',cfg)
    db.set_setting('material_summary_account_usage',{'remaining_exact':'123','scope':'deepseek'})
    task=tasks.enqueue(pid,'rules_ai',{});identity=summary_billing.key_hash('fake-gpt-billing-key')
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),task['id'],pid,'completed',cfg['model'],'openai',db.dumps({'provider':cfg,'credential_hash':identity}),db.dumps({'input_tokens':100,'output_tokens':50}),2,db.now()))
    def handle(r):
        assert str(r.url)=='https://www.ecoapi.cc/v1/usage' and r.headers['Authorization']=='Bearer fake-gpt-billing-key'
        return httpx.Response(200,json={'remaining':20,'unit':'USD','model_stats':[{'model':cfg['model'],'requests':request_count,'input_tokens':100,'output_tokens':50,'cost':0.01}]})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    result=summary_billing.reconcile(pid,profile='gpt-fallback')
    assert result['reconciled'] is (request_count==1)
    assert summary_billing.budget(pid)['used']==(0.01 if request_count==1 else 2)
    assert db.setting('material_summary_account_usage')['scope']=='deepseek'


def test_unknown_call_keeps_its_reservation_while_confirmed_calls_reconcile(client,monkeypatch):
    from test_summary_billing import setup,seed
    from app import summary_billing
    setup(monkeypatch);pid=project(client);confirmed=seed(pid)
    unknown=seed(pid,status='outcome_unknown',input_tokens=None,output_tokens=None)
    result=summary_billing.reconcile(pid)
    assert result['reconciled'] and summary_billing.budget(pid)['used']==2.02
    assert db.one('SELECT run_id FROM generation_billing_group_runs')['run_id']==confirmed
    assert db.one('SELECT status FROM generation_runs WHERE id=?',(unknown,))['status']=='outcome_unknown'
    assert summary_billing.budget(pid)['unknown_reserved']==2


def test_streamed_length_finish_is_known_invalid_and_keeps_usage(monkeypatch):
    import httpx
    from app import provider_stream
    wire='data: '+db.dumps({'choices':[{'delta':{'content':'{"rules":['},'finish_reason':'length'}],'usage':{'prompt_tokens':10,'completion_tokens':100,'completion_tokens_details':{'reasoning_tokens':90}}})+'\n\ndata: [DONE]\n\n'
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire.encode()))))
    with provider.request_scope({**provider.DEFAULT,'model':'deepseek-test'},'fake-stream-key'):
        with pytest.raises(provider.ProviderError) as error:provider_stream.generate('rules','source')
    assert not error.value.unknown and error.value.response_result['usage']['output_tokens']==100
    assert error.value.response_result['text']=='{"rules":['


def test_buffered_stream_reply_is_not_falsely_marked_as_streamed(monkeypatch):
    import httpx
    from app import provider_stream
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'message':{'content':'{"rules":[]}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':2}}))))
    with provider.request_scope({**provider.DEFAULT,'model':'deepseek-test'},'fake-stream-key'):
        result=provider_stream.generate('rules','source')
    assert result['streamed'] is False and result['usage']['output_tokens']==2
