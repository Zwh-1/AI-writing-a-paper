"""Whole-paper recovery can add real sources without republishing contracts."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from test_writing_agent import env
from test_workbench import file
from app import db,paper_writing,security,tasks,writing_live
from app.writing_agent import contracts,evidence,repository
from app.writing_agent.common import state,update_state


def extra_code(e):
    return evidence.create(e['pid'],{'kind':'code_observation','origin_type':'code',
        'origin_id':e['snapshot']['id'],'locator':{'path':'gate.ts','line_start':1,'line_end':1},
        'conditions':['这是静态源码读取，不代表测试已经执行。']})


def task_for(e,mapping,kind='write_paper',pid=None):
    return tasks.enqueue(pid or e['pid'],kind,{'recovery_evidence_ids_by_section':mapping})


def prepared(e,task,sid=None):
    return writing_live.prepare(e['pid'],security.require_section(sid or e['sid']),
                                '说明实际函数和源码条件',task['id'])


def test_real_range_is_required_and_frozen_without_publishing(env):
    e=env;added=extra_code(e);task=task_for(e,{e['sid']:[added['id'],added['id']]})
    before_head=deepcopy(db.one('SELECT * FROM writing_project_state WHERE project_id=?',(e['pid'],)))
    before_contract=db.one('SELECT content_json FROM section_contract_versions WHERE id=?',(e['contract']['id'],))['content_json']
    before_snapshot=paper_writing.frozen_state(e['pid'])
    payload,manifest,items=prepared(e,task)
    assert {item['id'] for item in items}=={e['eid'],added['id']}
    assert added['id'] not in payload['automatic_project_evidence_ids']
    actual=next(item for item in payload['project_evidence'] if item['id']==added['id'])
    assert actual['payload']['text']==added['payload']['text']
    assert actual['payload']['line_start']==1 and actual['payload']['line_end']==1
    assert actual['kind']=='code_observation'
    assert manifest['task_scoped_recovery_evidence_ids']==[added['id']]
    assert manifest['evidence_versions'][added['id']]==added['origin_version']
    assert manifest['permission_stamps'][added['id']]==evidence.permission_stamp(e['pid'],added['id'])
    assert db.one('SELECT * FROM writing_project_state WHERE project_id=?',(e['pid'],))==before_head
    assert db.one('SELECT content_json FROM section_contract_versions WHERE id=?',(e['contract']['id'],))['content_json']==before_contract
    assert paper_writing.frozen_state(e['pid'])==before_snapshot


def test_other_section_mapping_does_not_change_current_context(env):
    e=env;added=extra_code(e)
    task=tasks.enqueue(e['pid'],'write_paper',{})
    baseline=prepared(e,task)
    db.execute('UPDATE tasks SET payload=? WHERE id=?',
               (db.dumps({'recovery_evidence_ids_by_section':{'future-section':[added['id']]}}),task['id']))
    assert prepared(e,task)==baseline


@pytest.mark.parametrize('kind',['write','provider_probe'])
def test_other_task_kinds_cannot_select_recovery_evidence(env,kind):
    e=env;added=extra_code(e);task=task_for(e,{e['sid']:[added['id']]},kind=kind)
    payload,manifest,items=prepared(e,task)
    assert added['id'] not in {item['id'] for item in items}
    assert 'task_scoped_recovery_evidence_ids' not in manifest


def test_foreign_project_task_payload_is_not_read(env):
    e=env;added=extra_code(e)
    other=e['client'].post('/api/v1/projects',json={'title':'其他项目'}).json()['id']
    task=task_for(e,{e['sid']:[added['id']]},pid=other)
    _,manifest,items=prepared(e,task)
    assert added['id'] not in {item['id'] for item in items}
    assert 'task_scoped_recovery_evidence_ids' not in manifest


def test_recovery_evidence_cannot_come_from_another_project(env):
    e=env;other=e['client'].post('/api/v1/projects',json={'title':'其他项目'}).json()['id']
    foreign=evidence.create(other,{'kind':'author_statement','origin_type':'author',
                                   'text':'其他项目的说明，不能混入当前项目。'})
    task=task_for(e,{e['sid']:[foreign['id']]})
    with pytest.raises(HTTPException) as raised:prepared(e,task)
    assert raised.value.status_code==404


def test_recovery_permission_is_rechecked_after_an_earlier_prepare(env):
    e=env;added=extra_code(e);task=task_for(e,{e['sid']:[added['id']]})
    assert added['id'] in prepared(e,task)[1]['evidence_versions']
    db.execute("UPDATE repository_bindings SET egress_policy='local_only' WHERE id=?",(e['binding']['id'],))
    with pytest.raises(HTTPException) as raised:prepared(e,task)
    assert raised.value.status_code==403


def test_old_snapshot_recovery_range_is_rejected_not_silently_dropped(env):
    e=env;added=extra_code(e);task=task_for(e,{e['sid']:[added['id']]})
    current=repository.capture(e['pid'],e['binding']['id'])
    assert current['id']!=e['snapshot']['id']
    update_state(e['pid'],state(e['pid'])['revision'],target_snapshot_id=current['id'])
    with pytest.raises(HTTPException,match='当前快照'):prepared(e,task)


def test_changed_source_content_cannot_use_an_old_recovery_observation(env):
    e=env;material=file(e['client'],e['pid'],'原始资料明确限定为静态源码观察。')
    source=db.one('SELECT id FROM sources WHERE file_id=?',(material['id'],))
    item=evidence.create(e['pid'],{'kind':'material_observation','origin_type':'source','origin_id':source['id']})
    task=task_for(e,{e['sid']:[item['id']]})
    assert item['id'] in prepared(e,task)[1]['evidence_versions']
    db.execute('UPDATE sources SET text=text||? WHERE id=?',(' 内容已经变化。',source['id']))
    with pytest.raises(HTTPException,match='恢复证据已过期'):prepared(e,task)


@pytest.mark.parametrize('mapping',[None,[], 'source-id'])
def test_recovery_mapping_has_to_be_an_object(env,mapping):
    task=task_for(env,mapping)
    with pytest.raises(HTTPException) as raised:prepared(env,task)
    assert raised.value.status_code==422


@pytest.mark.parametrize('identities',['source-id',None,[True],[''],[' padded '],['x']*201])
def test_current_section_recovery_ids_have_to_be_a_bounded_id_list(env,identities):
    task=task_for(env,{env['sid']:identities})
    with pytest.raises(HTTPException) as raised:prepared(env,task)
    assert raised.value.status_code==422
