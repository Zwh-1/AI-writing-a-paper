"""A known safe failure gets five opportunities, within the same task budget."""
from datetime import datetime, timezone

import pytest

from test_workbench import client, project
from app import db, official_api_policy, provider, security, tasks


def setup(client,monkeypatch,limit=5):
    pid=project(client)
    db.set_setting('provider',{**provider.DEFAULT,'base_url':'https://retry.example/v1',
        'model':'retry-model','max_attempts':limit,'context_limit':32000,
        'max_output_tokens':1024,'input_price':0.,'output_price':0.})
    monkeypatch.setattr(provider,'get_key',lambda:'mock-model-key')
    task=tasks.enqueue(pid,'provider_probe',{})
    return pid,task


def failure(kind):
    error=provider.ProviderError('synthetic '+kind,retryable=kind=='rate_limit',retry_after=0)
    if kind=='agent_connect':
        error.failure_phase='connect_before_http'
        error.transport_error_type='ConnectTimeout'
    return error


@pytest.mark.parametrize('kind',['agent_connect','rate_limit'])
def test_fifth_opportunity_succeeds_and_failed_attempts_are_preserved(client,monkeypatch,kind):
    pid,task=setup(client,monkeypatch);calls=[]
    def generate(*args,**kwargs):
        calls.append(1)
        if len(calls)<5:raise failure(kind)
        return {'text':'{"ok":true}','usage':{},'cost':0.,'remote_id':'fifth-response','request_hash':'mock'}
    monkeypatch.setattr(provider,'generate',generate)
    assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='succeeded',saved['error']
    assert len(calls)==5
    runs=db.rows('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at',(task['id'],))
    assert len(runs)==5 and runs[-1]['status']=='completed'
    assert all(row['status']==('failed_before_send' if kind=='agent_connect' else 'rejected') for row in runs[:-1])
    assert db.loads(runs[-1]['manifest'])['automatic_retry_index']==4


@pytest.mark.parametrize('kind',['agent_connect','rate_limit'])
def test_five_failed_opportunities_stop_without_a_sixth(client,monkeypatch,kind):
    pid,task=setup(client,monkeypatch);calls=[]
    def generate(*args,**kwargs):calls.append(1);raise failure(kind)
    monkeypatch.setattr(provider,'generate',generate)
    assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='failed' and len(calls)==5
    assert '5/5' in saved['error']
    assert db.one('SELECT COUNT(*) n FROM generation_runs WHERE task_id=?',(task['id'],))['n']==5


def test_unknown_remote_execution_is_kept_without_automatic_replay(client,monkeypatch):
    pid,task=setup(client,monkeypatch);calls=[]
    def generate(*args,**kwargs):calls.append(1);raise provider.ProviderError('unknown execution',unknown=True,retryable=True)
    monkeypatch.setattr(provider,'generate',generate)
    assert tasks.run_one()
    assert len(calls)==1
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='outcome_unknown'


def test_explicit_one_opportunity_is_still_respected(client,monkeypatch):
    pid,task=setup(client,monkeypatch,limit=1);calls=[]
    def generate(*args,**kwargs):calls.append(1);raise failure('agent_connect')
    monkeypatch.setattr(provider,'generate',generate)
    assert tasks.run_one()
    assert len(calls)==1


@pytest.mark.parametrize('general,code', [(False,official_api_policy.CODE),(True,official_api_policy.PEAK_CODE)])
def test_official_permission_and_peak_policies_never_consume_paid_opportunities(client,monkeypatch,general,code):
    pid,task=setup(client,monkeypatch)
    cfg=provider.config();cfg['base_url']='https://api.deepseek.com/v1';db.set_setting('provider',cfg)
    monkeypatch.setattr(official_api_policy,'current_time',lambda:datetime(2026,11,3,1,0,tzinfo=timezone.utc))
    official_api_policy.save({'allow_official_api':general})
    monkeypatch.setattr(provider,'generate',lambda *a,**k:pytest.fail('Blocked official request reached transport'))
    assert tasks.run_one()
    saved=db.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
    assert saved['status']=='failed' and code in saved['error']
    assert not db.rows('SELECT id FROM generation_runs WHERE task_id=?',(task['id'],))
