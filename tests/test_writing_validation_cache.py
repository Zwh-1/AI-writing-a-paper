"""Manifest hashing stays within one preflight and never caches permissions."""
import pytest
from fastapi import HTTPException
from test_workbench import client, project, section, file  # noqa: F401
from app import db, knowledge_units as units, security, tasks, provider, expense_evidence
from app.writing_agent import impact
from app.writing_agent.common import state


def manifest_fixture(client):
    pid = project(client)
    sid = section(client, pid)
    original = file(client, pid, '本研究限于单用户环境。\n\n并发操作尚未验证。\n\n不得扩大原文适用条件。')
    parsed = units.build(pid, original['id'])
    identities = [unit['id'] for unit in parsed['units']]
    assert len(identities) == 3
    current = state(pid)
    manifest = {'section_id':sid, 'remote':True,
        'core_version_id':current['core_version_id'],
        'target_snapshot_id':current['target_snapshot_id'],
        'rule_package_id':security.require_project(pid)['rule_package_id'],
        'contract_version_id':current['contracts'].get(sid),
        'knowledge_unit_versions':{unit['id']:unit['content_hash'] for unit in parsed['units']},
        'knowledge_read_scopes':{identities[0]:{'passage_ids':identities}}}
    return pid, original, manifest


def test_manifest_hashes_a_shared_original_once_per_preflight(client, monkeypatch):
    pid, original, manifest = manifest_fixture(client)
    hashes = []
    original_rows = db.rows
    def counted(sql, args=()):
        if sql.startswith('SELECT id,text,method,locator FROM sources WHERE file_id=?'):
            hashes.append(args[0])
        return original_rows(sql, args)
    monkeypatch.setattr(db, 'rows', counted)
    assert impact.check_manifest(pid, manifest) == []
    assert hashes == [original['id']]
    assert impact.check_manifest(pid, manifest) == []
    assert hashes == [original['id'], original['id']]


def test_next_manifest_check_rehashes_edits_even_inside_an_older_input_scope(client):
    pid, original, manifest = manifest_fixture(client)
    with units.cache_inputs():
        units.input_stamp(original)
        assert impact.check_manifest(pid, manifest) == []
        db.execute('UPDATE sources SET text=? WHERE file_id=?', ('原文条件已更新。', original['id']))
        conflicts = impact.check_manifest(pid, manifest)
    assert any(value.startswith('知识单元:') for value in conflicts)


def test_next_manifest_check_rechecks_revoked_permissions(client):
    pid, original, manifest = manifest_fixture(client)
    assert impact.check_manifest(pid, manifest) == []
    db.execute("UPDATE files SET egress='local_only' WHERE id=?", (original['id'],))
    conflicts = impact.check_manifest(pid, manifest)
    assert any(value.startswith('知识单元:') for value in conflicts)


def test_hash_reuse_does_not_reuse_unit_permissions_within_a_preflight(client, monkeypatch):
    pid, original, manifest = manifest_fixture(client)
    original_get = units._get_uncached
    reads = []
    def revoke_after_first_read(*args, **kwargs):
        item = original_get(*args, **kwargs)
        reads.append(args[1])
        if len(reads) == 1:
            db.execute("UPDATE files SET egress='local_only' WHERE id=?", (original['id'],))
        return item
    monkeypatch.setattr(units, '_get_uncached', revoke_after_first_read)
    conflicts = impact.check_manifest(pid, manifest)
    assert reads
    assert any(value.startswith('知识单元:') for value in conflicts)


def model_manifest(original, manifest):
    return {**manifest, 'file_ids':[original['id']],
        'knowledge_refs':[{'type':'unit', 'id':identity, 'version':version}
                          for identity,version in manifest['knowledge_unit_versions'].items()]}


def validate_model_phase(phase, pid, manifest):
    if phase == 'inputs':
        return tasks._validate_model_source_inputs(pid, manifest)
    return tasks._validate_model_presend({'id':'fixture-task', 'project_id':pid, 'kind':'validation_probe'}, manifest)


@pytest.mark.parametrize('phase', ['inputs', 'presend'])
def test_model_phase_hashes_each_original_once_and_closes_its_scope(client, monkeypatch, phase):
    pid, original, manifest = manifest_fixture(client)
    manifest = model_manifest(original, manifest)
    hashes = []
    original_rows = db.rows
    def counted(sql, args=()):
        if sql.startswith('SELECT id,text,method,locator FROM sources WHERE file_id=?'):
            hashes.append(args[0])
        return original_rows(sql, args)
    monkeypatch.setattr(db, 'rows', counted)
    for _ in range(2):
        validate_model_phase(phase, pid, manifest)
        assert units._INPUT_CACHE.get() is None and db._READ_SESSION.get() is None
    assert hashes == [original['id'], original['id']]


@pytest.mark.parametrize('phase', ['inputs', 'presend'])
@pytest.mark.parametrize('change', ['content', 'permission'])
def test_model_phase_rechecks_changes_after_its_previous_call(client, phase, change):
    pid, original, manifest = manifest_fixture(client)
    manifest = model_manifest(original, manifest)
    with units.cache_inputs():
        units.input_stamp(original)
        validate_model_phase(phase, pid, manifest)
        if change == 'content':
            db.execute('UPDATE sources SET text=? WHERE file_id=?', ('原文条件已更新。', original['id']))
        else:
            db.execute("UPDATE files SET egress='local_only' WHERE id=?", (original['id'],))
        with pytest.raises(HTTPException):
            validate_model_phase(phase, pid, manifest)


@pytest.mark.parametrize('change', [None, 'content', 'permission'])
def test_metered_validation_ends_before_budget_events_and_transport_and_rechecks_after_budget(client, monkeypatch, change):
    pid, original, manifest = manifest_fixture(client)
    manifest = model_manifest(original, manifest)
    monkeypatch.setattr(provider, 'get_key', lambda:'fixture-validation-key')
    db.set_setting('provider', {**provider.config(), 'model':'fixture-validation',
        'input_price':1., 'output_price':2., 'max_attempts':1})
    original_preflight = expense_evidence.preflight
    original_event = tasks.event
    calls = []
    def scope_closed():
        assert units._INPUT_CACHE.get() is None and db._READ_SESSION.get() is None
        assert units._READ_CACHE.get() is None
    def budget_gate(*args, **kwargs):
        scope_closed()
        result = original_preflight(*args, **kwargs)
        if change == 'content':
            db.execute('UPDATE sources SET text=? WHERE file_id=?', ('预算校验期间原文条件已更新。', original['id']))
        elif change == 'permission':
            db.execute("UPDATE files SET egress='local_only' WHERE id=?", (original['id'],))
        return result
    def event(*args, **kwargs):
        scope_closed()
        return original_event(*args, **kwargs)
    def transport(*args, **kwargs):
        scope_closed()
        calls.append(True)
        return {'text':'fixture', 'usage':{}, 'cost':0.00001,
                'remote_id':'fixture', 'request_hash':'fixture'}
    monkeypatch.setattr(expense_evidence, 'preflight', budget_gate)
    monkeypatch.setattr(tasks, 'event', event)
    monkeypatch.setattr(provider, 'generate', transport)
    task = tasks.enqueue(pid, 'validation_probe', {})
    if change:
        with pytest.raises(HTTPException):
            tasks._model_call(task, 'fixture-system', 'fixture-input', manifest)
        assert not calls
    else:
        tasks._model_call(task, 'fixture-system', 'fixture-input', manifest)
        assert calls == [True]
    run = db.one('SELECT status FROM generation_runs WHERE task_id=?', (task['id'],))
    assert run['status'] == ('rejected' if change else 'completed')
    assert provider._CLIENT_REQUEST_ID.get() is None
    scope_closed()
