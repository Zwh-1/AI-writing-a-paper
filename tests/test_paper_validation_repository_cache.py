"""A readonly manuscript gate scans one repository once and stays fresh later."""
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException

from test_writing_agent import env
from app import db,documents,paper_writing as paper,security,tasks,writing_live
from app.writing_agent import repository
from app.writing_agent.common import digest,state


def saved_manuscript(e):
    pid=e['pid'];sections=[security.require_section(e['sid'])]
    for title in ('实现说明','结果范围'):
        response=e['client'].post(f'/api/v1/projects/{pid}/sections',json={
            'title':title,'kind':'implementation'})
        assert response.status_code==200,response.text
        sections.append(response.json())
    snapshot=paper.frozen_state(pid)
    task=tasks.enqueue(pid,'write_paper',{'snapshot':snapshot,
        'snapshot_hash':digest(snapshot),'generation_order':[s['id'] for s in sections],
        'instruction':'只描述冻结代码，不冒充运行结果。','budget_limit':10})
    head=state(pid);project=security.require_project(pid)
    items=[]
    for section in sections:
        manifest={'writing_state':{
            'project_id':pid,'section_id':section['id'],
            'core_version_id':head['core_version_id'],
            'target_snapshot_id':head['target_snapshot_id'],
            'contract_version_id':head['contracts'].get(section['id']),
            'rule_package_id':project['rule_package_id'],
            'live_input_versions':writing_live.versions(pid,task['id']),
            'writing_task_id':task['id'],'remote':True}}
        document=documents.text_document('这是冻结源码观察，尚不能证明实际测试通过。')
        items.append({'section_id':section['id'],'document':document,
            'text':documents.plain(document),'manifest':manifest})
    db.execute('UPDATE tasks SET result=? WHERE id=?',
        (db.dumps({'paper_sections':items}),task['id']))
    return task


def test_three_saved_chapters_share_one_scan_but_next_gate_is_fresh(env,monkeypatch):
    task=saved_manuscript(env);original=writing_live._repository_status;calls=[]
    def observed(pid,progress=None):
        calls.append(pid)
        return original(pid,progress)
    monkeypatch.setattr(writing_live,'_repository_status',observed)
    paper.validate_request(task)
    assert calls==[env['pid']]
    # A second public gate has a fresh filesystem observation, even in the same
    # process, so changed code cannot slip through a prior candidate cache hit.
    (env['root']/'gate.ts').write_text('export const changed = true;\n','utf-8')
    with pytest.raises(HTTPException):paper.validate_request(task)
    assert calls==[env['pid'],env['pid']]


@pytest.mark.parametrize('change',['edit','delete','new_file','new_directory_file'])
def test_later_validation_observes_filesystem_changes(env,change):
    with writing_live.repository_validation_scope():
        assert not writing_live.repository_status(env['pid'])['changed']
        assert not writing_live.repository_status(env['pid'])['changed']
    path=env['root']/'gate.ts'
    if change=='edit':path.write_text('export const changed = true;\n','utf-8')
    elif change=='delete':path.unlink()
    elif change=='new_file':(env['root']/'added.ts').write_text('export const added = 1;\n','utf-8')
    else:
        (env['root']/'nested').mkdir()
        (env['root']/'nested'/'added.ts').write_text('export const added = 1;\n','utf-8')
    with writing_live.repository_validation_scope():
        assert writing_live.repository_status(env['pid'])['changed']


def test_unscoped_repository_calls_are_always_fresh(env):
    assert not writing_live.repository_status(env['pid'])['changed']
    (env['root']/'new.ts').write_text('export const n = 1;\n','utf-8')
    assert writing_live.repository_status(env['pid'])['changed']


def test_scope_keeps_projects_separate_and_return_values_cannot_poison_cache(env,monkeypatch):
    other=env['client'].post('/api/v1/projects',json={'title':'另一项目'}).json()['id']
    original=writing_live._repository_status;calls=[]
    def observed(pid,progress=None):
        calls.append(pid);return original(pid,progress)
    monkeypatch.setattr(writing_live,'_repository_status',observed)
    with writing_live.repository_validation_scope():
        result=writing_live.repository_status(env['pid'])
        result['changed_files'].append('invented.ts');result['changed']=True
        assert writing_live.repository_status(other)['bound'] is False
        assert writing_live.repository_status(env['pid'])['changed'] is False
        assert writing_live.repository_status(env['pid'])['changed_files']==[]
    assert calls==[env['pid'],other]


def test_permission_revocation_still_checked_on_every_gate(env):
    task=saved_manuscript(env)
    paper.validate_request(task)
    repository.set_egress(env['pid'],env['binding']['id'],'local_only')
    with pytest.raises(HTTPException):paper.validate_request(task)


def test_corrupt_snapshot_path_escaping_parent_is_reported(env):
    outside=env['root'].parent/'outside.ts';outside.write_text('export const x = 1;\n','utf-8')
    stat=outside.stat();sha=security.put_blob(outside.read_bytes())
    db.execute('INSERT INTO repository_snapshot_files VALUES(?,?,?,?,?,?,?)',
        (env['snapshot']['id'],'../outside.ts',sha,'utf-8',None,'pending',
         db.dumps({'size':stat.st_size,'mtime_ns':stat.st_mtime_ns})))
    status=writing_live.repository_status(env['pid'])
    assert status['changed'] and '../outside.ts' in status['changed_files']


def test_file_reparse_and_parent_escape_remain_rejected(env,monkeypatch):
    original_resolve=Path.resolve;original_reparse=repository.reparse
    # Simulate a previously captured nested directory now redirected outside;
    # this does not require Windows symlink-creation privileges in the test.
    nested=env['root']/'nested';nested.mkdir();path=nested/'child.ts'
    path.write_text('export const x = 1;\n','utf-8')
    stat=path.stat();sha=security.put_blob(path.read_bytes())
    db.execute('INSERT INTO repository_snapshot_files VALUES(?,?,?,?,?,?,?)',
        (env['snapshot']['id'],'nested/child.ts',sha,'utf-8',None,'pending',
         db.dumps({'size':stat.st_size,'mtime_ns':stat.st_mtime_ns})))
    def resolve(path,*args,**kwargs):
        return original_resolve(env['root'].parent,*args,**kwargs) if path==nested else original_resolve(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'resolve',resolve)
        assert 'nested/child.ts' in writing_live.repository_status(env['pid'])['changed_files']
    def reparse(path):return path==env['root']/'gate.ts' or original_reparse(path)
    with monkeypatch.context() as patch:
        patch.setattr(repository,'reparse',reparse)
        assert 'gate.ts' in writing_live.repository_status(env['pid'])['changed_files']


def test_exception_does_not_leave_a_repository_cache_active(env,monkeypatch):
    original=writing_live._repository_status
    with pytest.raises(RuntimeError):
        with writing_live.repository_validation_scope():
            assert not writing_live.repository_status(env['pid'])['changed']
            raise RuntimeError('validation stopped')
    (env['root']/'gate.ts').unlink()
    assert writing_live.repository_status(env['pid'])['changed']
