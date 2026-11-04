"""Automatic rule-job wakes honor the saved GPT route and its own budget."""
import pytest

from test_workbench import client, project
from app import db, model_routing, provider, summary_billing, tasks


def blocked_rule_job(client, monkeypatch, *, allow_gpt, gpt_budget, gpt_ready=True):
    pid=project(client)
    db.set_setting('provider',{**provider.DEFAULT,'max_request_cost':0.1})
    db.set_setting('gpt_fallback_provider',{**model_routing.DEFAULT,'max_request_cost':0.75})
    current=tasks.enqueue(pid,'rules_ai',{'automatic':True,'provider_profile':'primary','budget_limit':5.})
    payload={'automatic':True,'provider_profile':'primary','rule_job_id':current['id'],'budget_limit':5.}
    db.execute('INSERT INTO rule_jobs VALUES(?,?,?,?,?,?,?)',
               (current['id'],pid,5.,gpt_budget,int(allow_gpt),'{}',db.now()))
    db.execute("UPDATE tasks SET payload=?,status='budget_blocked',error=? WHERE id=?",
               (db.dumps(payload),'GPT sub-budget blocked',current['id']))
    model_routing._save_automatic_route(current,payload,5,'five safe primary failures')
    monkeypatch.setattr(model_routing,'ready',lambda: gpt_ready)
    monkeypatch.setattr(summary_billing,'reconcile',lambda *args,**kwargs:{'reconciled':False})
    monkeypatch.setattr(provider,'client',lambda *args,**kwargs:pytest.fail('Budget waking must not send model HTTP'))
    return db.one('SELECT * FROM tasks WHERE id=?',(current['id'],))


@pytest.mark.parametrize('allow_gpt,gpt_budget',[(False,4.),(True,0.5)])
def test_sticky_gpt_rule_job_stays_blocked_without_permission_or_sufficient_gpt_budget(client,monkeypatch,allow_gpt,gpt_budget):
    current=blocked_rule_job(client,monkeypatch,allow_gpt=allow_gpt,gpt_budget=gpt_budget)
    tasks.wake_rule_jobs()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(current['id'],))
    assert saved['status']=='budget_blocked' and saved['error']=='GPT sub-budget blocked'
    assert model_routing._automatic_route(saved,db.loads(saved['payload']))['route']=='gpt'
    assert not db.rows('SELECT id FROM generation_runs')


def test_sticky_gpt_rule_job_stays_blocked_when_the_backup_is_not_ready(client,monkeypatch):
    current=blocked_rule_job(client,monkeypatch,allow_gpt=True,gpt_budget=4.,gpt_ready=False)
    tasks.wake_rule_jobs()
    assert db.one('SELECT status FROM tasks WHERE id=?',(current['id'],))['status']=='budget_blocked'
    assert not db.rows('SELECT id FROM generation_runs')


def test_sticky_gpt_rule_job_wakes_only_after_its_permission_and_budget_are_restored(client,monkeypatch):
    current=blocked_rule_job(client,monkeypatch,allow_gpt=False,gpt_budget=0.)
    tasks.wake_rule_jobs()
    assert db.one('SELECT status FROM tasks WHERE id=?',(current['id'],))['status']=='budget_blocked'
    db.execute('UPDATE rule_jobs SET allow_gpt=1,gpt_budget=1 WHERE id=?',(current['id'],))
    tasks.wake_rule_jobs()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(current['id'],))
    assert saved['status']=='queued' and saved['error'] is None
    assert saved['payload']==current['payload']
    assert model_routing._automatic_route(saved,db.loads(saved['payload']))['route']=='gpt'
    assert db.one('SELECT COUNT(*) AS count FROM tasks')['count']==1
    assert db.one('SELECT budget_limit,gpt_budget FROM rule_jobs WHERE id=?',(current['id'],))=={'budget_limit':5.,'gpt_budget':1.}
    assert not db.rows('SELECT id FROM generation_runs')
