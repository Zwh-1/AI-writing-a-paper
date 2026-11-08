"""Evidence, coverage and paired-evaluation regressions; no remote model calls."""
from copy import deepcopy
import pytest
from test_workbench import client, project
from test_ai_runtime import live_task
from test_writing_flow_integration import writing_project, write
from app import db, tasks
from app.ai_contracts.tasks import compile_spec, assess, merge_point_reviews
from app.ai_contracts.evidence import select, support_view
from app.ai_runtime import evaluation, telemetry


def spec():
    return compile_spec('p', {'id': 's'}, {'writing_brief': {'points': [
        {'id': 'p1', 'text': '实际指标', 'required': True}]}}, {})


def reviewed():
    return {'mapping': {'status': 'checked'}, 'semantic': {'coverage_complete': True,
        'claims': [{'claim_id': 'c1', 'paragraph_index': 0, 'support': 'supported', 'quote_verified': True}],
        'point_reviews': [{'point_id': 'p1', 'status': 'complete', 'paragraph_indices': [0]}]}}


def test_verified_responsibility_requires_actual_claim_reviews():
    quality = reviewed()
    assert assess(spec(), quality, [{'text': '静态设计'}])['complete']
    quality['semantic']['claims'] = []
    result = assess(spec(), quality, [{'text': '静态设计'}])
    assert not result['complete'] and not result['obligations'][0]['checks']['claim_support']


@pytest.mark.parametrize('indices', [[True], [-1], [1], ['0'], None])
def test_invalid_paragraph_indices_cannot_verify_obligations(indices):
    quality = reviewed()
    quality['semantic']['point_reviews'][0]['paragraph_indices'] = indices
    assert not assess(spec(), quality, [{'text': '静态设计'}])['complete']


@pytest.mark.parametrize('statuses', [('missing', 'complete'), ('complete', 'missing'),
                                     ('missing', 'complete', 'complete')])
def test_disagreements_are_sticky_and_order_independent(statuses):
    quality = reviewed()
    quality['semantic']['point_reviews'] = [
        {'point_id': 'p1', 'status': state, 'paragraph_indices': [0]} for state in statuses]
    result = assess(spec(), quality, [{'text': '静态设计'}])
    assert not result['complete']
    assert result['obligations'][0]['review']['disputed'] is True
    assert merge_point_reviews(quality['semantic']['point_reviews'])['p1']['status'] == 'unknown'


def test_measurement_obligation_cannot_be_verified_by_static_literature():
    contract = spec()
    contract['obligations'][0]['evidence_requirements'] = [
        {'any_of': ['source'], 'measurement_required': True}]
    result = assess(contract, reviewed(), [{'text': '静态设计', 'source_ids': ['s']}])
    assert not result['complete']
    assert result['obligations'][0]['checks']['evidence_requirements'] is False


def test_unknown_acceptance_checks_and_missing_dependencies_are_not_passed():
    contract = spec()
    contract['obligations'][0]['acceptance_checks'].append('not_implemented')
    assert not assess(contract, reviewed(), [{'text': '静态设计'}])['complete']
    contract = spec()
    contract['obligations'][0]['depends_on'] = ['missing-claim']
    assert not assess(contract, reviewed(), [{'text': '静态设计'}])['complete']


def test_cyclic_obligations_remain_unverified():
    contract = spec()
    contract['obligations'][0]['depends_on'] = ['p1']
    assert not assess(contract, reviewed(), [{'text': '静态设计'}])['complete']


@pytest.mark.parametrize('field', ['fact_revisions', 'fact_versions', 'knowledge_unit_versions',
    'document_reading_versions', 'source_read_scopes', 'related_revisions'])
def test_contract_identity_tracks_each_immutable_dependency(field):
    args = ('p', {'id': 's'}, {'writing_brief': {'points': [{'id': 'p1', 'text': '范围'}]}})
    assert compile_spec(*args, {field: {'x': 1}})['hash'] != compile_spec(*args, {field: {'x': 2}})['hash']


@pytest.mark.parametrize('kind', ['test_run', 'data_result', 'run_result'])
def test_traceable_executed_results_rank_before_static_definitions(kind):
    measured = {'id': 'measured', 'type': 'evidence', 'kind': kind, 'text': '延迟',
                'payload': {'actual_execution': True, 'artifact_sha256': 'raw-record'}}
    static = {'id': 'static', 'type': 'evidence', 'kind': 'test_definition', 'text': '接口响应延迟统计'}
    selected = select('接口响应延迟统计', [static, measured], ['run_result'], limit=1)
    assert selected[0]['id'] == 'measured' and selected[0]['type_eligible']
    measured['payload'] = {}
    assert not select('延迟', [measured], ['run_result'])[0]['type_eligible']


@pytest.mark.parametrize('need,row', [
    ('test_definition', {'type': 'evidence', 'kind': 'test_definition'}),
    ('reference', {'type': 'source', 'reference_ids': ['r1']}),
    ('source', {'type': 'knowledge_unit'}), ('source', {'type': 'document_reading'})])
def test_requirement_taxonomy_uses_real_reading_and_evidence_types(need, row):
    selected = select('响应延迟', [{**row, 'id': 'actual', 'text': '响应延迟'}], [need])
    assert selected[0]['type_eligible']


def test_code_selected_without_false_argument_gap(client):
    from app.writing_argument import build
    pid = project(client)
    context = {'_ai_quality_version': 'evidence.obligations.v1', 'project_evidence': [
        {'id': 'e1', 'kind': 'code_observation', 'text': '关键流程'}]}
    brief = {'points': [{'id': 'p1', 'text': '关键流程'}], 'argument_checkpoints': [
        {'point_id': 'p1', 'needs': ['code'], 'evidence_fields': ['evidence_ids']}]}
    card = build(pid, {'id': 's'}, context, brief)['paragraph_cards'][0]
    assert card['candidate_sources'][0]['id'] == 'e1' and not card['gaps']


def paired_tasks(pid, *, second_manifest=None, second_payload=None, failed=False, unknown=False):
    ids = []
    manifest = {'section_id': 's', 'base_revision_id': 'rev', 'rule_package_id': 'rules',
                'evidence_versions': {'e': 'v1'}, 'fact_versions': {'f': 1}}
    for index in range(2):
        payload = {'section_id': 's', 'base_revision_id': 'rev', 'instruction': 'same'}
        if index == 1: payload.update(second_payload or {})
        task = tasks.enqueue(pid, 'write', payload)
        current = {**manifest, **(second_manifest or {})} if index == 1 else manifest
        result = {'manifest': current, 'text': '可以人工对照的真实候选'}
        if failed: result.pop('text')
        db.execute('UPDATE tasks SET status=?,result=? WHERE id=?',
            ('failed' if failed else 'succeeded', db.dumps(result), task['id']))
        identity = {'pool_used': 'pool'} if unknown else {'actual_provider': {
            'base_url': 'https://synthetic.invalid/v1', 'model': 'synthetic', 'protocol': 'openai', 'max_output_tokens':2048},
            'actual_provider_credential_hash': 'synthetic-hash'}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,created_at) '
            'VALUES(?,?,?,?,?,?,?,?)', (db.uid(), task['id'], pid, 'failed_before_send' if failed else 'completed',
                'synthetic', 'openai', db.dumps(identity), db.now()))
        ids.append(task['id'])
    return ids


def test_complete_matching_tasks_are_ready_for_human_comparison(client):
    pid = project(client)
    report = evaluation.compare(pid, {'task_ids': paired_tasks(pid)})
    assert report['paired_input_groups'][0]['ready_for_human_comparison']
    assert report['new_model_requests'] == 0


@pytest.mark.parametrize('field', ['fact_versions', 'fact_revisions', 'related_revisions',
    'knowledge_unit_versions', 'document_reading_versions', 'contract_version_id', 'writing_quality_policy'])
def test_changed_source_input_is_not_paired(client, field):
    pid = project(client)
    ids = paired_tasks(pid, second_manifest={field: {'different': 'v2'}})
    report = evaluation.compare(pid, {'task_ids': ids})
    assert len(report['paired_input_groups']) == 2
    assert not any(group['ready_for_human_comparison'] for group in report['paired_input_groups'])


def test_changed_generation_parameters_are_not_paired(client):
    pid = project(client)
    ids = paired_tasks(pid, second_payload={'target_node_ids': ['selected']})
    assert len(evaluation.compare(pid, {'task_ids': ids})['paired_input_groups']) == 2


@pytest.mark.parametrize('failed,unknown,reason', [(False, True, 'actual_provider_unknown'),
                                                (True, False, 'candidate_missing')])
def test_unusable_tasks_have_explicit_comparison_blockers(client, failed, unknown, reason):
    pid = project(client)
    ids = paired_tasks(pid, failed=failed, unknown=unknown)
    report = evaluation.compare(pid, {'task_ids': ids})
    group = report['paired_input_groups'][0]
    assert not group['ready_for_human_comparison'] and reason in group['comparison_blockers']


def test_physical_attempts_and_endpoint_switches_are_counted(client):
    pid = project(client)
    ids = paired_tasks(pid)
    run = db.one('SELECT * FROM generation_runs WHERE task_id=?', (ids[0],))
    stamp = db.now()
    db.execute('INSERT INTO request_intents VALUES(?,?,?,?,?,?,?,?,?,?)',
        ('intent', pid, ids[0], run['id'], 1, 'draft', 'hash', 'persisted', stamp, stamp))
    db.execute('INSERT INTO request_intent_runs VALUES(?,?)', ('intent', run['id']))
    for index in range(3):
        identity = {'base_url': f'https://endpoint-{index}.invalid', 'model': 'synthetic',
                    'protocol': 'openai', 'credential_hash': 'synthetic-hash'}
        db.execute('INSERT INTO provider_attempts(id,intent_id,ordinal,endpoint_id,identity,state,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?)', (f'a{index}', 'intent', index+1, str(index), db.dumps(identity),
                'known_rejected' if index < 2 else 'persisted', stamp, stamp))
        db.execute('INSERT INTO request_execution_events(intent_id,attempt_id,event_type,detail,created_at) '
                   'VALUES(?,?,?,?,?)', ('intent', f'a{index}', 'send_started', '{}', stamp))
    row = evaluation.compare(pid, {'task_ids': ids})['tasks'][0]
    assert row['requests'] == 1
    assert row['execution_counts']['logical_requests'] == 1
    assert row['execution_counts']['actual_sends'] == 3
    assert row['execution_counts']['retries'] == 2
    assert row['execution_counts']['endpoint_switches'] == 2
    assert row['execution_counts']['before_send_rejections'] == 0


def test_task_budget_filter_does_not_return_project_total(client, live_task):
    pid = live_task['project_id']
    other = tasks.enqueue(pid, 'support_check', {})
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,cost,created_at) '
        'VALUES(?,?,?,?,?,?,?,?,?)', (db.uid(), other['id'], pid, 'completed', 'synthetic', 'openai', '{}', .37, db.now()))
    response = client.get(f'/api/v1/ai-runtime/projects/{pid}?task_id={live_task["id"]}')
    assert response.status_code == 200
    assert response.json()['budget_occupancy'] == 0 and response.json()['budget_scope'] == 'task'
    assert client.get(f'/api/v1/ai-runtime/projects/{pid}').json()['budget_occupancy'] == pytest.approx(.37)


def test_reuse_and_failure_do_not_change_execution_latency(live_task):
    for _ in range(5): telemetry.record(live_task, 'draft', 'graph_node', 'completed', 1000, reused=False)
    for _ in range(6): telemetry.record(live_task, 'draft', 'graph_node', 'completed', 0, reused=True)
    telemetry.record(live_task, 'draft', 'graph_node', 'failed', 9999)
    row = telemetry.summary(live_task['project_id'])['stages'][0]
    assert row['p50_ms'] == 1000 and row['sample_count'] == 5 and row['reused'] == 6
    assert row['distributions']['failure']['p50_ms'] == 9999


def test_telemetry_window_is_explicit_and_reuse_only_has_no_execution_latency(live_task):
    for _ in range(3): telemetry.record(live_task, 'draft', 'graph_node', 'completed', 0, reused=True)
    report = telemetry.summary(live_task['project_id'], limit=2)
    assert report['total_events'] == 3 and report['events'] == 2 and report['truncated']
    assert report['stages'][0]['p50_ms'] is None


@pytest.mark.parametrize('state,relation', [('supported', 'supports'), ('contradicted', 'contradicts'), ('partial', 'qualifies')])
def test_all_displayed_evidence_relations_require_verified_quotes(state, relation):
    claim = {'claim_id': 'c1', 'paragraph_index': 0, 'text': '候选正文'}
    review = {'claim_id': 'c1', 'support': state, 'quote_verified': False}
    quality = {'semantic': {'claims': [review]}}
    assert support_view([], quality, [claim])['items'][0]['relation'] == 'unresolved'
    review['quote_verified'] = True
    assert support_view([], quality, [claim])['items'][0]['relation'] == relation


def test_actual_writer_emits_evidence_checked_obligations(writing_project):
    task, result = write(writing_project)
    report = result['writing_quality']['obligations']
    assert report['required_count'] > 0
    for row in report['obligations']:
        if row['status'] == 'verified':
            assert row['checks']['claim_support'] and row['checks']['evidence_requirements']
    assert len(db.rows('SELECT * FROM generation_runs WHERE task_id=?', (task['id'],))) == 3


def test_registered_execution_artifact_satisfies_measurement_responsibility(client):
    from app import security
    from app.writing_agent import evidence
    from app.writing_argument import build
    pid = project(client)
    sha = security.put_blob('实测响应延迟10毫秒。'.encode('utf-8'))
    db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)', ('run-artifact', pid, '实际执行记录', 'data', sha,
        db.dumps({'actual_execution': True, 'run_id': 'real-record', 'egress': 'selected_content'}), db.now()))
    item = evidence.sync_artifact(pid, 'run-artifact')
    contract = spec()
    contract['project_id'] = pid
    contract['obligations'][0]['evidence_requirements'] = [{'any_of': ['run_result'], 'measurement_required': True}]
    context = {'_ai_quality_version': 'evidence.obligations.v1', 'project_evidence': [item]}
    paragraph = {'text': '实测响应延迟10毫秒。', 'evidence_ids': [item['id']]}
    report = assess(contract, reviewed(), [paragraph], context=context, manifest={})
    assert report['complete'] and 'run_result' in report['obligations'][0]['bound_evidence_types']
    contract['obligations'][0]['claim_strength_cap'] = 'static'
    assert not assess(contract, reviewed(), [paragraph], context=context, manifest={})['complete']
    brief = {'points': [{'id': 'p1', 'text': '响应延迟'}], 'argument_checkpoints': [
        {'point_id': 'p1', 'needs': ['run_result'], 'evidence_fields': ['evidence_ids']}]}
    assert not build(pid, {'id': 's'}, context, brief)['paragraph_cards'][0]['gaps']


def test_semantic_batch_disagreement_survives_actual_stage_projection(writing_project, monkeypatch):
    from app import provider, writing_flow, writing_evidence_quality
    from test_writing_flow_safety import _running, _basis
    from test_writing_flow_integration import response
    env = writing_project
    task = _running(env)
    context, manifest = _basis(env)
    context['writing_brief'] = {'points': [{'id': 'p1', 'text': '说明模块职责', 'required': True}]}
    paragraphs = [{'text': f'模块职责与维护边界说明{i}。', 'source_ids': [context['sources'][0]['id']]} for i in range(9)]
    local = writing_evidence_quality.check_candidate(env['pid'], paragraphs, context, manifest)
    calls = []
    def transport(system, user, images=None):
        payload = db.loads(user)
        calls.append(payload)
        source = payload['actual_sources'][0]
        return response({'claim_reviews': [{'claim_id': claim['claim_id'], 'support': 'supported',
            'source_quotes': [{'source_id': source['id'], 'quote': source['text']}]} for claim in payload['claims']],
            'point_reviews': [{'point_id': 'p1', 'status': 'missing' if len(calls)==1 else 'complete',
                'paragraph_indices': [0], 'evidence_text': paragraphs[0]['text']}], 'findings': []})
    monkeypatch.setattr(provider, 'generate', transport)
    semantic = writing_flow._semantic(task, {'id': env['sid'], 'goal': ''}, paragraphs, context, manifest, local)
    assert len(calls) == 2 and semantic['coverage_complete']
    assert not semantic['task_coverage']['complete']
    assert semantic['point_reviews'][0]['status'] == 'unknown'
    contract = compile_spec(env['pid'], {'id': env['sid']}, context, manifest)
    assert not assess(contract, {'mapping': {'status': 'checked'}, 'semantic': semantic}, paragraphs)['complete']
