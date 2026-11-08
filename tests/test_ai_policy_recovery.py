"""A whole-paper budget pause must not adopt changed quality defaults on resume."""
from copy import deepcopy
from test_workbench import client
from test_paper_writing import manuscript, launch
from app import db, tasks, writing_flow
from app.summary_billing import BudgetExceeded


def test_whole_paper_resume_keeps_original_quality_after_budget_pause(manuscript, monkeypatch):
    env = manuscript
    writing_flow.save_settings(env['pid'], {'semantic_review': False, 'max_revision_rounds': 0})
    original_call = tasks.model_call
    original_review = writing_flow._semantic
    calls = []
    observed_policy = []
    def pause(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise BudgetExceeded('synthetic pause before second chapter')
        return original_call(*args, **kwargs)
    def review(task, *args, **kwargs):
        observed_policy.append(writing_flow.task_settings(task['project_id'], task)['semantic_review'])
        return original_review(task, *args, **kwargs)
    monkeypatch.setattr(tasks, 'model_call', pause)
    monkeypatch.setattr(writing_flow, '_semantic', review)
    response = launch(env)
    assert response.status_code == 200, response.text
    tid = response.json()['id']
    assert tasks.run_one(tid)
    stored = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert stored['status'] == 'budget_blocked', stored['error']
    first = deepcopy(db.loads(stored['result'])['paper_sections'][0])
    writing_flow.save_settings(env['pid'], {'semantic_review': True, 'max_revision_rounds': 2})
    monkeypatch.setattr(tasks, 'model_call', original_call)
    resumed = env['client'].post(f'/api/v1/projects/{env["pid"]}/paper-writing/{tid}/resume', json={})
    assert resumed.status_code == 200, resumed.text
    assert tasks.run_one(tid)
    stored = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert stored['status'] == 'succeeded', stored['error']
    assert first in db.loads(stored['result'])['paper_sections']
    assert len(env['calls']) == 5
    assert observed_policy and not any(observed_policy)
