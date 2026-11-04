"""Only an exact returned draft may resume directly at its format repair."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_workbench import client  # noqa: F401
from test_paper_writing import manuscript, launch  # noqa: F401
from app import db, provider, security, tasks, writing_flow
from app import paper_writing as paper


RAW = '# 第一章 绪论\n\n这是未满足章节结构契约的原始草稿，不表示完成或核验通过。'


def returned_invalid_draft(e, monkeypatch):
    original_transport = provider.generate
    original_call = writing_flow._cached_call
    calls = []
    stopped = False

    def transport(system, user, images=None):
        payload = db.loads(user)
        calls.append(payload.get('section', {}).get('kind'))
        response = original_transport(system, user, images)
        if len(calls) == 1:
            response['text'] = RAW
        return response

    def capacity_stop(task, system, context, manifest, *, stage, index=0):
        nonlocal stopped
        if stage == 'candidate_format_repair' and not stopped:
            stopped = True
            raise HTTPException(422, {'code': 'WRITING_CONTEXT_BUDGET_EXCEEDED',
                                      'message': 'synthetic repair capacity stop'})
        return original_call(task, system, context, manifest, stage=stage, index=index)

    monkeypatch.setattr(provider, 'generate', transport)
    monkeypatch.setattr(writing_flow, '_cached_call', capacity_stop)
    response = launch(e)
    assert response.status_code == 200, response.text
    tid = response.json()['id']
    assert tasks.run_one()
    task = db.one('SELECT * FROM tasks WHERE id=?', (tid,))
    assert task['status'] == 'failed'
    saved = db.loads(task['result'])
    assert not saved['paper_sections']
    assert saved['invalid_output']['raw_text'] == RAW
    run = db.one('SELECT * FROM generation_runs WHERE task_id=?', (tid,))
    assert run['status'] == 'completed'
    assert len(calls) == 1
    return task, run, calls


def test_resume_reuses_returned_invalid_draft_and_only_sends_existing_repair(manuscript, monkeypatch):
    e = manuscript
    task, run, calls = returned_invalid_draft(e, monkeypatch)
    cached = writing_flow.completed_invalid_draft(task)
    assert cached['run_id'] == run['id'] and cached['text'] == RAW
    assert cached['format_repair_resume'] and not paper.uncheckpointed_runs(task)
    assert not db.rows('SELECT * FROM revisions')

    response = e['client'].post(
        f"/api/v1/projects/{e['pid']}/paper-writing/{task['id']}/resume", json={})
    assert response.status_code == 200, response.text
    queued = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert not db.loads(queued['payload']).get('acknowledged_run_ids')
    assert not db.loads(queued['result'])['invalid_output'].get('degraded')
    assert tasks.run_one()
    done = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert done['status'] == 'succeeded', done['error']
    output = db.loads(done['result'])
    assert output['complete'] and output['completed_sections'] == 5
    assert calls.count('introduction') == 2
    assert len(calls) == 6  # Returned invalid draft, its repair, four other chapters.
    assert run['id'] in output['completed_run_ids']
    assert not paper.uncheckpointed_runs(done)
    assert db.one('SELECT COUNT(*) n FROM generation_runs WHERE task_id=?', (task['id'],))['n'] == 6
    assert not any(item.get('degraded') for item in output['paper_sections'])
    assert not db.rows('SELECT * FROM revisions')


@pytest.mark.parametrize('change', [
    'pending', 'sent', 'outcome_unknown', 'completed_invalid', 'legacy_stage',
    'different_raw', 'valid_json', 'degraded', 'rejected_checkpoint', 'unit_mode',
])
def test_other_responses_keep_existing_remote_acknowledgement(manuscript, monkeypatch, change):
    e = manuscript
    task, run, _ = returned_invalid_draft(e, monkeypatch)
    meta = db.loads(run['manifest'])
    output = db.loads(task['result'])
    if change in ('pending', 'sent', 'outcome_unknown', 'completed_invalid'):
        db.execute('UPDATE generation_runs SET status=? WHERE id=?', (change, run['id']))
    elif change == 'legacy_stage':
        meta.pop('writing_stage')
        db.execute('UPDATE generation_runs SET manifest=? WHERE id=?', (db.dumps(meta), run['id']))
    elif change in ('different_raw', 'valid_json'):
        raw = RAW + '\n不同的本地文本' if change == 'different_raw' else db.dumps({'paragraphs': [{'text': '有效结构候选'}]})
        output['invalid_output']['raw_text'] = raw
        if change == 'valid_json':
            meta['output_blob'] = security.put_blob(raw.encode('utf-8'))
            db.execute('UPDATE generation_runs SET manifest=? WHERE id=?', (db.dumps(meta), run['id']))
    elif change == 'degraded':
        output['invalid_output']['degraded'] = True
    elif change == 'rejected_checkpoint':
        output['invalid_output']['removed_from_checkpoint'] = True
    elif change == 'unit_mode':
        sid = output['invalid_output']['section_id']
        db.execute('UPDATE sections SET target_words=5000 WHERE id=?', (sid,))
    db.execute('UPDATE tasks SET result=? WHERE id=?', (db.dumps(output), task['id']))
    fresh = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert writing_flow.completed_invalid_draft(fresh) is None
    assert run['id'] in {r['id'] for r in paper.uncheckpointed_runs(fresh)}


def test_returned_draft_does_not_hide_an_unresolved_repair(manuscript, monkeypatch):
    task, run, _ = returned_invalid_draft(manuscript, monkeypatch)
    unknown = db.uid()
    meta = deepcopy(db.loads(run['manifest']))
    meta['writing_stage'] = 'candidate_format_repair'
    meta['candidate_format_attempt'] = 2
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (unknown, task['id'], task['project_id'], 'outcome_unknown', 'fixture', 'openai', db.dumps(meta), db.now()))
    assert writing_flow.completed_invalid_draft(task)['run_id'] == run['id']
    assert {r['id'] for r in paper.uncheckpointed_runs(task)} == {unknown}
    path = f"/api/v1/projects/{task['project_id']}/paper-writing/{task['id']}/resume"
    response = manuscript['client'].post(path, json={})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'PAPER_REMOTE_ACK_REQUIRED'


@pytest.mark.parametrize('missing_link',[False,True])
def test_valid_returned_candidate_reused_only_when_its_sources_are_still_loaded(manuscript,monkeypatch,missing_link):
    task,run,_=returned_invalid_draft(manuscript,monkeypatch)
    meta=db.loads(run['manifest']);section=security.require_section(meta['section_id'])
    raw=db.dumps({'paragraphs':[{'text':'已经形成结构但仍待独立核验的正文。','evidence_ids':['observed']}],
                  'missing_materials':[]})
    meta.update(writing_stage='candidate_format_repair',candidate_format_attempt=2,
                output_blob=security.put_blob(raw.encode('utf-8')))
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) VALUES(?,?,?,?,?,?,?,?)',
               ('valid-repair',task['id'],task['project_id'],'completed','fixture','openai',db.dumps(meta),db.now()))
    context={'project_evidence':[] if missing_link else [{'id':'observed'}]}
    found=writing_flow.completed_candidate_response(task,section,context,meta,tasks.candidate_from_text)
    if missing_link:
        assert found is None
    else:
        assert found['text']==raw and found['run_id']=='valid-repair'
        assert found['unverified_candidate_resume']
    assert not db.loads(db.one('SELECT result FROM tasks WHERE id=?',(task['id'],))['result'])['paper_sections']
    assert not db.rows('SELECT * FROM revisions')
