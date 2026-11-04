"""Task-slot locking and immutable block reuse on isolated synthetic ledgers."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from fastapi import HTTPException

from test_workbench import client
from test_writing_recovery_content_cache import setup, legacy_run
from app import ai_cache_policy, ai_request_cache as cache, db, provider, security, tasks, writing_flow


SYSTEM = 'verify exact inputs'


@pytest.fixture(autouse=True)
def isolated_blocks(monkeypatch):
    monkeypatch.setattr(cache, '_BLOCKS', OrderedDict())
    monkeypatch.setattr(cache, '_BLOCK_BYTES', 0)
    monkeypatch.setattr(cache, '_SLOTS', {})


def invoke(task, context, manifest, index=8):
    return writing_flow._cached_call(task, SYSTEM, context, manifest, stage='semantic_review', index=index)


def save_response(task, manifest, status='completed', text='{"summary":"complete isolated response"}'):
    identity = db.uid()
    meta = {**deepcopy(manifest), 'provider': provider.config()}
    if status == 'completed':meta['output_blob'] = security.put_blob(text.encode())
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,cost,reserved,created_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (identity, task['id'], task['project_id'], status,
               meta['provider']['model'], meta['provider']['protocol'], db.dumps(meta), '{}', 0., 0., db.now()))
    return {'text': text, 'run_id': identity}


def test_same_slot_concurrent_identical_inputs_send_once_and_share_saved_response(client, monkeypatch):
    task, context, manifest, _ = setup(client)
    entered = threading.Event(); release = threading.Event(); duplicate = threading.Event(); calls = []
    def send(parent, system, user, meta, images=None):
        calls.append(1)
        if len(calls) > 1:duplicate.set()
        entered.set()
        assert release.wait(5)
        return save_response(parent, meta)
    monkeypatch.setattr(tasks, 'model_call', send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, task, context, manifest)
        assert entered.wait(2)
        second = pool.submit(invoke, task, context, manifest)
        try:assert not duplicate.wait(.15)
        finally:release.set()
        initial, reused = first.result(timeout=5), second.result(timeout=5)
    assert calls == [1] and initial['run_id'] == reused['run_id'] and reused['cache_hit']
    assert len(db.rows('SELECT id FROM generation_runs WHERE task_id=?', (task['id'],))) == 1
    assert cache._SLOTS == {}


def test_different_inputs_for_one_slot_are_serialized_and_do_not_reuse_response(client, monkeypatch):
    task, context, manifest, _ = setup(client)
    changed = {**context, 'question': 'a different logical request'}
    entered = threading.Event(); release = threading.Event(); overlap = threading.Event(); calls = []
    def send(parent, system, user, meta, images=None):
        calls.append(db.loads(user)['question'])
        if len(calls) == 1:
            entered.set(); assert release.wait(5)
        else:
            if not release.is_set():overlap.set()
        return save_response(parent, meta)
    monkeypatch.setattr(tasks, 'model_call', send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, task, context, manifest)
        assert entered.wait(2)
        second = pool.submit(invoke, task, changed, manifest)
        try:assert not overlap.wait(.15)
        finally:release.set()
        assert first.result(timeout=5)['run_id'] != second.result(timeout=5)['run_id']
    assert calls == [context['question'], changed['question']]


@pytest.mark.parametrize('status', ['pending', 'sent', 'outcome_unknown', 'completed_invalid'])
def test_unresolved_slot_blocks_changed_input_before_content_comparison(client, monkeypatch, status):
    task, context, manifest, cfg = setup(client)
    legacy_run(task, context, manifest, cfg, status=status)
    monkeypatch.setattr(tasks, 'model_call', lambda *a, **kw: pytest.fail('Unresolved slot was replayed'))
    with pytest.raises(provider.ProviderError) as captured:
        invoke(task, {**context, 'question': 'changed input cannot authorize replay'}, manifest)
    assert captured.value.unknown


def test_waiter_rechecks_revoked_permission_after_another_thread_finishes(client, monkeypatch):
    task, context, manifest, _ = setup(client)
    entered = threading.Event(); release = threading.Event(); calls = []
    def send(parent, system, user, meta, images=None):
        calls.append(1); entered.set(); assert release.wait(5)
        return save_response(parent, meta)
    monkeypatch.setattr(tasks, 'model_call', send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, task, context, manifest)
        assert entered.wait(2)
        second = pool.submit(invoke, task, context, manifest)
        db.execute("UPDATE files SET egress='local_only' WHERE id=?", (manifest['file_ids'][0],))
        release.set(); first.result(timeout=5)
        with pytest.raises(ValueError, match='禁止外发'):second.result(timeout=5)
    assert calls == [1]


def test_other_slots_can_send_while_one_slot_is_waiting(client, monkeypatch):
    task, context, manifest, _ = setup(client)
    entered = threading.Event(); release = threading.Event()
    def send(parent, system, user, meta, images=None):
        if meta['writing_unit_index'] == 8:
            entered.set(); assert release.wait(5)
        return save_response(parent, meta)
    monkeypatch.setattr(tasks, 'model_call', send)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(invoke, task, context, manifest)
        assert entered.wait(2)
        try:
            other = pool.submit(invoke, task, context, manifest, 16)
            assert other.result(timeout=2)['run_id']
        finally:release.set()
        first.result(timeout=5)


def test_equal_inputs_in_different_tasks_never_share_completed_response(client, monkeypatch):
    task, context, manifest, _ = setup(client)
    other = tasks.enqueue(task['project_id'], 'provider_probe', {})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?", (other['id'],))
    other = db.one('SELECT * FROM tasks WHERE id=?', (other['id'],))
    calls = []
    def send(parent, system, user, meta, images=None):
        calls.append(parent['id']); return save_response(parent, meta)
    monkeypatch.setattr(tasks, 'model_call', send)
    assert invoke(task, context, manifest)['run_id'] != invoke(other, context, manifest)['run_id']
    assert calls == [task['id'], other['id']]


@pytest.mark.parametrize('change', ['model_view_version', 'generation_intent_revision', 'endpoint'])
def test_recovery_key_versions_invalidate_completed_output_but_endpoint_changes_do_not(client, monkeypatch, change):
    task, context, manifest, cfg = setup(client)
    calls = []
    def send(parent, system, user, meta, images=None):
        calls.append(1); return save_response(parent, meta)
    monkeypatch.setattr(tasks, 'model_call', send)
    first = invoke(task, context, manifest)
    if change == 'endpoint':
        db.set_setting('provider', {**cfg, 'base_url': 'https://new-recovery.example/v1',
                                   'model': 'changed-endpoint-model'})
    else:
        payload = db.loads(task['payload'], {})
        payload[change] = 'writing-model-view.changed' if change == 'model_view_version' else 1
        db.execute('UPDATE tasks SET payload=? WHERE id=?', (db.dumps(payload), task['id']))
        task = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    second = invoke(task, context, manifest)
    if change == 'endpoint':
        assert second['run_id'] == first['run_id'] and second['cache_hit'] and calls == [1]
    else:
        assert second['run_id'] != first['run_id'] and not second.get('cache_hit') and calls == [1, 1]
    expected = 1 if change == 'endpoint' else 2
    assert len(db.rows('SELECT id FROM generation_runs WHERE task_id=?', (task['id'],))) == expected


def test_lock_timeout_leaves_no_waiter_and_next_owner_can_acquire(client):
    task, _, manifest, _ = setup(client)
    def wait():
        with cache.singleflight(task, manifest, 'semantic_review', 8, timeout=.03):
            pytest.fail('Timed-out waiter entered the slot')
    with cache.singleflight(task, manifest, 'semantic_review', 8):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(HTTPException) as captured:pool.submit(wait).result(timeout=2)
        assert captured.value.status_code == 409 and captured.value.detail['code'] == 'AI_REQUEST_IN_PROGRESS'
    assert cache._SLOTS == {}
    with cache.singleflight(task, manifest, 'semantic_review', 8, timeout=0):pass


def test_process_file_lock_blocks_same_slot_without_starting_application(client):
    task, _, manifest, _ = setup(client)
    identity = cache.slot_key(task, manifest, 'semantic_review', 8)
    path = Path(db.DB_PATH).resolve().parent / 'tmp/ai-request-locks' / (identity + '.lock')
    root = Path(__file__).resolve().parents[1]
    code = "import sys;sys.path[:0]=sys.argv[2:];from filelock import FileLock,Timeout\ntry:\n with FileLock(sys.argv[1]).acquire(timeout=.05):print('acquired')\nexcept Timeout:print('blocked')"
    with cache.singleflight(task, manifest, 'semantic_review', 8):
        result = subprocess.run([sys.executable, '-c', code, str(path),
            str(root / '.runtime/python-deps'), str(root / '.runtime/advanced-deps')],
            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and result.stdout.strip() == 'blocked'


def test_slot_identity_is_stable_across_content_attempt_and_layout_but_isolated_by_scope(client, monkeypatch, tmp_path):
    task, _, manifest, _ = setup(client)
    identity = cache.slot_key(task, manifest, 'semantic_review', 8)
    changed = {**task, 'attempt': task['attempt'] + 1, 'payload': db.dumps({'prompt_layout_version': 'cache_v2'})}
    assert cache.slot_key(changed, {**manifest, 'request_options': {'seed': 3}}, 'semantic_review', 8) == identity
    assert cache.slot_key(task, {**manifest, 'section_id': 'other-section'}, 'semantic_review', 8) != identity
    assert cache.slot_key(task, manifest, 'draft', 8) != identity
    assert cache.slot_key(task, manifest, 'semantic_review', 9) != identity
    assert cache.slot_key({**task, 'project_id': 'another-project'}, manifest, 'semantic_review', 8) != identity
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'another.sqlite3')
    assert cache.slot_key(task, manifest, 'semantic_review', 8) != identity


def test_block_lru_returns_deep_copies_and_uses_canonical_keys(client):
    original = {'rows': [{'text': 'immutable content'}]}; calls = []
    def build():calls.append(1); return original
    first, hit = cache.stable_block({'task': 'task', 'version': 1}, build)
    assert not hit
    first['rows'][0]['text'] = 'changed caller value'
    original['rows'][0]['text'] = 'changed builder value'
    second, hit = cache.stable_block({'version': 1, 'task': 'task'}, build)
    assert hit and second['rows'][0]['text'] == 'immutable content' and calls == [1]


def test_block_lru_evicts_oldest_entry_and_updates_recency(client, monkeypatch):
    monkeypatch.setattr(cache, 'MAX_BLOCKS', 2)
    for key in ('a', 'b'):cache.stable_block(key, lambda: {'value': 'content'})
    assert cache.stable_block('a', lambda: pytest.fail('Expected warm a'))[1]
    cache.stable_block('c', lambda: {'value': 'third'})
    assert cache.stable_block('a', lambda: pytest.fail('Recently read a was evicted'))[1]
    assert not cache.stable_block('b', lambda: {'value': 'rebuilt b'})[1]
    assert len(cache._BLOCKS) == 2


def test_block_byte_budget_and_clear_reset_accounting(client, monkeypatch):
    monkeypatch.setattr(cache, 'MAX_BYTES', 20)
    for key in ('a', 'b', 'c'):cache.stable_block(key, lambda: '12345678')
    assert cache._BLOCK_BYTES <= 20 and len(cache._BLOCKS) == 2
    before = cache._BLOCK_BYTES
    assert not cache.stable_block('oversized', lambda: 'x' * 30)[1]
    assert cache._BLOCK_BYTES == before
    cache.clear_blocks()
    assert not cache._BLOCKS and cache._BLOCK_BYTES == 0


def test_block_database_namespace_never_reuses_another_database(client, monkeypatch, tmp_path):
    cache.stable_block('task-and-version', lambda: {'text': 'first database'})
    monkeypatch.setattr(ai_cache_policy, 'settings', lambda: {**ai_cache_policy.DEFAULTS})
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'another.sqlite3')
    value, hit = cache.stable_block('task-and-version', lambda: {'text': 'second database'})
    assert not hit and value == {'text': 'second database'}


def test_disabling_block_lru_builds_fresh_without_reading_warm_value(client):
    cache.stable_block('task-and-version', lambda: {'text': 'warm value'})
    ai_cache_policy.save_policy({'stable_block_lru_enabled': False})
    value, hit = cache.stable_block('task-and-version', lambda: {'text': 'fresh value'})
    assert not hit and value == {'text': 'fresh value'}


@pytest.mark.parametrize('disabled', ['stable_block_lru_enabled', 'task_recovery_v2_enabled'])
def test_disabling_optional_cache_never_skips_fresh_permission_validation(client, monkeypatch, disabled):
    task, context, manifest, _ = setup(client)
    calls = []
    def send(parent, system, user, meta, images=None):
        calls.append(1); return save_response(parent, meta)
    monkeypatch.setattr(tasks, 'model_call', send)
    invoke(task, context, manifest)
    ai_cache_policy.save_policy({disabled: False})
    db.execute("UPDATE files SET egress='local_only' WHERE id=?", (manifest['file_ids'][0],))
    with pytest.raises(ValueError, match='禁止外发'):invoke(task, context, manifest)
    assert calls == [1]


def test_lock_release_on_exception_does_not_poison_the_next_call(client):
    task, _, manifest, _ = setup(client)
    with pytest.raises(RuntimeError):
        with cache.singleflight(task, manifest, 'semantic_review', 8):raise RuntimeError('synthetic failure')
    assert cache._SLOTS == {}
    with cache.singleflight(task, manifest, 'semantic_review', 8, timeout=0):pass
