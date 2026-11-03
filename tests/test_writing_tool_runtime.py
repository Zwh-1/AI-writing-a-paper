"""Synthetic-only coverage for material channels and resumable investigation."""
import json
import pytest
from fastapi import HTTPException
from test_writing_agent import env
from app import db, documents, tasks, provider, writing_tool_runtime as runtime
from app.writing_agent import contracts, coordinator, tools, profiles, repository
from app.writing_agent.common import state, update_state, blob, unblob, digest


def pure_literature(env):
    head = state(env['pid'])
    update_state(env['pid'], head['revision'], target_snapshot_id=None)
    contract = contracts.save_contract(env['pid'], env['sid'],
        {'goal': '按实际文献论证方法和局限', 'evidence_ids': []}, state(env['pid'])['revision'])
    return coordinator.create(env['pid'], {
        'section_id': env['sid'], 'operation': 'generate_unit',
        'base_revision_id': db.one('SELECT current_revision_id FROM sections WHERE id=?', (env['sid'],))['current_revision_id'],
        'expected_project_state_revision': state(env['pid'])['revision'],
        'contract_version_id': contract['id'], 'profile_id': env['profile']['id']})


def running_task(env, **payload):
    task = tasks.enqueue(env['pid'], 'write', {'section_id': env['sid'], **payload})
    db.execute("UPDATE tasks SET status='running',attempt=1 WHERE id=?", (task['id'],))
    return db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))


def envelope(answer):
    return {'text': db.dumps(answer), 'tool_calls': [], 'message': None, 'items': []}


def test_pure_literature_session_and_noncode_cache_ignore_repository_scope(env):
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    revision = documents.save(section, documents.text_document('本段来自隔离测试正文。'), None)
    session = pure_literature(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    args = {'section_id': env['sid'], 'revision_id': revision['id']}
    first = tools.execute(env['pid'], step['id'], 'read_section', args, 'section-read', None)
    assert first['data']['text'] == '本段来自隔离测试正文。'
    repository.set_egress(env['pid'], env['binding']['id'], 'local_only')
    assert tools.execute(env['pid'], step['id'], 'read_section', args, 'section-read', None) == first
    assert not any(t['function']['name'] in tools.CODE_TOOLS for t in tools.schemas(include_code=False))
    with pytest.raises(HTTPException) as exc:
        tools.execute(env['pid'], step['id'], 'read_code',
            {'snapshot_id': env['snapshot']['id'], 'relative_path': 'gate.ts', 'line_start': 1, 'line_end': 1}, 'code', None)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        tools.execute(env['pid'], step['id'], 'get_evidence', {'evidence_id': env['eid']}, 'code-evidence', None)


def test_material_read_cache_revalidates_without_repeating_read(env, monkeypatch):
    from app import writing_retrieval
    session = pure_literature(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    calls = []
    monkeypatch.setattr(writing_retrieval, 'read', lambda *a, **kw: calls.append('read') or {'manifest': {}, 'schema': 'fixture'})
    monkeypatch.setattr(writing_retrieval, 'validate', lambda *a, **kw: calls.append('validate'))
    args = {'candidates_json': '[{"source_ref":{"type":"note","id":"synthetic"}}]', 'query': '隔离测试'}
    first = tools.execute(env['pid'], step['id'], 'read_writing_materials', args, 'read-once')
    assert tools.execute(env['pid'], step['id'], 'read_writing_materials', args, 'read-once') == first
    assert calls == ['read', 'validate']
    def revoked(*a, **kw):
        raise HTTPException(403, '权限已撤回')
    monkeypatch.setattr(writing_retrieval, 'validate', revoked)
    with pytest.raises(HTTPException):
        tools.execute(env['pid'], step['id'], 'read_writing_materials', args, 'read-once')
    assert calls.count('read') == 1


def test_tool_project_ownership_checked_before_cache(env):
    session = pure_literature(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    other = env['client'].post('/api/v1/projects', json={'title': '另一个隔离项目'}).json()['id']
    with pytest.raises(HTTPException) as exc:
        tools.execute(other, step['id'], 'list_literature_documents', {}, 'cross-project')
    assert exc.value.status_code == 404


def test_public_agent_launch_accepts_pure_literature_without_code_snapshot(env):
    head = state(env['pid'])
    update_state(env['pid'], head['revision'], target_snapshot_id=None)
    contracts.save_contract(env['pid'], env['sid'], {'goal': '综述实际文献及条件', 'evidence_ids': []}, state(env['pid'])['revision'])
    # Bootstrap must not select the synthetic fixture's old code baseline.
    db.execute("UPDATE repository_snapshots SET state='archived'")
    result = coordinator.start(env['pid'], {'section_id': env['sid'],
        'expected_project_state_revision': state(env['pid'])['revision']})
    assert result['target_snapshot_id'] is None and result['status'] == 'queued'
    assert db.one('SELECT COUNT(*) n FROM generation_runs')['n'] == 0


def test_shared_runtime_json_actions_checkpoint_and_replay(env, monkeypatch):
    pure_literature(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    revision = documents.save(section, documents.text_document('实际读取的隔离段落。'), None)
    task = running_task(env, budget_limit=3)
    calls = []
    from app.writing_agent import provider_adapter
    def call(*args, **kwargs):
        messages = args[6] if len(args) > 6 else kwargs.get('messages')
        calls.append(messages)
        if len(calls) == 1:
            return envelope({'action': 'tool', 'name': 'read_section',
                             'arguments': {'section_id': env['sid'], 'revision_id': revision['id']}})
        return envelope({'action': 'done', 'summary': '已读真实段落，仍未取得实验记录。'})
    monkeypatch.setattr(provider_adapter, 'call', call)
    result = runtime.collect(task, section, '核对本段上下文', max_rounds=3)
    assert result['status'] == 'completed' and len(result['tool_observations']) == 1
    assert result['context']['dynamic_materials'][0]['data']['text'] == '实际读取的隔离段落。'
    again = runtime.collect(task, section, '核对本段上下文', max_rounds=3)
    assert result == again and len(calls) == 2
    policy = db.loads(db.one('SELECT policy_json FROM writing_sessions WHERE id=?', (result['manifest']['writing_research_session_id'],))['policy_json'])
    assert policy['budget_limit'] == 3
    assert db.one('SELECT COUNT(*) n FROM writing_candidates')['n'] == 0
    assert db.one('SELECT COUNT(*) n FROM revisions')['n'] == 1
    assert not db.rows('PRAGMA foreign_key_check')


def test_shared_runtime_unknown_outcome_never_resubmitted(env, monkeypatch):
    pure_literature(env)
    task = running_task(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    calls = []
    from app.writing_agent import provider_adapter
    def unknown(*args, **kwargs):
        calls.append(1)
        raise provider.ProviderError('合成未知结果', unknown=True)
    monkeypatch.setattr(provider_adapter, 'call', unknown)
    for _ in range(2):
        with pytest.raises(provider.ProviderError) as exc:
            runtime.collect(task, section, '查找待补资料')
        assert exc.value.unknown
    assert len(calls) == 1
    assert db.one("SELECT COUNT(*) n FROM writing_steps WHERE state='outcome_unknown'")['n'] == 1


def test_scope_only_local_checkpoint_respects_parent_lease(env):
    task = running_task(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    scope = runtime.ensure_scope(task, section, 'generate', stage='unit_plan', input_key='v1')
    runtime.save_scope(task, scope, {'units': [{'id': 'u1', 'goal': '合成论证单元'}]})
    again = runtime.ensure_scope(task, section, 'generate', stage='unit_plan', input_key='v1')
    assert again['step_id'] == scope['step_id'] and again['output']['units'][0]['id'] == 'u1'
    db.execute('UPDATE tasks SET attempt=2 WHERE id=?', (task['id'],))
    with pytest.raises(HTTPException) as exc:
        runtime.save_scope(task, scope, {'units': []}, status='completed')
    assert exc.value.status_code == 409
    assert db.one('SELECT COUNT(*) n FROM generation_runs')['n'] == 0


def test_runtime_resumes_pending_action_without_second_model_call(env, monkeypatch):
    session = pure_literature(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    revision = documents.save(section, documents.text_document('可恢复读取。'), None)
    task = running_task(env)
    from app.writing_agent import provider_adapter
    model_calls = []
    def call(*args, **kwargs):
        model_calls.append(1)
        if len(model_calls) == 1:
            return envelope({'action': 'tool', 'name': 'read_section', 'arguments': {'section_id': env['sid'], 'revision_id': revision['id']}})
        return envelope({'action': 'done', 'summary': '恢复完成'})
    monkeypatch.setattr(provider_adapter, 'call', call)
    execute = tools.execute
    attempts = []
    def interrupt(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError('合成本地工具失败，模型响应已保存')
        return execute(*args, **kwargs)
    monkeypatch.setattr(tools, 'execute', interrupt)
    with pytest.raises(ValueError):
        runtime.collect(task, section, '恢复工具动作')
    result = runtime.collect(task, section, '恢复工具动作')
    assert result['status'] == 'completed' and len(model_calls) == 2 and len(attempts) == 2


def test_native_runtime_uses_parent_ledger_and_reuses_completed_response(env, monkeypatch):
    pure_literature(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    revision = documents.save(section, documents.text_document('原生工具读取的真实隔离段落。'), None)
    profile = profiles.create(env['pid'], {'role': 'draft', 'configuration': {'route': 'primary', 'tool_mode': 'native'}})
    task = running_task(env, budget_limit=1)
    calls = []
    def generate(system, user, images=None):
        frame = json.loads(user); calls.append(frame)
        if len(calls) == 1:
            args = {'section_id': env['sid'], 'revision_id': revision['id']}
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'native-read', 'type': 'function', 'function': {'name': 'read_section', 'arguments': db.dumps(args)}}]}
            answer = {'agent_transport': True, 'text': '', 'tool_calls': [{'id': 'native-read', 'name': 'read_section', 'arguments': db.dumps(args)}], 'message': message, 'items': [], 'finish': 'tool_calls'}
        else:
            assert any(message.get('role') == 'tool' and '原生工具读取' in message['content'] for message in frame['messages'])
            answer = {'agent_transport': True, 'text': '已读取实际原文。', 'tool_calls': [], 'message': None, 'items': [], 'finish': 'stop'}
        return {'text': db.dumps(answer), 'usage': {'input_tokens': 100, 'output_tokens': 50}, 'cost': 0,
                'remote_id': 'native-' + str(len(calls)), 'request_hash': digest([system, user])}
    monkeypatch.setattr(provider, 'generate', generate)
    result = runtime.collect(task, section, '读取段落原文', profile_id=profile['id'])
    assert len(calls) == 2 and calls[0]['tools']
    assert all(run['task_id'] == task['id'] for run in db.rows('SELECT * FROM generation_runs'))
    assert result['manifest']['related_revisions'][env['sid']] == revision['id']
    assert runtime.collect(task, section, '读取段落原文', profile_id=profile['id']) == result
    assert len(calls) == 2


def test_runtime_model_costs_count_against_parent_task_budget(env, monkeypatch):
    from app.summary_billing import BudgetExceeded
    pure_literature(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    revision = documents.save(section, documents.text_document('预算隔离测试。'), None)
    task = running_task(env, budget_limit=0.1)
    calls = []
    def generate(system, user, images=None):
        calls.append(1)
        answer = envelope({'action': 'tool', 'name': 'read_section', 'arguments': {'section_id': env['sid'], 'revision_id': revision['id']}})
        return {'text': db.dumps({'agent_transport': True, **answer, 'finish': 'stop'}),
                'usage': {'input_tokens': 100, 'output_tokens': 50}, 'cost': 0.2,
                'remote_id': 'budget-fixture', 'request_hash': digest([system, user])}
    monkeypatch.setattr(provider, 'generate', generate)
    with pytest.raises(BudgetExceeded):
        runtime.collect(task, section, '预算必须共享父任务')
    assert len(calls) == 1
    assert db.one('SELECT task_id,cost FROM generation_runs')['task_id'] == task['id']
    assert db.one("SELECT COUNT(*) n FROM writing_steps WHERE state='budget_blocked'")['n'] == 1


def test_broker_tool_observation_flows_into_registered_evidence_and_downstream_context(env):
    from app import parsers, resource_refs, writing_retrieval
    from app.writing_agent import context
    session = pure_literature(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    original, _ = parsers.register_file(env['pid'], '动态实际原文.txt', '该研究仅在小样本条件下比较两种方法，不能推广为完整实验结论。'.encode(), 'research')
    parsers.parse_file(original['id'])
    source = db.one('SELECT id FROM sources WHERE file_id=? ORDER BY rowid LIMIT 1', (original['id'],))
    ref = resource_refs.make(env['pid'], 'source', source['id'])
    observed = tools.execute(env['pid'], step['id'], 'read_writing_materials',
        {'candidates_json': db.dumps([ref]), 'query': '核对研究条件与局限'}, 'actual-material-read')
    assert observed['observed_evidence_ids']
    data, manifest = context.assemble(env['pid'], env['sid'], state(env['pid'])['contracts'][env['sid']],
        env['profile'], True, observed['observed_evidence_ids'])
    result = {'material_bundles': [observed['data']]}
    runtime.augment_context(env['pid'], data, manifest, result)
    assert any('小样本' in row['payload'].get('text', '') for row in data['evidence'])
    assert data['dynamic_material_catalog'] and manifest['resource_refs']
    assert db.one('SELECT COUNT(*) n FROM revisions')['n'] == 0


def test_only_acknowledged_parent_unknown_run_can_start_fresh_research_attempt(env, monkeypatch):
    pure_literature(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    task = running_task(env)
    calls = []
    def generate(system, user, images=None):
        calls.append(1)
        if len(calls) == 1:
            raise provider.ProviderError('合成远端发送后结果未知', unknown=True)
        return {'text': db.dumps({'agent_transport': True, **envelope({'action': 'done', 'summary': '明确确认后重新调查'}), 'finish': 'stop'}),
                'usage': {'input_tokens': 50, 'output_tokens': 30}, 'cost': 0,
                'remote_id': 'acknowledged-research', 'request_hash': digest([system, user])}
    monkeypatch.setattr(provider, 'generate', generate)
    with pytest.raises(provider.ProviderError):
        runtime.collect(task, section, '明确确认前不能重发')
    unknown = db.one("SELECT id,status FROM generation_runs WHERE status='outcome_unknown'")
    assert unknown
    with pytest.raises(provider.ProviderError):
        runtime.collect(task, section, '明确确认前不能重发')
    assert len(calls) == 1
    payload = db.loads(task['payload']);payload['acknowledged_run_ids'] = [unknown['id']]
    db.execute('UPDATE tasks SET payload=?,attempt=2 WHERE id=?', (db.dumps(payload), task['id']))
    fresh = db.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
    result = runtime.collect(fresh, section, '明确确认前不能重发')
    assert result['status'] == 'completed' and len(calls) == 2
    assert db.one('SELECT status FROM generation_runs WHERE id=?', (unknown['id'],))['status'] == 'outcome_unknown'
    assert db.one("SELECT COUNT(*) n FROM writing_steps WHERE state='abandoned_unknown'")['n'] == 1


@pytest.mark.parametrize('resource_kind', ['source', 'unit'])
def test_dynamic_material_read_and_evidence_registration_preserve_frozen_live_inputs(env, resource_kind):
    from app import parsers, resource_refs, knowledge_units, writing_live
    original, _ = parsers.register_file(env['pid'], '只读取材时钟.txt', '仅按单用户样例讨论研究方法及局限。'.encode(), 'research')
    parsers.parse_file(original['id'])
    if resource_kind == 'unit':
        parsed = knowledge_units.build(env['pid'], original['id'])
        identity = parsed['units'][0]['id']
    else:
        identity = db.one('SELECT id FROM sources WHERE file_id=? ORDER BY rowid LIMIT 1', (original['id'],))['id']
    session = pure_literature(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    ref = resource_refs.make(env['pid'], resource_kind, identity)
    frozen = writing_live.versions(env['pid'])
    before_evidence = db.one('SELECT COUNT(*) n FROM evidence_items')['n']
    result = tools.execute(env['pid'], step['id'], 'read_writing_materials',
        {'candidates_json': db.dumps([ref]), 'query': '核对方法适用条件'}, 'clock-safe-' + resource_kind)
    assert result['observed_evidence_ids']
    assert db.one('SELECT COUNT(*) n FROM evidence_items')['n'] > before_evidence
    assert db.one('SELECT COUNT(*) n FROM tool_observations')['n'] == 1
    assert writing_live.versions(env['pid']) == frozen
    writing_live.validate(env['pid'], {'live_input_versions': frozen})


def test_existing_document_reading_changes_only_reading_clock_not_live_material_inputs(env):
    from app import parsers, knowledge_units, writing_live
    original, _ = parsers.register_file(env['pid'], '已建结构阅读.txt', '研究方法用于案例讨论。\n\n适用条件仅限小样本。'.encode(), 'research')
    parsers.parse_file(original['id']);knowledge_units.build(env['pid'], original['id'])
    session = pure_literature(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    frozen = writing_live.versions(env['pid'])
    clock = db.one('SELECT * FROM project_change_clock WHERE project_id=?', (env['pid'],))
    opened = tools.execute(env['pid'], step['id'], 'open_literature_document',
        {'file_id': original['id'], 'question': '核对方法和适用条件'}, 'clock-open-existing')['data']
    result = tools.execute(env['pid'], step['id'], 'read_document_range',
        {'reading_id': opened['reading_id'], 'ordinal_start': 1, 'ordinal_end': 1}, 'clock-read-existing')['data']
    current = db.one('SELECT * FROM project_change_clock WHERE project_id=?', (env['pid'],))
    assert result['evidence_id'] and current['reading'] > clock['reading']
    assert current['materials'] == clock['materials']
    assert writing_live.versions(env['pid']) == frozen
    writing_live.validate(env['pid'], {'live_input_versions': frozen})


def test_legacy_document_open_rebuild_is_visible_as_real_material_change(env):
    from app import parsers, writing_live
    original, _ = parsers.register_file(env['pid'], '尚无结构原件.txt', '研究条件必须保留。'.encode(), 'research')
    parsers.parse_file(original['id'])
    assert not db.one('SELECT * FROM knowledge_heads WHERE file_id=?', (original['id'],))
    session = pure_literature(env)
    step = db.one('SELECT * FROM writing_steps WHERE session_id=?', (session['id'],))
    frozen = writing_live.versions(env['pid'])
    opened = tools.execute(env['pid'], step['id'], 'open_literature_document',
        {'file_id': original['id'], 'question': '读取研究条件'}, 'clock-open-legacy')['data']
    assert opened['reading_id'] and db.one('SELECT * FROM knowledge_heads WHERE file_id=?', (original['id'],))
    assert writing_live.versions(env['pid'])['material_revision'] > frozen['material_revision']
    with pytest.raises(HTTPException) as exc:
        writing_live.validate(env['pid'], {'live_input_versions': frozen})
    assert exc.value.detail['code'] == 'WRITING_INPUT_CHANGED'


def test_frozen_collect_defers_implicit_structure_build_and_still_reads_legacy_source(env):
    from app import parsers, writing_live, resource_refs
    original, _ = parsers.register_file(env['pid'], '冻结补证旧版原文.txt', '该方法仅适用于当前小样本，不能推断并发性能。'.encode(), 'research')
    parsers.parse_file(original['id'])
    source = db.one('SELECT id FROM sources WHERE file_id=? ORDER BY rowid LIMIT 1', (original['id'],))
    pure_literature(env)
    task = running_task(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    frozen = writing_live.versions(env['pid'])
    scope = runtime.ensure_scope(task, section, 'generate', {'live_input_versions': frozen}, stage='material_research')
    before_evidence = db.one('SELECT COUNT(*) n FROM evidence_items')['n']
    result = tools.execute(env['pid'], scope['step_id'], 'open_literature_document',
        {'file_id': original['id'], 'question': '核对方法与局限'}, 'frozen-open')['data']
    assert result['status'] == 'needs_index_preparation'
    assert result['passages'] == [] and 'reading_id' not in result
    assert not db.one('SELECT * FROM knowledge_heads WHERE file_id=?', (original['id'],))
    assert not db.one('SELECT * FROM document_readings WHERE file_id=?', (original['id'],))
    assert db.one('SELECT COUNT(*) n FROM evidence_items')['n'] == before_evidence
    assert db.one('SELECT COUNT(*) n FROM tool_observations')['n'] == 1
    assert writing_live.versions(env['pid']) == frozen
    ref = resource_refs.make(env['pid'], 'source', source['id'])
    fallback = tools.execute(env['pid'], scope['step_id'], 'read_writing_materials',
        {'candidates_json': db.dumps([ref]), 'query': '核对方法与局限'}, 'frozen-fallback')
    assert fallback['observed_evidence_ids']
    assert '小样本' in fallback['data']['primary_evidence'][0]['text']
    assert writing_live.versions(env['pid']) == frozen
    writing_live.validate(env['pid'], {'live_input_versions': frozen})


def test_frozen_reading_range_is_immutable_but_new_reading_can_collect_more(env):
    from app import parsers, knowledge_units, document_reading as reader, writing_live
    original, _ = parsers.register_file(env['pid'], '已冻结阅读与独立补读.txt', '方法采用案例分析。\n\n局限仅为小样本模拟。'.encode(), 'research')
    parsers.parse_file(original['id']);knowledge_units.build(env['pid'], original['id'])
    pure_literature(env)
    old = reader.create(env['pid'], {'file_id': original['id'], 'section_id': env['sid'], 'question': '方法与局限是什么？'})
    reader.read_range(env['pid'], old['id'], {'ordinal_start': 1, 'ordinal_end': 1}, remote=True)
    old = reader.context(env['pid'], old['id'], remote=True)
    task = running_task(env)
    section = db.one('SELECT * FROM sections WHERE id=?', (env['sid'],))
    frozen = writing_live.versions(env['pid'])
    scope = runtime.ensure_scope(task, section, 'generate', {'live_input_versions': frozen, 'document_reading_versions': {old['id']: old['version']}}, stage='material_research')
    result = tools.execute(env['pid'], scope['step_id'], 'read_document_range',
        {'reading_id': old['id'], 'ordinal_start': 2, 'ordinal_end': 2}, 'frozen-reading-expand')['data']
    assert result['status'] == 'frozen_reading_requires_new_record' and not result['passages']
    assert reader.context(env['pid'], old['id'], remote=True)['version'] == old['version']
    assert not db.one('SELECT * FROM evidence_items WHERE origin_type=? AND origin_id=?', ('document_reading', old['id']))
    opened = tools.execute(env['pid'], scope['step_id'], 'open_literature_document',
        {'file_id': original['id'], 'question': old['question']}, 'independent-reading')['data']
    assert opened['reading_id'] != old['id']
    added = tools.execute(env['pid'], scope['step_id'], 'read_document_range',
        {'reading_id': opened['reading_id'], 'ordinal_start': 2, 'ordinal_end': 2}, 'independent-reading-range')['data']
    assert added['evidence_id'] and added['passages']
    assert reader.context(env['pid'], old['id'], remote=True)['version'] == old['version']
    assert writing_live.versions(env['pid']) == frozen
