import pytest
import time

from test_workbench import client
from test_paid_testing import setup_allocation,bound_task,transport
from app import db,paid_testing as testing,tasks,provider
from app.summary_billing import BudgetExceeded


def stopped_run(client,monkeypatch):
    pid,cfg,grant,old=setup_allocation(client,monkeypatch)
    task=bound_task(pid,grant);seen=[];transport(monkeypatch,seen,fail=True)
    with pytest.raises(provider.ProviderError):tasks._model_call(task,'sys','input',{})
    db.execute("UPDATE tasks SET status='outcome_unknown' WHERE id=?",(task['id'],))
    run=db.one('SELECT * FROM generation_runs WHERE task_id=?',(task['id'],))
    return pid,cfg,grant,old,run


def test_human_resume_preserves_cap_and_unknown_hold(client,monkeypatch):
    pid,cfg,grant,old,run=stopped_run(client,monkeypatch)
    before=testing.report(pid,grant['id'])['used']
    authorization=testing.authorize_resume(pid,grant['id'],[run['id']],'用户在看到本轮524预留后明确要求：请完成编译。')
    task=bound_task(pid,grant,paid_testing_resume_id=authorization['id']);seen=[];transport(monkeypatch,seen)
    tasks._model_call(task,'sys','new input',{})
    assert len(seen)==1 and testing.get(pid,grant['id'])['limit']==grant['limit']
    assert testing.report(pid,grant['id'])['used']==pytest.approx(before+.0003)
    assert db.one('SELECT * FROM generation_runs WHERE id=?',(run['id'],))==run
    assert db.one('SELECT budget FROM projects WHERE id=?',(pid,))['budget']==2


def test_unacknowledged_or_other_historical_request_cannot_resume(client,monkeypatch):
    pid,cfg,grant,old,run=stopped_run(client,monkeypatch)
    with pytest.raises(ValueError):testing.authorize_resume(pid,grant['id'],[run['id']],'')
    with pytest.raises(BudgetExceeded):testing.authorize_resume(pid,grant['id'],[old],'用户仅要求本轮续编')
    task=bound_task(pid,grant);seen=[];transport(monkeypatch,seen)
    with pytest.raises(BudgetExceeded,match='新增请求'):tasks._model_call(task,'sys','new',{})
    assert not seen


def test_explicit_replay_is_limited_to_one_sent_attempt(client,monkeypatch):
    pid,cfg,grant,old,run=stopped_run(client,monkeypatch)
    authorization=testing.authorize_resume(pid,grant['id'],[run['id']],'用户明确要求继续本轮编译，保留未知预留。')
    task=bound_task(pid,grant,paid_testing_resume_id=authorization['id'])
    assert testing.replay_allowed(task,run)
    seen=[];transport(monkeypatch,seen)
    tasks._model_call(task,'sys','input',{'knowledge_authorized_replay_of_run_id':run['id']})
    assert not testing.replay_allowed(task,run)


def test_second_unknown_still_stops_after_authorized_resume(client,monkeypatch):
    pid,cfg,grant,old,run=stopped_run(client,monkeypatch)
    authorization=testing.authorize_resume(pid,grant['id'],[run['id']],'用户明确要求继续本轮编译，保留未知预留。')
    task=bound_task(pid,grant,paid_testing_resume_id=authorization['id']);seen=[];transport(monkeypatch,seen,fail=True)
    with pytest.raises(provider.ProviderError):tasks._model_call(task,'sys','input',{})
    other=bound_task(pid,grant,paid_testing_resume_id=authorization['id'])
    with pytest.raises(BudgetExceeded,match='新增请求'):tasks._model_call(other,'sys','other input',{})
    assert len(seen)==1


def test_changed_unknown_snapshot_invalidates_authorization(client,monkeypatch):
    pid,cfg,grant,old,run=stopped_run(client,monkeypatch)
    authorization=testing.authorize_resume(pid,grant['id'],[run['id']],'用户明确要求继续本轮编译。')
    db.execute('UPDATE generation_runs SET reserved=reserved+0.001 WHERE id=?',(run['id'],))
    task=bound_task(pid,grant,paid_testing_resume_id=authorization['id']);seen=[];transport(monkeypatch,seen)
    with pytest.raises(BudgetExceeded,match='历史待核'):tasks._model_call(task,'sys','input',{})
    assert not seen


def test_bounded_same_task_inflight_requests_keep_atomic_reservations(client,monkeypatch):
    pid,cfg,grant,old=setup_allocation(client,monkeypatch)
    task=bound_task(pid,grant,kind='graph_wiki_build',knowledge_parallel_requests=2)
    db.execute("UPDATE tasks SET status='running',heartbeat=? WHERE id=?",(time.time(),task['id']))
    inserted=[]
    for i in range(2):
        with db.transaction():
            scope=testing.guard(task,cfg,'expense-fixture-secret',.03)
            assert len(scope['inflight_request_ids'])==i
            rid=db.uid();inserted.append(rid)
            db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (rid,task['id'],pid,'sent',cfg['model'],'openai','{}',.03,db.now()))
    assert testing.report(pid,grant['id'])['used']==.06
    with pytest.raises(BudgetExceeded,match='并发请求'):testing.guard(task,cfg,'expense-fixture-secret',.03)
    db.execute("UPDATE generation_runs SET status='outcome_unknown' WHERE id=?",(inserted[0],))
    with pytest.raises(BudgetExceeded,match='新增请求'):testing.guard(task,cfg,'expense-fixture-secret',.03)


def test_parallel_inflight_does_not_bypass_total_cap(client,monkeypatch):
    pid,cfg,grant,old=setup_allocation(client,monkeypatch,limit=.04)
    task=bound_task(pid,grant,kind='graph_wiki_build',knowledge_parallel_requests=2)
    db.execute("UPDATE tasks SET status='running',heartbeat=? WHERE id=?",(time.time(),task['id']))
    with db.transaction():
        testing.guard(task,cfg,'expense-fixture-secret',.03)
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (db.uid(),task['id'],pid,'sent',cfg['model'],'openai','{}',.03,db.now()))
        with pytest.raises(BudgetExceeded,match='授权上限'):testing.guard(task,cfg,'expense-fixture-secret',.03)
