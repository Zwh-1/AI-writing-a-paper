import httpx
import pytest
from test_workbench import client,project
from app import ai_autopilot as auto,db,provider,security,tasks,summary_billing


def used_budget(pid,amount):
    job=tasks.enqueue(pid,'provider_probe',{})
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,cost,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
               (db.uid(),job['id'],pid,'completed','fixture','openai','{}',amount,amount,db.now()))
    db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(job['id'],))


def ready(monkeypatch):
    monkeypatch.setattr(security,'key_status',lambda *args,**kwargs:{'configured':True})


def test_stale_round_budget_rejected_without_changing_policy_or_ledger(client,monkeypatch):
    pid=project(client);ready(monkeypatch);used_budget(pid,18.95)
    db.set_setting('ai_autopilot_policy:'+pid,{'enabled':False,'budget_limit':.5})
    before=summary_billing.budget(pid)
    response=client.post(f'/api/v1/projects/{pid}/ai-autopilot',json={'budget_limit':1.1})
    assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='AUTOPILOT_BUDGET_CHANGED'
    assert response.json()['detail']['budget']['available']==pytest.approx(1.05)
    assert auto.policy(pid)['budget_limit']==.5 and not auto.policy(pid)['enabled']
    assert not db.rows('SELECT id FROM tasks WHERE kind=?',(auto.KIND,))
    assert summary_billing.budget(pid)==before


def test_available_round_budget_can_start_and_duplicate_click_reuses_job(client,monkeypatch):
    pid=project(client);ready(monkeypatch);used_budget(pid,18.95)
    first=client.post(f'/api/v1/projects/{pid}/ai-autopilot',json={'budget_limit':1.05})
    assert first.status_code==200,first.text
    assert db.loads(first.json()['payload'])['budget_limit']==pytest.approx(1.05)
    second=client.post(f'/api/v1/projects/{pid}/ai-autopilot',json={'budget_limit':1.05})
    assert second.status_code==200 and second.json()['id']==first.json()['id']
    assert len(db.rows('SELECT id FROM tasks WHERE kind=?',(auto.KIND,)))==1
    assert summary_billing.budget(pid)['used']==pytest.approx(18.95)


def test_follow_imports_caps_saved_policy_to_remaining_budget(client):
    pid=project(client);used_budget(pid,18.946)
    db.set_setting('ai_autopilot_policy:'+pid,{'enabled':True,'budget_limit':1.1})
    job=auto.schedule(pid)
    assert db.loads(job['payload'])['budget_limit']==1.05
    assert auto.policy(pid)['budget_limit']==1.1
    assert summary_billing.budget(pid)['used']==pytest.approx(18.946)


def test_follow_imports_waits_with_less_than_one_cent_without_phantom_job(client):
    pid=project(client);used_budget(pid,19.995)
    db.set_setting('ai_autopilot_policy:'+pid,{'enabled':True,'budget_limit':1.1})
    assert auto.schedule(pid) is None
    assert not db.rows('SELECT id FROM tasks WHERE kind=?',(auto.KIND,))
    assert auto.policy(pid)['enabled']


def test_failed_billing_query_keeps_all_reservations_and_reports_403(client,monkeypatch):
    pid=project(client)
    job=tasks.enqueue(pid,'rules_ai',{})
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
               (db.uid(),job['id'],pid,'outcome_unknown','fixture','openai','{}',2,db.now()))
    ready(monkeypatch)
    monkeypatch.setattr(security,'get_key',lambda *args:'synthetic-key')
    monkeypatch.setattr(provider,'get_key',lambda:'synthetic-key')
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(403,json={'error':'forbidden'}))))
    before=summary_billing.budget(pid)
    response=client.post(f'/api/v1/projects/{pid}/tasks/billing-reconcile',json={'profile':'primary'})
    assert response.status_code==200,response.text
    assert not response.json()['reconciled'] and '403' in response.json()['reason']
    assert summary_billing.budget(pid)==before
    assert db.one('SELECT status FROM generation_runs')['status']=='outcome_unknown'
    assert not db.rows('SELECT * FROM generation_billing_groups')


def test_budget_reconciliation_profiles_report_partial_failure_truthfully(client,monkeypatch):
    pid=project(client);ready(monkeypatch)
    from app import model_routing
    monkeypatch.setattr(model_routing,'ready',lambda:True)
    calls=[]
    def reconcile(identity,profile):
        calls.append((identity,profile))
        return {'reconciled':profile!='gpt-fallback','reason':'HTTP 403' if profile=='gpt-fallback' else None}
    monkeypatch.setattr(summary_billing,'reconcile',reconcile)
    response=client.post(f'/api/v1/projects/{pid}/tasks/billing-reconcile',json={})
    assert response.status_code==200,response.text
    profiles={row['profile']:row for row in response.json()['profiles']}
    assert set(profiles)=={'primary','material-summary','gpt-fallback','image-generation'}
    assert {profile for identity,profile in calls}==set(profiles) and len(calls)==len(profiles)
    assert profiles['gpt-fallback']['reason']=='HTTP 403'
    assert response.json()['budget']['limit']==20


def test_budget_wake_requires_a_full_request_reservation_not_just_positive_balance(client,monkeypatch):
    pid=project(client);used_budget(pid,18.95)
    from app import rule_pipeline,model_routing
    job=tasks.enqueue(pid,'rules_ai',{'automatic':True,'budget_limit':5,'provider_profile':'gpt','allow_gpt':True,'gpt_budget':5})
    rule_pipeline.ensure_job(job,db.loads(job['payload']))
    db.execute("UPDATE tasks SET status='budget_blocked',result=? WHERE id=?",(db.dumps({'budget':{'reservation':2}}),job['id']))
    monkeypatch.setattr(summary_billing,'reconcile',lambda *a,**k:{'reconciled':False,'reason':'HTTP 403'})
    monkeypatch.setattr(model_routing,'ready',lambda:False)
    tasks.wake_rule_jobs()
    assert db.one('SELECT status FROM tasks WHERE id=?',(job['id'],))['status']=='budget_blocked'
    assert summary_billing.budget(pid)['used']==pytest.approx(18.95)


def test_budget_wake_resumes_once_full_project_and_round_limits_allow_it(client,monkeypatch):
    pid=project(client);used_budget(pid,18.95)
    from app import rule_pipeline,model_routing
    job=tasks.enqueue(pid,'rules_ai',{'automatic':True,'budget_limit':5,'provider_profile':'primary'})
    rule_pipeline.ensure_job(job,db.loads(job['payload']))
    db.execute("UPDATE tasks SET status='budget_blocked',result=? WHERE id=?",(db.dumps({'budget':{'reservation':.05}}),job['id']))
    monkeypatch.setattr(summary_billing,'reconcile',lambda *a,**k:{'reconciled':False,'reason':'HTTP 403'})
    monkeypatch.setattr(model_routing,'ready',lambda:False)
    tasks.wake_rule_jobs()
    assert db.one('SELECT status FROM tasks WHERE id=?',(job['id'],))['status']=='queued'
    assert len(db.rows('SELECT id FROM tasks WHERE kind=?',('rules_ai',)))==1
