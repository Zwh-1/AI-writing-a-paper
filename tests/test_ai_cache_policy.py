"""Frozen new-task layouts and reversible controls on isolated workbench data."""
import pytest
from test_workbench import client, project
from app import ai_cache_policy as policy, db, prompt_layout, tasks


def test_new_writers_freeze_layout_and_old_payloads_remain_legacy(client):
    pid=project(client)
    first=tasks.enqueue(pid,'write',{'section_id':'example'})
    frozen=db.loads(first['payload'])
    assert frozen['prompt_layout_version']=='cache_v2'
    assert frozen['model_view_version']=='writing-model-view.v2'
    old={**first,'payload':db.dumps({'section_id':'example'})}
    assert prompt_layout.version(old)=='legacy'
    response=client.put('/api/v1/ai-cache-policy',json={'new_task_prompt_layout':'legacy'})
    assert response.status_code==200
    assert prompt_layout.version(db.one('SELECT * FROM tasks WHERE id=?',(first['id'],)))=='cache_v2'
    second=tasks.enqueue(pid,'write',{'section_id':'example'})
    assert prompt_layout.version(second)=='legacy'
    assert prompt_layout.version(old)=='legacy'


def test_idempotent_recovery_does_not_upgrade_layout_after_default_changes(client):
    pid=project(client)
    client.put('/api/v1/ai-cache-policy',json={'new_task_prompt_layout':'legacy'})
    first=tasks.enqueue(pid,'write',{'instruction':'same request'},key='same-operation')
    client.put('/api/v1/ai-cache-policy',json={'new_task_prompt_layout':'cache_v2'})
    resumed=tasks.enqueue(pid,'write',{'instruction':'same request'},key='same-operation')
    assert resumed['id']==first['id'] and prompt_layout.version(resumed)=='legacy'


@pytest.mark.parametrize('values',[{}, {'unsupported':True}, {'unit_matrix_cache_enabled':1},
    {'new_task_prompt_layout':'future'}, {'provider_cache_hints_enabled':True}])
def test_policy_rejects_unknown_or_unsafe_settings_without_saving(client,values):
    before=client.get('/api/v1/ai-cache-policy').json()
    assert client.put('/api/v1/ai-cache-policy',json=values).status_code==422
    assert policy.settings()==before and db.setting('ai_cache_policy') is None


def test_safe_rollback_controls_leave_tasks_and_ledger_unchanged(client):
    pid=project(client);task=tasks.enqueue(pid,'write',{})
    response=client.put('/api/v1/ai-cache-policy',json={
        'stable_block_lru_enabled':False,'task_recovery_v2_enabled':False,
        'unit_matrix_cache_enabled':False,'new_task_prompt_layout':'legacy'})
    assert response.status_code==200
    assert prompt_layout.version(db.one('SELECT * FROM tasks WHERE id=?',(task['id'],)))=='cache_v2'
    assert not db.rows('SELECT id FROM generation_runs')
    assert client.put('/api/v1/ai-cache-policy',json={'writing_singleflight_enabled':False}).status_code==409
    assert policy.settings()['writing_singleflight_enabled']


def test_nonwriting_tasks_keep_payload_and_legacy_transport_contract(client):
    pid=project(client);payload={'question':'exact synthetic request'}
    task=tasks.enqueue(pid,'provider_probe',payload)
    assert db.loads(task['payload'])==payload and prompt_layout.version(task)=='legacy'


def test_explicit_legacy_for_new_task_and_bad_version_rejected(client):
    pid=project(client)
    assert prompt_layout.version(tasks.enqueue(pid,'write',{'prompt_layout_version':'legacy'}))=='legacy'
    with pytest.raises(Exception):
        tasks.enqueue(pid,'write',{'prompt_layout_version':'unsupported'})
