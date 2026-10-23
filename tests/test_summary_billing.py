import json,os
import httpx,pytest
from test_workbench import client,project,file
from app import db,security,provider,tasks,material_summary as summary,summary_billing as billing

def seed(pid,identity='billing-secret',status='completed',input_tokens=100,output_tokens=50,legacy=False):
    task=tasks.enqueue(pid,'material_summary',{'file_id':'billing-fixture-'+db.uid()});rid=db.uid()
    manifest={'provider':{**summary.config()},'purpose':'material_summary'}
    if not legacy:manifest['credential_hash']=billing.key_hash(identity)
    usage={'input_tokens':input_tokens,'output_tokens':output_tokens} if input_tokens is not None else {}
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,reserved,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,task['id'],pid,status,summary.config()['model'],'openai',db.dumps(manifest),db.dumps(usage),2,'模型输出被截断' if status=='rejected' else None,db.now()))
    return rid

def setup(monkeypatch,stat=None,unit='USD'):
    monkeypatch.setenv('PAPER_SUMMARY_API_KEY','billing-secret')
    if stat is None:stat={'model':'deepseek-chat','requests':1,'input_tokens':100,'output_tokens':50,'cost':0.02,'actual_cost':0.01,'account_cost':0.02}
    def response(request):
        assert request.method=='GET' and str(request.url)=='https://api.x5m5x.com/v1/usage'
        assert request.headers['Authorization']=='Bearer billing-secret'
        return httpx.Response(200,json={'remaining':9.9,'unit':unit,'isValid':True,'model_stats':[stat]})
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(response)))
    return stat

def test_reconciles_exact_scope_without_faking_individual_cost(client,monkeypatch):
    setup(monkeypatch);p=project(client);rid=seed(p)
    assert billing.budget(p)['used']==2
    result=billing.reconcile(p);assert result['reconciled'] and result['amount']==0.02
    assert billing.budget(p)['used']==0.02
    run=db.one('SELECT * FROM generation_runs WHERE id=?',(rid,));assert run['cost'] is None and run['reserved']==2
    assert billing.budget(p)['unknown_reserved']==0
    assert db.one('SELECT * FROM generation_billing_groups')['currency']=='USD'
    assert billing.reconcile(p)['reconciled']
    assert db.one('SELECT COUNT(*) n FROM generation_billing_group_runs')['n']==1

@pytest.mark.parametrize('problem',['count','input','output','currency','key','active','negative','missing_unit','nan'])
def test_mismatch_keeps_reservations(client,monkeypatch,problem):
    stat={'model':'deepseek-chat','requests':2 if problem=='count' else 1,'input_tokens':101 if problem=='input' else 100,'output_tokens':51 if problem=='output' else 50,'cost':-1 if problem=='negative' else float('nan') if problem=='nan' else 0.02}
    setup(monkeypatch,stat,unit='EUR' if problem=='currency' else 'USD')
    p=project(client);seed(p,identity='other-key' if problem=='key' else 'billing-secret',status='sent' if problem=='active' else 'completed')
    if problem=='missing_unit':
        monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'remaining':1,'model_stats':[stat]}))))
    if problem=='nan':
        monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=json.dumps({'remaining':1,'unit':'USD','model_stats':[stat]})))))
    result=billing.reconcile(p);assert not result['reconciled']
    assert not db.rows('SELECT * FROM generation_billing_groups')
    assert billing.budget(p)['used']==2

def test_shared_model_across_projects_not_misattributed(client,monkeypatch):
    setup(monkeypatch,{'model':'deepseek-chat','requests':2,'input_tokens':200,'output_tokens':100,'cost':0.04})
    p=project(client);q=project(client,'另一个项目');seed(p);seed(q)
    assert not billing.reconcile(p)['reconciled']
    assert billing.budget(p)['used']==2 and billing.budget(q)['used']==2

def test_legacy_key_lifetime_and_truncation_are_checked(client,monkeypatch):
    setup(monkeypatch,{'model':'deepseek-chat','requests':1,'input_tokens':100,'output_tokens':50,'cost':0.02})
    monkeypatch.delenv('PAPER_SUMMARY_API_KEY');security.save_key('billing-secret',summary.PROFILE)
    p=project(client);rid=seed(p,legacy=True,status='rejected',input_tokens=None)
    assert billing.reconcile(p)['reconciled']
    assert db.one('SELECT status FROM generation_runs WHERE id=?',(rid,))['status']=='completed_invalid'
    assert billing.budget(p)['used']==0.02

def test_rotated_key_does_not_claim_legacy_requests(client,monkeypatch):
    setup(monkeypatch);monkeypatch.delenv('PAPER_SUMMARY_API_KEY');p=project(client);rid=seed(p,legacy=True)
    security.save_key('billing-secret',summary.PROFILE)
    key_path=security.DATA/'material-summary.dpapi';future=key_path.stat().st_mtime+60;os.utime(key_path,(future,future))
    assert not billing.reconcile(p)['reconciled']
    assert billing.budget(p)['used']==2

def test_group_grows_only_when_new_total_matches(client,monkeypatch):
    stat=setup(monkeypatch);p=project(client);seed(p);assert billing.reconcile(p)['reconciled']
    seed(p);assert not billing.reconcile(p)['reconciled']
    assert billing.budget(p)['used']==2.02
    stat.update(requests=2,input_tokens=200,output_tokens=100,cost=0.04,account_cost=0.04,actual_cost=0.02)
    assert billing.reconcile(p)['reconciled'];assert billing.budget(p)['used']==0.04
    assert db.one('SELECT COUNT(*) n FROM generation_billing_groups')['n']==1

def test_truncated_generation_preserves_response_and_budget(client,monkeypatch):
    monkeypatch.setenv('PAPER_SUMMARY_API_KEY','billing-secret');p=project(client);f=file(client,p)
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':'truncated','choices':[{'finish_reason':'length','message':{'content':'{"overview":"incomplete'}}],'usage':{'prompt_tokens':100,'completion_tokens':4096}}))))
    job=client.post('/api/v1/files/'+f['id']+'/summary').json()['task'];tasks.run_one()
    run=db.one('SELECT * FROM generation_runs');assert run['status']=='completed_invalid'
    assert db.loads(run['usage'])['output_tokens']==4096
    result=db.loads(db.one('SELECT result FROM tasks WHERE id=?',(job['id'],))['result']);assert result['raw_text']=='{"overview":"incomplete'
    assert billing.budget(p)['used']==2


def test_queue_position_and_batch_status(client,monkeypatch):
    monkeypatch.setenv('PAPER_SUMMARY_API_KEY','billing-secret');p=project(client);a=file(client,p);b=file(client,p,'另一份资料')
    first=client.post('/api/v1/files/'+a['id']+'/summary').json()['task'];second=client.post('/api/v1/files/'+b['id']+'/summary').json()['task']
    assert summary.status(a['id'])['task']['queue_ahead']==0
    assert summary.status(b['id'])['task']['queue_ahead']==1
    data=client.get('/api/v1/projects/'+p+'/materials/summary-status').json();assert data['totals']['queued']==2


def test_unknown_status_not_repeated_by_batch(client,monkeypatch):
    monkeypatch.setenv('PAPER_SUMMARY_API_KEY','billing-secret');p=project(client);f=file(client,p)
    job=client.post('/api/v1/files/'+f['id']+'/summary').json()['task'];db.execute("UPDATE tasks SET status='outcome_unknown' WHERE id=?",(job['id'],))
    assert client.post('/api/v1/files/'+f['id']+'/summary').status_code==409
    batch=client.post('/api/v1/projects/'+p+'/materials/summarize').json();assert batch['count']==0 and len(batch['skipped'])==1


def test_reconciliation_does_not_reduce_manually_confirmed_cost(client,monkeypatch):
    setup(monkeypatch);p=project(client);rid=seed(p)
    db.execute('UPDATE generation_runs SET cost=0.5 WHERE id=?',(rid,))
    assert billing.reconcile(p)['amount']==0.5
    assert billing.budget(p)['used']==0.5
