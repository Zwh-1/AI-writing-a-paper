"""Shared resource checks are bounded to one source-validation observation."""
import pytest
from fastapi import HTTPException
from test_workbench import client,project,file
from app import db,resource_refs as refs,knowledge_lineage as lineage


def source(client):
    pid=project(client);original=file(client,pid)
    row=db.one('SELECT * FROM sources WHERE file_id=?',(original['id'],))
    assert row
    return pid,original,row


def count_source_reads(monkeypatch):
    original=db.one;calls=[]
    def one(sql,args=()):
        if sql.startswith('SELECT s.*,f.project_id,f.sha256,'):calls.append(args)
        return original(sql,args)
    monkeypatch.setattr(db,'one',one)
    return calls


def asset(pid,identity,upstream):
    db.execute('INSERT INTO artifacts(id,project_id,name,kind,metadata,created_at) VALUES(?,?,?,?,?,?)',
        (identity,pid,identity,'figure',db.dumps({'source_refs':upstream}),db.now()))


def test_duplicate_upstream_preserves_hash_and_only_reads_source_once(client,monkeypatch):
    pid,original,row=source(client)
    ref=refs.make(pid,'source',row['id'])
    asset(pid,'many',[ref]*80)
    expected=refs.version_of(pid,'asset','many')
    calls=count_source_reads(monkeypatch)
    with lineage.validation_scope():
        assert refs.version_of(pid,'asset','many')==expected
        assert lineage.version_of(pid,'asset','many')==expected
        assert refs.version_of(pid,'source',row['id'])==ref['version']
    assert len(calls)==1
    assert refs._VISITING.get()==frozenset() and lineage._CACHE.get() is None


def test_two_sibling_assets_share_complete_upstream_check(client,monkeypatch):
    pid,original,row=source(client);ref=refs.make(pid,'source',row['id'])
    asset(pid,'left',[ref]);asset(pid,'right',[ref])
    calls=count_source_reads(monkeypatch)
    with lineage.validation_scope():
        refs.version_of(pid,'asset','left');refs.version_of(pid,'asset','right')
    assert len(calls)==1


def test_new_scope_observes_source_edit_and_acl_revocation(client):
    pid,original,row=source(client)
    with lineage.validation_scope():before=refs.version_of(pid,'source',row['id'],True)
    db.execute('UPDATE sources SET text=? WHERE id=?',('new original source text',row['id']))
    with lineage.validation_scope():assert refs.version_of(pid,'source',row['id'],True)!=before
    client.patch('/api/v1/files/'+original['id'],json={'egress':'local_only'})
    with lineage.validation_scope():
        with pytest.raises(HTTPException) as error:refs.version_of(pid,'source',row['id'],True)
        assert error.value.status_code==403


def test_remote_permission_result_is_not_shared_with_local_access(client):
    pid,original,row=source(client)
    client.patch('/api/v1/files/'+original['id'],json={'egress':'local_only'})
    with lineage.validation_scope():
        assert refs.version_of(pid,'source',row['id'],False)
        with pytest.raises(HTTPException) as error:refs.version_of(pid,'source',row['id'],True)
        assert error.value.status_code==403


def test_project_identity_is_part_of_memo(client):
    pid,original,row=source(client);other=project(client,'another')
    with lineage.validation_scope():
        assert refs.version_of(pid,'source',row['id'])
        with pytest.raises(HTTPException):refs.version_of(other,'source',row['id'])


def test_direct_calls_are_fresh_without_explicit_validation_scope(client,monkeypatch):
    pid,original,row=source(client);calls=count_source_reads(monkeypatch)
    refs.version_of(pid,'source',row['id']);refs.version_of(pid,'source',row['id'])
    assert len(calls)==2


def test_warm_memo_does_not_bypass_explicit_cycle_guard(client):
    pid,original,row=source(client)
    with lineage.validation_scope():
        refs.version_of(pid,'source',row['id'])
        with pytest.raises(HTTPException,match='循环'):
            refs.version_of(pid,'source',row['id'],seen={('source',row['id'])})


def test_adapter_cycle_still_fails_and_releases_visiting_state(client):
    pid=project(client)
    asset(pid,'cycle-a',[{'type':'asset','id':'cycle-b','version':'old'}])
    asset(pid,'cycle-b',[{'type':'asset','id':'cycle-a','version':'old'}])
    with lineage.validation_scope():
        with pytest.raises(HTTPException,match='循环'):refs.version_of(pid,'asset','cycle-a')
        assert refs._VISITING.get()==frozenset()


def test_warm_composite_does_not_skip_an_explicit_upstream_ancestor(client):
    pid,original,row=source(client);ref=refs.make(pid,'source',row['id'])
    asset(pid,'parent',[ref])
    with lineage.validation_scope():
        assert refs.version_of(pid,'asset','parent')
        with pytest.raises(HTTPException,match='循环'):
            refs.version_of(pid,'asset','parent',seen={('source',row['id'])})


def test_explicit_fresh_nested_scope_rechecks_changed_source(client):
    pid,original,row=source(client)
    with lineage.validation_scope():
        first=refs.version_of(pid,'source',row['id'])
        db.execute('UPDATE sources SET text=? WHERE id=?',('changed during source-query test',row['id']))
        with lineage.validation_scope(fresh=True):
            assert refs.version_of(pid,'source',row['id'])!=first
