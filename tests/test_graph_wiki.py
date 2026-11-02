import pytest
from fastapi import HTTPException
from test_workbench import client, project, file, section
from app import db, tasks, model_install, knowledge_units
from app import knowledge_lineage as lineage, knowledge_compiler as compiler, knowledge_graph as graph, knowledge_wiki as wiki, knowledge_builds as builds

@pytest.fixture(autouse=True)
def no_remote_models(monkeypatch):
    monkeypatch.setattr(model_install,'status',lambda name:{'ready':False})

@pytest.fixture
def library(client):
    pid=project(client);source=file(client,pid,'方法仅适用于单用户环境。\n\n样本数为12，并发场景尚未验证。')
    parsed=knowledge_units.build(pid,source['id']);return client,pid,source,parsed

def build(pid,scope='literature'):
    task=builds.start(pid,{'mode':'literal','namespace':scope})
    return task,builds.process(task,db.loads(task['payload'],{}))

def test_build_is_traced_and_original_ids_preserved(library):
    c,pid,f,parsed=library;before=db.rows('SELECT id,text FROM sources ORDER BY id')
    task,result=build(pid)
    assert not result['failures'],result
    assert result['files'][0]['assertion_ids']
    from app.writing_agent.migrations import CURRENT
    assert db.one('PRAGMA user_version')['user_version']==CURRENT
    assert db.rows('SELECT id,text FROM sources ORDER BY id')==before
    assert not db.rows('PRAGMA foreign_key_check')
    page=wiki.search(pid,'单用户')['items'][0]
    assert page['current'] and page['blocks'][0]['sources']
    assert not db.rows('SELECT * FROM generation_runs')

def test_permissions_rechecked_after_build(library):
    c,pid,f,p=library;build(pid)
    assert wiki.search(pid,'单用户',remote=True)['items']
    c.patch('/api/v1/files/'+f['id'],json={'egress':'local_only'})
    assert not wiki.search(pid,'单用户',remote=True)['items']
    assert not graph.search(pid,'单用户',remote=True)['assertions']
    assert wiki.search(pid,'单用户')['items']

def test_original_change_invalidates_summaries(library):
    c,pid,f,p=library;build(pid)
    db.execute('UPDATE sources SET text=text||? WHERE file_id=?',('修订原文',f['id']))
    assert not wiki.search(pid,'单用户')['items']
    assert not graph.search(pid,'单用户')['assertions']

def test_edit_conflict_and_no_self_evidence(library):
    c,pid,f,p=library;build(pid)
    page=wiki.search(pid,'单用户')['items'][0];base=page['current_version_id']
    revision=wiki.revise(pid,page['id'],{'base_version_id':base,'blocks':[{'key':'author-note','kind':'note','text':'人工备注'}]})
    wiki.publish(pid,page['id'],{'version_id':revision['version']['id'],'base_version_id':base})
    with pytest.raises(HTTPException):wiki.revise(pid,page['id'],{'base_version_id':base,'blocks':[{'kind':'note','text':'旧版本覆盖'}]})
    with pytest.raises(HTTPException):wiki.revise(pid,page['id'],{'base_version_id':revision['version']['id'],'blocks':[{'kind':'fact','text':'无来源事实'}]})
