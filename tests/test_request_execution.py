"""Actual transport evidence, endpoint preflights and fenced task transitions."""
import json
import httpx
import pytest
from test_workbench import client,project
from test_deepseek_pool import pool_endpoint,configure_pool,ok
from app import db,provider,security,tasks,request_receipts,task_transitions as transitions


def wire(monkeypatch,handler):
    original=httpx.Client;sent=[]
    def handle(request):
        sent.append(request)
        return handler(request)
    def connection(**kwargs):
        kwargs['transport']=httpx.MockTransport(handle)
        return original(**kwargs)
    monkeypatch.setattr(provider.httpx,'Client',connection)
    return sent


def configured(client):
    pid=project(client)
    endpoints=[pool_endpoint('a','A','https://a.example',priority=1,max_output_tokens=128,input_price=0.,output_price=0.),
               pool_endpoint('b','B','https://b.example',priority=2,max_output_tokens=128,input_price=0.,output_price=0.)]
    configure_pool(client,pid,endpoints,{'mode':'priority','retry_same_endpoint':0})
    return pid,endpoints


def test_actual_pool_attempts_and_events_preserve_requested_and_actual_identity(client,monkeypatch):
    pid,_=configured(client)
    sent=wire(monkeypatch,lambda r:httpx.Response(429,json={'error':'limit'}) if r.url.host=='a.example' else ok())
    task=tasks.enqueue(pid,'provider_probe',{})
    result=tasks.model_call(task,'fixed system','fixed evidence',{'file_ids':[]})
    assert [r.url.host for r in sent]==['a.example','b.example']
    intent=db.one('SELECT * FROM request_intents WHERE generation_run_id=?',(result['run_id'],))
    attempts=db.rows('SELECT * FROM provider_attempts WHERE intent_id=? ORDER BY ordinal',(intent['id'],))
    assert intent['state']=='persisted'
    assert [r['state']for r in attempts]==['known_rejected','persisted']
    assert [db.loads(r['identity'])['base_url']for r in attempts]==['https://a.example','https://b.example']
    assert db.rows("SELECT id FROM request_execution_events WHERE event_type='send_started'")
    meta=db.loads(db.one('SELECT manifest FROM generation_runs WHERE id=?',(result['run_id'],))['manifest'])
    assert meta['provider']['base_url']=='https://ds.example/v1'
    assert meta['actual_provider']['base_url']=='https://b.example'


@pytest.mark.parametrize('change',['capacity','currency','price'])
def test_pool_rechecks_actual_backup_before_any_second_http(client,monkeypatch,change):
    pid,_=configured(client)
    cfg=db.setting('deepseek_pool')
    target=cfg['endpoints'][1]
    if change=='capacity':target['context_limit']=130
    if change=='currency':target['currency']='CNY'
    if change=='price':target.update(input_price=1_000_000.,output_price=1_000_000.)
    db.set_setting('deepseek_pool',cfg)
    sent=wire(monkeypatch,lambda r:httpx.Response(429,json={'error':'limit'}))
    task=tasks.enqueue(pid,'provider_probe',{})
    with pytest.raises(ValueError):tasks.model_call(task,'full evidence system','x'*300,{'file_ids':[]})
    assert [r.url.host for r in sent]==['a.example']
    assert db.one('SELECT status FROM generation_runs')['status']=='failed_before_send'
    states=[r['state']for r in db.rows('SELECT state FROM provider_attempts ORDER BY ordinal')]
    assert states==['known_rejected','known_rejected']


def test_remote_unknown_attempt_and_reservation_survive_without_another_send(client,monkeypatch):
    pid,_=configured(client)
    def error(request):raise httpx.ReadTimeout('response unknown',request=request)
    sent=wire(monkeypatch,error)
    task=tasks.enqueue(pid,'provider_probe',{})
    with pytest.raises(provider.ProviderError) as captured:tasks.model_call(task,'system','original',{})
    assert captured.value.unknown and len(sent)==1
    assert db.one('SELECT state FROM request_intents')['state']=='outcome_unknown'
    assert db.one('SELECT state FROM provider_attempts')['state']=='outcome_unknown'
    assert db.one('SELECT status FROM generation_runs')['status']=='outcome_unknown'


@pytest.mark.parametrize('terminal',['succeeded','cancelled','failed'])
def test_late_task_transition_never_overwrites_new_attempt(client,terminal):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    db.execute("UPDATE tasks SET attempt=2,status='running',result=? WHERE id=?",(db.dumps({'new':'result'}),task['id']))
    assert not transitions.transition(task['id'],1,'running',status=terminal,result='late original candidate')
    saved=db.one('SELECT status,result FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='running' and db.loads(saved['result'])=={'new':'result'}
    detail=db.loads(db.one("SELECT detail FROM audit WHERE action='late_task_transition_ignored'")['detail'])
    assert detail['result_retained'] and detail['late_result_blob']


def test_late_progress_cannot_mutate_successor(client):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    db.execute("UPDATE tasks SET status='running',attempt=2,phase='new phase' WHERE id=?",(task['id'],))
    with transitions.ownership({**task,'attempt':1}):
        with pytest.raises(InterruptedError):tasks.event(task['id'],'late phase',99)
    assert db.one('SELECT phase FROM tasks WHERE id=?',(task['id'],))['phase']=='new phase'


def test_unknown_receipt_without_verified_lookup_capability_sends_nothing(client,monkeypatch):
    pid,_=configured(client);task=tasks.enqueue(pid,'provider_probe',{})
    identity='f'*64
    meta={'actual_provider':{'base_url':'https://a.example','model':'deepseek-test','protocol':'openai','currency':'USD'},
          'actual_provider_credential_hash':identity}
    run=db.uid()
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (run,task['id'],pid,'outcome_unknown','deepseek-test','openai',db.dumps(meta),db.now()))
    monkeypatch.setattr(provider,'client',lambda *a,**k:pytest.fail('No remote query without capability'))
    response=request_receipts.lookup(run)
    assert not response['recovered']
    assert db.one('SELECT status FROM generation_runs WHERE id=?',(run,))['status']=='outcome_unknown'


def test_drain_blocks_new_work_but_never_deletes_queued_or_unknown_work(client,monkeypatch):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    db.set_setting('runtime_drain',True)
    with pytest.raises(Exception):tasks.enqueue(pid,'provider_probe',{'new':True})
    monkeypatch.setattr(tasks,'dispatch',lambda t:pytest.fail('No claim while draining'))
    assert tasks.run_one() is False
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='queued'


@pytest.mark.parametrize('status',[400,422])
def test_known_nonretryable_rejection_is_not_mislabeled_unknown_attempt(client,monkeypatch,status):
    pid=project(client)
    security.save_key('fake-key')
    db.set_setting('provider',{**provider.DEFAULT,'base_url':'https://known-rejection.example','model':'deepseek-test','max_attempts':1})
    seen=wire(monkeypatch,lambda r:httpx.Response(status,json={'error':'invalid request shape'}))
    task=tasks.enqueue(pid,'provider_probe',{})
    with pytest.raises(provider.ProviderError):tasks.model_call(task,'original system','original input',{})
    assert len(seen)==1
    assert db.one('SELECT status FROM generation_runs')['status']=='rejected'
    assert db.one('SELECT state FROM provider_attempts')['state']=='known_rejected'
    assert db.one('SELECT state FROM request_intents')['state']=='known_rejected'


def test_unknown_error_has_priority_even_when_transport_hooks_are_unavailable(client,monkeypatch):
    from test_model_routing import configured,mock
    pid=configured(client,max_attempts=5)
    def lost(request):raise httpx.ReadTimeout('execution may have happened',request=request)
    mock(monkeypatch,lost)
    task=tasks.enqueue(pid,'provider_probe',{})
    with pytest.raises(provider.ProviderError) as captured:tasks.model_call(task,'same system','same input',{})
    assert captured.value.unknown
    assert db.one('SELECT state FROM provider_attempts')['state']=='outcome_unknown'
    assert db.one('SELECT state FROM request_intents')['state']=='outcome_unknown'


@pytest.mark.parametrize('streaming',[False,True])
@pytest.mark.parametrize('protocol',['openai','responses'])
def test_actual_pool_hook_covers_stream_and_both_protocols(client,monkeypatch,streaming,protocol):
    from app import security,provider_identity
    pid=project(client)
    endpoints=[pool_endpoint('first','First','https://first-attempt.example/v1',model='deepseek-first',
                            priority=1,protocol=protocol,streaming=streaming,max_output_tokens=128,input_price=0.,output_price=0.),
               pool_endpoint('second','Second','https://second-attempt.example/v1',model='deepseek-second',
                            priority=2,protocol=protocol,streaming=streaming,max_output_tokens=128,input_price=0.,output_price=0.,
                            credential_ref='pool',api_key='fake-second-attempt-key')]
    configure_pool(client,pid,endpoints,{'mode':'priority','retry_same_endpoint':0})
    def handle(request):
        body=json.loads(request.content)
        assert request.url.path==('/v1/responses' if protocol=='responses' else '/v1/chat/completions')
        assert body.get('stream',False) is streaming
        if request.url.host=='first-attempt.example':
            return httpx.Response(429,json={'error':'limit'})
        assert request.headers['Authorization']=='Bearer fake-second-attempt-key'
        usage=({'input_tokens':13,'output_tokens':7,'input_tokens_details':{'cached_tokens':8}}
               if protocol=='responses' else {'prompt_tokens':13,'completion_tokens':7,'prompt_tokens_details':{'cached_tokens':8}})
        if streaming:
            if protocol=='responses':
                events=[{'type':'response.output_text.delta','delta':'{"ok":true}'},
                        {'type':'response.completed','response':{'id':'actual-stream','usage':usage}}]
            else:events=[{'id':'actual-stream','choices':[{'delta':{'content':'{"ok":true}'},'finish_reason':'stop'}],'usage':usage}]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                content=''.join('data: '+json.dumps(event)+'\n\n' for event in events).encode())
        if protocol=='responses':
            return httpx.Response(200,json={'id':'actual-response','status':'completed',
                'output':[{'type':'message','content':[{'type':'output_text','text':'{"ok":true}'}]}],'usage':usage})
        return httpx.Response(200,json={'id':'actual-chat','choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}],'usage':usage})
    sent=wire(monkeypatch,handle)
    task=tasks.enqueue(pid,'provider_probe',{})
    result=tasks.model_call(task,'Complete fixed system.','Complete fixed evidence.',{'file_ids':[]})
    assert result['text']=='{"ok":true}'
    assert [request.url.host for request in sent]==['first-attempt.example','second-attempt.example']
    intent=db.one('SELECT * FROM request_intents WHERE generation_run_id=?',(result['run_id'],))
    attempts=db.rows('SELECT * FROM provider_attempts WHERE intent_id=? ORDER BY ordinal',(intent['id'],))
    assert intent['state']=='persisted'
    assert [attempt['state'] for attempt in attempts]==['known_rejected','persisted']
    identities=[db.loads(attempt['identity']) for attempt in attempts]
    assert [identity['model'] for identity in identities]==['deepseek-first','deepseek-second']
    assert all(identity['protocol']==protocol for identity in identities)
    assert identities[-1]['credential_hash']==__import__('hashlib').sha256(b'fake-second-attempt-key').hexdigest()
    assert db.loads(attempts[-1]['usage'])['cache_hit_tokens']==8
    row=db.one('SELECT * FROM generation_runs WHERE id=?',(result['run_id'],))
    projected=provider_identity.effective(row)
    assert projected['requested']['model']=='deepseek-test'
    assert projected['effective']['model']=='deepseek-second'
    assert projected['effective']['protocol']==protocol
    assert projected['effective']['credential_hash']==identities[-1]['credential_hash']
