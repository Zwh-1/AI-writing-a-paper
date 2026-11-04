"""Read-only receipt recovery and explicit stage ceilings never authorize generation."""
import hashlib
import httpx
import pytest
from test_workbench import client,project
from app import adapter,db,provider,security,tasks,execution_policy,request_receipts
from app.summary_billing import BudgetExceeded


def receipt_run(client):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    cfg={**provider.DEFAULT,'base_url':'https://receipt.example/v1','model':'gpt-receipt','protocol':'responses',
         'max_attempts':1,'max_output_tokens':128}
    key='synthetic-receipt-key';security.save_key(key);db.set_setting('provider',cfg)
    run=db.uid();manifest={'file_ids':[],'actual_provider':cfg,'actual_provider_credential_hash':adapter.credential_hash(key)}
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,remote_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
               (run,task['id'],pid,'outcome_unknown',cfg['model'],cfg['protocol'],db.dumps(manifest),.1,'saved-response-id',db.now()))
    capability={'verified':True,'method':'GET','protocol':'responses','path':'/v1/responses/{response_id}',
                'credential_hash':adapter.credential_hash(key)}
    db.set_setting('provider_receipt_capabilities',{cfg['base_url']:capability})
    return pid,task,run,cfg


def receipt_body():
    return {'id':'saved-response-id','model':'gpt-receipt','status':'completed',
            'output':[{'type':'message','content':[{'type':'output_text','text':'saved original candidate'}]}],
            'usage':{'input_tokens':100,'output_tokens':9,'input_tokens_details':{'cached_tokens':40}}}


def mock_receipt(monkeypatch,body,status=200):
    seen=[]
    def handle(request):
        seen.append(request)
        assert request.method=='GET' and request.url.path=='/v1/responses/saved-response-id'
        return httpx.Response(status,json=body)
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(handle)))
    return seen


def test_verified_receipt_restores_original_output_without_resuming_or_new_charge(client,monkeypatch):
    _,task,run,_=receipt_run(client)
    seen=mock_receipt(monkeypatch,receipt_body())
    response=request_receipts.lookup(run)
    assert response['recovered'] and not response['task_resumed'] and len(seen)==1
    saved=db.one('SELECT * FROM generation_runs WHERE id=?',(run,));manifest=db.loads(saved['manifest'])
    assert saved['status']=='completed' and saved['cost'] is None and saved['reserved']==.1
    assert db.loads(saved['usage'])['cache_hit_tokens']==40
    assert security.safe_object(manifest['output_blob']).read_text(encoding='utf-8')=='saved original candidate'
    assert manifest['receipt_recovery']['quality_verified'] is False
    assert db.one('SELECT COUNT(*) n FROM generation_runs')['n']==1
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='queued'
    assert request_receipts.lookup(run)['recovered'] is False and len(seen)==1


@pytest.mark.parametrize('change',['response_id','model','incomplete','http_error','credential'])
def test_unverified_or_nonfinal_receipt_never_discards_unknown_state(client,monkeypatch,change):
    _,_,run,_=receipt_run(client);body=receipt_body()
    if change=='response_id':body['id']='another-response'
    if change=='model':body['model']='another-model'
    if change=='incomplete':body['status']='in_progress'
    if change=='credential':security.save_key('changed-receipt-key')
    seen=mock_receipt(monkeypatch,body,503 if change=='http_error' else 200)
    if change in ('response_id','model','credential'):
        with pytest.raises(Exception):request_receipts.lookup(run)
    else:assert not request_receipts.lookup(run)['recovered']
    assert db.one('SELECT status FROM generation_runs WHERE id=?',(run,))['status']=='outcome_unknown'
    assert len(seen)==(0 if change=='credential' else 1)


def test_stage_budget_is_opt_in_and_stops_without_silently_skipping_review(client):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    response=client.put(f'/api/v1/projects/{pid}/execution-budget-policy',json={'stage_limits':{'semantic_review':.01},'currency':'USD'})
    assert response.status_code==200
    with pytest.raises(BudgetExceeded):execution_policy.check_stage(task,{'writing_stage':'semantic_review'},.02)
    execution_policy.check_stage(task,{'writing_stage':'draft'},.02)
    assert not db.rows('SELECT id FROM generation_runs')


def test_cost_preview_keeps_unknown_and_does_not_allocate_aggregate_bills(client):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    response=client.post(f'/api/v1/projects/{pid}/execution-cost-preview',json={
        'task_id':task['id'],'remaining_by_stage':{'draft':3,'semantic_review':2}})
    assert response.status_code==200
    body=response.json()
    assert body['remaining_estimate'] is None and body['known_stage_subtotal']==0
    assert all(stage['sample_count']==0 and stage['remaining_estimate'] is None for stage in body['stages'])
    assert body['new_model_requests']==0 and not body['quality_checks_skipped'] and not body['budget_changed']


def test_draining_owner_can_checkpoint_a_child_but_cannot_claim_it(client):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    db.execute("UPDATE tasks SET attempt=1,status='running' WHERE id=?",(task['id'],))
    db.set_setting('runtime_drain',True)
    from app.task_transitions import ownership
    with ownership({**task,'attempt':1}):
        child=tasks.enqueue(pid,'provider_probe',{'parent_task_id':task['id']})
    assert child['status']=='queued' and tasks.run_one() is False


def test_one_logical_request_has_one_intent_across_five_deepseek_failures_and_gpt(client,monkeypatch):
    from test_model_routing import configured,mock,ok
    pid=configured(client,max_attempts=5);seen=[]
    def handle(request):
        seen.append(request)
        return httpx.Response(429,json={'error':'limit'},headers={'retry-after':'0'}) if request.url.host=='ds.example' else ok()
    mock(monkeypatch,handle)
    task=tasks.enqueue(pid,'provider_probe',{})
    result=tasks.model_call(task,'same system','same context',{})
    assert len(seen)==6
    assert db.one('SELECT COUNT(*) n FROM request_intents')['n']==1
    assert db.one('SELECT COUNT(*) n FROM request_intent_runs')['n']==6
    intent=db.one('SELECT * FROM request_intents')
    assert intent['state']=='persisted'
    attempts=db.rows('SELECT state FROM provider_attempts ORDER BY ordinal')
    assert [row['state']for row in attempts]==['known_rejected']*5+['persisted']


def test_earlier_rejected_retry_cannot_erase_final_unknown_intent(client,monkeypatch):
    from test_model_routing import configured,mock
    pid=configured(client,max_attempts=5);seen=[]
    def handle(request):
        seen.append(request)
        if len(seen)==1:return httpx.Response(429,json={'error':'limit'},headers={'retry-after':'0'})
        raise httpx.ReadTimeout('second attempt may have executed',request=request)
    mock(monkeypatch,handle)
    task=tasks.enqueue(pid,'provider_probe',{})
    with pytest.raises(provider.ProviderError) as error:tasks.model_call(task,'system','context',{})
    assert error.value.unknown and len(seen)==2
    assert db.one('SELECT state FROM request_intents')['state']=='outcome_unknown'
    assert [r['state']for r in db.rows('SELECT state FROM provider_attempts ORDER BY ordinal')]==['known_rejected','outcome_unknown']
