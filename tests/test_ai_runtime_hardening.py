"""Frozen quality policy, honest transport statistics and isolated reusable adapters."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event
import pytest
from fastapi import HTTPException
from test_workbench import client, project
from test_ai_runtime import live_task
from test_writing_flow_integration import writing_project, write
from test_ai_runtime_regressions import paired_tasks
from test_writing_agent import env as agent_env, start as start_agent
from app import db, tasks, writing_flow
from app.ai_runtime import policy, evaluation, health, parallel, gateway


def test_queued_and_resumed_tasks_keep_quality_values(client):
    pid = project(client)
    writing_flow.save_settings(pid, {'semantic_review': False, 'max_revision_rounds': 0})
    task = tasks.enqueue(pid, 'write', {'section_id': 's'})
    original = policy.frozen(task)
    writing_flow.save_settings(pid, {'semantic_review': True, 'max_revision_rounds': 2})
    assert writing_flow.task_settings(pid, task)['semantic_review'] is False
    assert writing_flow.task_settings(pid, task)['max_revision_rounds'] == 0
    db.execute("UPDATE tasks SET status='queued',attempt=2 WHERE id=?", (task['id'],))
    resumed = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert policy.frozen(resumed) == original
    assert writing_flow.task_settings(pid, resumed)['max_revision_rounds'] == 0
    assert writing_flow.task_settings(pid, tasks.enqueue(pid, 'write', {}))['max_revision_rounds'] == 2
    changed = writing_flow.task_settings(pid, task)
    changed['max_revision_rounds'] = 2
    assert writing_flow.task_settings(pid, task)['max_revision_rounds'] == 0


def test_internal_children_inherit_quality_snapshot(live_task):
    from app.task_transitions import ownership
    pid = live_task['project_id']
    original = policy.frozen(live_task)['writing_flow_policy']
    writing_flow.save_settings(pid, {'semantic_review': not original['semantic_review']})
    with ownership(live_task):
        child = tasks.enqueue(pid, 'studio', {'action': 'fixture'})
        assert writing_flow.task_settings(pid) == original
    assert policy.frozen(child)['writing_flow_policy'] == original


def test_old_tasks_and_their_children_do_not_fabricate_old_policy_values(live_task):
    from app.task_transitions import ownership
    body = db.loads(live_task['payload'])
    body['ai_runtime'] = dict(policy.frozen(live_task))
    body['ai_runtime'].pop('writing_flow_policy', None)
    db.execute('UPDATE tasks SET payload=? WHERE id=?', (db.dumps(body), live_task['id']))
    old = db.one('SELECT * FROM tasks WHERE id=?', (live_task['id'],))
    pid = old['project_id']
    writing_flow.save_settings(pid, {'semantic_review': False})
    assert writing_flow.task_settings(pid, old)['semantic_review'] is False
    with ownership(old):
        child = tasks.enqueue(pid, 'write', {})
    assert policy.frozen(child)['writing_flow_policy'] is None


@pytest.mark.parametrize('snapshot', [{}, {'unknown': True}, 'invalid'])
def test_invalid_quality_snapshot_rejected(client, snapshot):
    pid = project(client)
    with pytest.raises(HTTPException) as error:
        tasks.enqueue(pid, 'write', {'ai_runtime': {'writing_flow_policy': snapshot}})
    assert error.value.status_code == 422
    assert not db.rows('SELECT * FROM tasks WHERE project_id=?', (pid,))


def test_quality_snapshot_rejects_wrong_project(client, live_task):
    with pytest.raises(HTTPException) as error:
        writing_flow.task_settings(project(client), live_task)
    assert error.value.status_code == 404


def test_agent_session_retains_quality_and_historical_steps_stay_legacy(agent_env):
    from app.writing_agent import coordinator
    env = agent_env
    writing_flow.save_settings(env['pid'], {'semantic_review': False})
    session = start_agent(env)
    first = db.one('SELECT t.* FROM tasks t JOIN writing_steps s ON s.task_id=t.id WHERE s.session_id=?', (session['id'],))
    writing_flow.save_settings(env['pid'], {'semantic_review': True})
    step = coordinator.enqueue(session['id'], 'judgment')
    child = db.one('SELECT * FROM tasks WHERE id=?', (step['task_id'],))
    assert policy.frozen(child)['writing_flow_policy']['semantic_review'] is False
    body = db.loads(first['payload']); body['ai_runtime'].pop('writing_flow_policy', None)
    db.execute('UPDATE tasks SET payload=? WHERE id=?', (db.dumps(body), first['id']))
    old_step = coordinator.enqueue(session['id'], 'judgment_check')
    old_child = db.one('SELECT * FROM tasks WHERE id=?', (old_step['task_id'],))
    assert policy.frozen(old_child)['writing_flow_policy'] is None


def test_real_writer_uses_frozen_checks_after_settings_change(writing_project):
    env = writing_project
    writing_flow.save_settings(env['pid'], {'semantic_review': False, 'max_revision_rounds': 0})
    task = tasks.enqueue(env['pid'], 'write', {'section_id': env['sid'], 'base_revision_id': None, 'operation': 'generate'})
    writing_flow.save_settings(env['pid'], {'semantic_review': True})
    assert tasks.run_one(task['id'])
    stored = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    assert stored['status'] == 'succeeded', stored['error']
    result = db.loads(stored['result'])
    assert result['writing_quality']['semantic']['status'] == 'not_checked'
    assert len(env['sent']) == 1
    db.execute("UPDATE tasks SET status='queued' WHERE id=?", (task['id'],))
    assert tasks.run_one(task['id'])
    assert len(env['sent']) == 1


def test_absent_attempt_history_is_unknown_instead_of_zero(client):
    pid = project(client)
    report = evaluation.compare(pid, {'task_ids': paired_tasks(pid)})
    row = report['tasks'][0]['execution_counts']
    assert row['actual_sends'] is None and row['retries'] is None
    assert row['observed_send_starts'] == 0 and not row['attempt_coverage_complete']


def test_missing_model_output_limit_blocks_controlled_comparison(client):
    pid = project(client)
    ids = paired_tasks(pid)
    for row in db.rows('SELECT id,manifest FROM generation_runs WHERE project_id=?', (pid,)):
        manifest = db.loads(row['manifest']); manifest['actual_provider'].pop('max_output_tokens')
        db.execute('UPDATE generation_runs SET manifest=? WHERE id=?', (db.dumps(manifest), row['id']))
    report = evaluation.compare(pid, {'task_ids': ids})
    assert not report['paired_input_groups'][0]['ready_for_human_comparison']
    assert 'model_parameters_unknown' in report['paired_input_groups'][0]['comparison_blockers']


@pytest.mark.parametrize('kind,origin,payload,label', [
    ('data_result', 'fact', {'status': 'planned'}, 'fact_record'),
    ('data_result', 'artifact', {'status': 'planned'}, 'unverified_result'),
    ('data_result', 'artifact', {'actual_execution': True, 'artifact_sha256': 'raw'}, 'run_result'),
    ('test_run', 'artifact', {'status': 'planned'}, 'test_definition')])
def test_labels_and_selection_share_evidence_levels(kind, origin, payload, label):
    from app.ai_contracts.evidence import kinds
    from app.writing_evidence_quality import evidence_kind_label
    row = {'type': 'evidence', 'kind': kind, 'origin_type': origin, 'payload': payload}
    assert evidence_kind_label(row) == label
    if label in ('run_result', 'test_definition'):
        assert label in kinds(row)
    else:
        assert not {'run_result', 'test_definition'} & kinds(row)


def test_fact_with_execution_artifact_is_selected_as_measured(client):
    from app import security
    from app.writing_argument import build
    from app.writing_evidence_quality import bound_categories
    pid = project(client)
    sha = security.put_blob('响应延迟10毫秒'.encode())
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)', ('raw', pid, '执行', 'data', sha,
        db.dumps({'actual_execution': True, 'egress': 'selected_content'}), db.now()))
    fact = client.post(f'/api/v1/projects/{pid}/facts', json={'label': '延迟', 'statement': '响应延迟10毫秒',
        'status': 'measured', 'verification': 'verified', 'artifact_id': 'raw'}).json()
    context = {'_ai_quality_version': 'evidence.obligations.v1', 'facts': [fact]}
    brief = {'points': [{'id': 'p1', 'text': '响应延迟'}], 'argument_checkpoints': [
        {'point_id': 'p1', 'needs': ['run_result'], 'evidence_fields': ['fact_ids']}]}
    card = build(pid, {'id': 's'}, context, brief)['paragraph_cards'][0]
    assert card['candidate_sources'] and not card['gaps']
    assert 'run_result' in bound_categories(pid, {'fact_ids': [fact['id']]}, context)


def test_readiness_reports_broken_dependencies(client, live_task, monkeypatch):
    original = health.dependency
    monkeypatch.setattr(health, 'dependency', lambda module, name:
        {'available': False, 'error_type': 'ImportError'} if name=='StateGraph' else original(module, name))
    response = client.get(f'/api/v1/ai-runtime/projects/{live_task["project_id"]}')
    assert response.status_code == 200
    assert not response.json()['capabilities']['chapter_graph']
    assert not response.json()['health']['ready']


def test_readiness_does_not_create_or_modify_checkpoint(live_task):
    checkpoint = Path(db.DB_PATH).with_name('ai-graph-checkpoints.sqlite3')
    assert health.status()['checkpoint']['status'] == 'not_created' and not checkpoint.exists()
    checkpoint.write_bytes(b'invalid checkpoint')
    assert health.status()['checkpoint']['status'] == 'unreadable'
    assert checkpoint.read_bytes() == b'invalid checkpoint'


def test_readiness_accepts_real_graph_checkpoint_without_changing_it(live_task):
    from app.ai_graphs.chapter import run
    run(live_task, {'id': 'health-section'}, {'value': 1}, [('prepare', lambda state: state)], lambda state: None)
    checkpoint = Path(db.DB_PATH).with_name('ai-graph-checkpoints.sqlite3')
    before = checkpoint.read_bytes()
    report = health.status()
    assert report['checkpoint']['status'] == 'readable' and report['capabilities']['chapter_graph']
    assert not report['checkpoint']['write_verified']
    assert checkpoint.read_bytes() == before


def test_cached_gateway_does_not_capture_the_first_transport(live_task):
    shared = gateway._runnable()
    for marker in ('first', 'second'):
        result = gateway.invoke(live_task, 's', 'u', {}, None, lambda **values: {'text': marker})
        assert result['text'] == marker
    assert gateway._runnable() is shared


def test_cached_router_is_safe_for_independent_parallel_inputs(live_task):
    from app.ai_graphs.agent import route, compiled_router
    shared = compiled_router()
    cases = [('judgment', {'judgment': {'blocking_gaps': ['gap']}}, 'generate_unit'),
             ('draft', {}, 'generate_unit'), ('coherence', {}, 'revise_unit')]
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda case: route(live_task, *case), cases))
    assert [row['next_stage'] for row in results] == [None, 'annotation', 'regression']
    assert compiled_router() is shared


def test_parallel_failure_stops_queued_work_without_hiding_running_outcomes(live_task):
    task = deepcopy(live_task)
    body = db.loads(task['payload']); body['ai_runtime'] = dict(policy.frozen(task))
    body['ai_runtime']['max_parallel_checks'] = 2; task['payload'] = body
    started, failed = Event(), Event()
    def slow():
        started.set()
        assert failed.wait(10)
        return 'observed'
    def fail():
        assert started.wait(10)
        failed.set()
        raise ValueError('invalid schema')
    with pytest.raises(ValueError, match='invalid schema'):
        parallel.run(task, {'slow': slow, 'failure': fail,
                            'queued': lambda: pytest.fail('queued operation should not start after failure')})
