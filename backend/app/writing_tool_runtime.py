"""Bounded material investigation shared by chapter writers and the writing agent.

Every remote call uses the existing task ledger. Checkpoints live in ordinary
writing steps so a replay reuses known responses and never resends an unknown
request. This module collects material; it never creates or adopts prose.
"""
import json
import math
from copy import deepcopy

from fastapi import HTTPException
from . import db, provider, security
from .source_payload import token_upper_bound
from .writing_agent.common import blob, unblob, digest, fail, state
from .writing_agent import evidence, profiles, prompts, tools, provider_adapter

VERSION = 'writing_material_runtime.v1'


def _model_observation(name, result):
    """Keep each read text once; storage retains the complete sealed bundle."""
    if name != 'read_writing_materials':
        return result
    bundle = result['data']
    from .writing_retrieval import for_model
    view = for_model(bundle)
    for group in ('constraints', 'primary_evidence', 'guidance', 'derived_context'):
        view[group] = [{key: value for key, value in item.items()
                        if key in ('id', 'title', 'text', 'source_ref', 'locator', 'kind', 'conditions',
                                   'reference_ids', 'read_scope', 'semantic_support', 'channel')}
                       for item in bundle.get(group, [])]
    return {**result, 'data': view}


def current_evidence(pid, identities):
    """A reading can gain passages; downstream stages use its final version."""
    from .document_reading import context as reading_context
    result = []
    readings = set()
    for identity in dict.fromkeys(identities):
        item = evidence.get(pid, identity, remote=True)
        if item['origin_type'] == 'document_reading':
            if item['origin_id'] in readings:
                continue
            reading = reading_context(pid, item['origin_id'], remote=True)
            readings.add(item['origin_id'])
            if reading['intent'] != 'research':
                continue
            item = evidence.sync_reading(pid, item['origin_id'])
        if item['kind'] != 'guidance_observation':
            result.append(item['id'])
    return result


def _save(step_id, checkpoint, assert_active, epoch):
    with db.transaction():
        assert_active()
        db.execute('UPDATE writing_steps SET output_blob=? WHERE id=? AND state=? AND lease_epoch=?',
                   (blob({'runtime_checkpoint': checkpoint}), step_id, 'running', epoch))


def run_investigation(task, step, session, profile, payload, manifest, *,
                      system=None, max_rounds=None, max_tool_calls=None,
                      allowed_tools=None, assert_active=None):
    """Execute native or JSON tool actions, persisting each completed action.

    The caller owns step/session lifecycle. Its step must point at the running
    parent task, preserving cancellation, request accounting and lease guards.
    """
    from .writing_agent.coordinator import assert_lease
    config = profile['configuration']
    rounds = max_rounds if max_rounds is not None else config.get('max_turns', 6)
    maximum = max_tool_calls if max_tool_calls is not None else config.get('max_tool_calls', 24)
    if type(rounds) is not int or not 1 <= rounds <= 6 or type(maximum) is not int or not 1 <= maximum <= 24:
        fail('INVALID_POLICY', '取材轮数或工具次数超出范围')
    native = config.get('tool_mode', 'json') == 'native'
    catalog = tools.investigation_catalog(native=native,include_knowledge=bool(manifest.get('knowledge_refs')),
                            include_code=bool(session.get('target_snapshot_id')),allowed_tools=allowed_tools)
    permitted = {tool['function']['name'] if native else tool['name'] for tool in catalog}
    system = tools.investigation_system(system or prompts.INVESTIGATE,session.get('target_snapshot_id'))
    assert_active = assert_active or (lambda: assert_lease(step['id'], manifest['lease_epoch']))
    epoch=manifest.get('lease_epoch',step.get('lease_epoch',0))
    original = unblob(step['output_blob']) if step.get('output_blob') else {}
    checkpoint = original.get('runtime_checkpoint')
    fingerprint = digest([VERSION, profile['route_fingerprint'], payload, system,
                          sorted(permitted), rounds, maximum])
    if checkpoint and checkpoint.get('fingerprint') != fingerprint:
        fail('INPUT_VERSION_CHANGED', '取材检查点的上下文或配置已变化', 409)
    if not checkpoint:
        checkpoint = {'fingerprint': fingerprint, 'turn': 0, 'count': 0,
                      'messages': [{'role': 'user', 'content': db.dumps({'context': payload} if native else {'context': payload, 'available_tools': catalog})}],
                      'items': None, 'evidence_ids': [e['id'] for e in payload.get('evidence', [])
                                                   if e['id'] in manifest.get('source_evidence', {})],
                      'pending': None, 'observations': []}

    def finish(summary, limited=False, reason=None):
        result = {'summary': str(summary)[:6000],
                  'evidence_ids': current_evidence(session['project_id'], checkpoint['evidence_ids']),
                  'limit_reached': limited, 'tool_observation_ids': checkpoint['observations']}
        read_rows=db.rows("SELECT result_blob FROM tool_observations WHERE step_id=? AND tool_name='read_writing_materials' ORDER BY rowid",(step['id'],))
        if read_rows:result['material_bundles']=[unblob(row['result_blob'])['data'] for row in read_rows]
        if reason:
            result['unresolved'] = [reason]
        return result

    while checkpoint['turn'] < rounds:
        assert_active()
        messages, items = checkpoint['messages'], checkpoint['items']
        if not checkpoint['pending']:
            _, cfg, _ = profiles.for_session(session['project_id'], profile['id'])
            frame = {'messages': messages, 'tools': catalog if native else []}
            if items is not None:
                frame['items'] = items
            if token_upper_bound(system, db.dumps(frame)) + int(cfg.get('max_output_tokens', 8192)) > int(cfg.get('context_limit', 32000)):
                return finish('调查输入达到上限，保留实际读取的依据；未读取目录不作为证据。', True, 'context_limit')
            _save(step['id'], checkpoint, assert_active,epoch)
            response = provider_adapter.call(task, profile, system, payload,
                {**manifest, 'investigation_round': checkpoint['turn']},
                catalog if native else None, messages, items)
            calls = response.get('tool_calls', [])
            if native and calls:
                if response.get('message'):
                    messages.append(response['message'])
                if response.get('items'):
                    checkpoint['items'] = (items if items is not None else messages[:1]) + response['items']
            elif not native:
                action = provider.parse_json(response['text'])
                if action.get('action') == 'done':
                    return finish(action.get('summary', '取材完成'))
                if action.get('action') != 'tool':
                    fail('INVALID_ACTION', '调查必须返回工具动作或完成说明')
                calls = [{'id': str(checkpoint['turn']) + '-json-' + digest([action.get('name'), action.get('arguments', {})])[:24],
                          'name': action.get('name'), 'arguments': action.get('arguments', {})}]
                messages.append({'role': 'assistant', 'content': response['text']})
            else:
                return finish(response.get('text', '取材完成'))
            checkpoint['pending'] = {'calls': calls, 'index': 0}
            _save(step['id'], checkpoint, assert_active,epoch)
        pending = checkpoint['pending']
        while pending['index'] < len(pending['calls']):
            assert_active()
            if checkpoint['count'] >= maximum:
                return finish('调查工具次数到限，保留已读取材料。', True, 'tool_limit')
            call = pending['calls'][pending['index']]
            if call.get('name') not in permitted:
                fail('TOOL_DENIED', '工具不在本次取材允许范围', 403)
            try:
                arguments = call['arguments'] if isinstance(call['arguments'], dict) else json.loads(call['arguments'])
            except (ValueError, TypeError, KeyError):
                fail('INVALID_TOOL_ARGUMENTS', '工具参数必须是JSON对象')
            result = tools.execute(session['project_id'], step['id'], call['name'], arguments,
                                   call['id'], session.get('target_snapshot_id'))
            if result.get('data', {}).get('evidence_id'):
                checkpoint['evidence_ids'].append(result['data']['evidence_id'])
            checkpoint['evidence_ids'].extend(result.get('observed_evidence_ids', []))
            if call['name'] == 'read_writing_materials':
                checkpoint['evidence_ids'].extend(result.get('data', {}).get('compatibility', {}).get('evidence_ids', []))
            observed = db.one('SELECT id FROM tool_observations WHERE step_id=? AND call_id=?', (step['id'], call['id']))
            if observed and observed['id'] not in checkpoint['observations']:
                checkpoint['observations'].append(observed['id'])
            model_result = _model_observation(call['name'], result)
            if native:
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': db.dumps(model_result)})
                if checkpoint['items'] is not None:
                    checkpoint['items'].append({'type': 'function_call_output', 'call_id': call['id'], 'output': db.dumps(model_result)})
            else:
                messages.append({'role': 'user', 'content': db.dumps({'tool_result': model_result})})
            checkpoint['count'] += 1
            pending['index'] += 1
            _save(step['id'], checkpoint, assert_active,epoch)
        checkpoint['pending'] = None
        checkpoint['turn'] += 1
        _save(step['id'], checkpoint, assert_active,epoch)
    return finish('调查轮数到限，已读取材料保留，未完成范围需要继续补证。', True, 'round_limit')


def _scope(task, section, profile, payload, manifest, budget_limit):
    """Create one idempotent observation owner inside the parent's task budget."""
    project = security.require_project(task['project_id'])
    task_payload = db.loads(task.get('payload'), {})
    limits = [float(project['budget'])]
    for value in (budget_limit, task_payload.get('budget_limit')):
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                fail('INVALID_POLICY', '取材预算必须是有效正数')
            limits.append(float(value))
    key = digest([VERSION, task['id'], section['id'], payload, manifest, profile['route_fingerprint']])
    with db.transaction():
        prior = db.one("SELECT st.* FROM writing_steps st JOIN writing_sessions s ON s.id=st.session_id WHERE st.task_id=? AND st.input_hash=? AND s.operation='collect_materials' ORDER BY st.iteration DESC LIMIT 1", (task['id'], key))
        if prior:
            session = db.one('SELECT * FROM writing_sessions WHERE id=?', (prior['session_id'],))
            if session['project_id'] != task['project_id']:
                fail('NOT_FOUND', '取材检查点不属于当前项目', 404)
            if prior['state']=='outcome_unknown':
                unknown=db.rows("SELECT id FROM generation_runs WHERE task_id=? AND json_extract(manifest,'$.writing_step_id')=? AND status IN ('pending','sent','outcome_unknown')",(task['id'],prior['id']))
                acknowledged=set(task_payload.get('acknowledged_run_ids',[]))
                if unknown and {run['id'] for run in unknown}<=acknowledged:
                    replacement=db.uid()
                    db.execute("UPDATE writing_steps SET state='abandoned_unknown' WHERE id=?",(prior['id'],))
                    db.execute('INSERT INTO writing_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (replacement,session['id'],prior['stage'],prior['iteration']+1,key,'running',task.get('attempt',0),task['id'],None,None))
                    db.execute("UPDATE writing_sessions SET status='running',updated_at=? WHERE id=?",(db.now(),session['id']))
                    return db.one('SELECT * FROM writing_sessions WHERE id=?',(session['id'],)),db.one('SELECT * FROM writing_steps WHERE id=?',(replacement,))
            return session, prior
        identity, step_id = db.uid(), db.uid()
        epoch = task.get('attempt', 0)
        policy = {'budget_limit': min(limits), 'parent_task_id': task['id'], 'runtime': VERSION}
        db.execute('INSERT INTO writing_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (identity, task['project_id'], section['id'], 'collect_materials', 'running',
                    manifest.get('target_snapshot_id'), db.dumps(manifest), profile['id'], db.dumps(policy), db.now(), db.now()))
        db.execute('INSERT INTO writing_steps VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (step_id, identity, 'investigate', 0, key, 'running', epoch, task['id'], None, None))
        return (db.one('SELECT * FROM writing_sessions WHERE id=?', (identity,)),
                db.one('SELECT * FROM writing_steps WHERE id=?', (step_id,)))


def ensure_scope(task, section, operation, manifest=None, *, stage='writing_flow',
                 input_key=None, budget_limit=None, profile_id=None):
    """Return a stable local checkpoint owner without requesting a model.

    Save small plan/unit/quality records through ``save_scope``. The caller
    chooses its stage and finishes the scope explicitly; no task is enqueued.
    """
    pid = task['project_id']
    own = security.require_section(section['id'])
    if own['project_id'] != pid:
        fail('NOT_FOUND', '章节不属于当前项目', 404)
    profile = profiles.get(pid, profile_id) if profile_id else profiles.current_default(pid)
    payload = {'operation': operation, 'stage': stage, 'input_key': input_key}
    session, step = _scope(task, section, profile, payload, deepcopy(manifest or {}), budget_limit)
    if step['stage'] != stage:
        db.execute('UPDATE writing_steps SET stage=? WHERE id=?', (stage, step['id']))
        step['stage'] = stage
    if step['state'] == 'outcome_unknown':
        raise provider.ProviderError('写作检查点远端结果未知，不能自动重试', unknown=True)
    if session['status'] in ('stale', 'cancelled'):
        fail('INPUT_VERSION_CHANGED', '写作检查点已过期或取消', 409)
    return {'session_id': session['id'], 'step_id': step['id'],
            'state': step['state'], 'lease_epoch': task.get('attempt', 0),
            'task_id': task['id'], 'stage': stage,
            'output': unblob(step['output_blob']) if step.get('output_blob') else None}


def save_scope(task, scope, output, *, status='running'):
    """Atomically save local state while retaining parent lease/cancellation."""
    allowed = {'running', 'completed', 'needs_review', 'budget_blocked', 'failed', 'outcome_unknown'}
    if status not in allowed:
        fail('INVALID_POLICY', '检查点状态无效')
    with db.transaction():
        live = db.one('SELECT * FROM tasks WHERE id=? AND project_id=?', (task['id'], task['project_id']))
        step = db.one('SELECT st.* FROM writing_steps st JOIN writing_sessions s ON s.id=st.session_id WHERE st.id=? AND s.project_id=? AND st.task_id=?',
                      (scope['step_id'], task['project_id'], task['id']))
        if not live or not step or live['attempt'] != task.get('attempt', 0) or live['status'] != 'running':
            fail('STEP_LEASE_EXPIRED', '父任务代次或执行状态已变化', 409)
        if live['cancel_requested']:
            raise InterruptedError('写作任务已取消')
        db.execute('UPDATE writing_steps SET state=?,lease_epoch=?,output_blob=? WHERE id=?',
                   (status, task.get('attempt', 0), blob(output), step['id']))
        db.execute('UPDATE writing_sessions SET status=?,updated_at=? WHERE id=?',
                   (status, db.now(), step['session_id']))
    return {**scope, 'state': status, 'output': output}


def collect(task, section, query, *, operation='generate', initial_context=None,
            initial_manifest=None, max_rounds=3, max_tool_calls=8,
            budget_limit=None, profile_id=None):
    """Collect missing sources on demand; return context/manifest additions only."""
    from .writing_agent.coordinator import assert_lease
    pid = task['project_id']
    owned_section = security.require_section(section['id'])
    if owned_section['project_id'] != pid:
        fail('NOT_FOUND', '章节不属于当前项目', 404)
    head = state(pid)
    profile = profiles.get(pid, profile_id) if profile_id else profiles.current_default(pid)
    payload = {'query': str(query)[:2000], 'operation': operation,
               'section': {key: section.get(key) for key in ('id', 'title', 'kind', 'goal')},
               'context': deepcopy(initial_context or {}),
               'retrieval_policy': {'max_rounds': max_rounds, 'max_tool_calls': max_tool_calls},
               'policy': '只搜索并读取当前问题缺少的材料，不生成正文，不把目录预览、指导资料或个人笔记提升为研究结果。'}
    manifest = {'project_id': pid, 'section_id': section['id'],
                'core_version_id': head['core_version_id'], 'contract_version_id': head['contracts'].get(section['id']),
                'target_snapshot_id': head['target_snapshot_id'],
                'rule_package_id': security.require_project(pid)['rule_package_id'],
                **deepcopy(initial_manifest or {}), 'remote': True}
    session, step = _scope(task, section, profile, payload, manifest, budget_limit)
    if step['state'] == 'completed' and step['output_blob']:
        saved = unblob(step['output_blob'])
        _validate_result(pid, saved)
        return saved
    if step['state'] == 'outcome_unknown':
        raise provider.ProviderError('取材检查点的远端结果未知，不能自动重新请求', unknown=True)
    if session['status'] in ('stale', 'cancelled'):
        fail('INPUT_VERSION_CHANGED', '取材会话已过期或取消', 409)
    with db.transaction():
        db.execute("UPDATE writing_steps SET state='running',lease_epoch=?,error_json=NULL WHERE id=?", (task.get('attempt', 0), step['id']))
        db.execute("UPDATE writing_sessions SET status='running',updated_at=? WHERE id=?", (db.now(), session['id']))
    manifest.update(writing_session_id=session['id'], writing_step_id=step['id'],
                    lease_epoch=task.get('attempt', 0), stage='investigate',
                    writing_parent_task_id=task['id'], writing_runtime=VERSION)
    # The checkpoint identity distinguishes an explicitly acknowledged new
    # attempt while keeping a replay of this step's wire request identical.
    payload={**payload,'research_checkpoint':step['id']}
    system = prompts.BOUNDARY + ' 这是写作取材调查。JSON动作模式每次只输出 {"action":"tool","name":"工具名","arguments":{...}} 或 {"action":"done","summary":"已读依据及剩余缺口"}。先search_writing_materials，再read_writing_materials读取选定候选；具体缺口可用文献原文、章节和已登记证据工具继续核对。检索预览不能作为原文证据。没有代码快照时不请求代码工具。'
    allowed = set(tools.SPECS) - {'request_check', 'record_document_findings'}
    try:
        result = run_investigation(task, step, session, profile, payload, manifest,
                    system=system, max_rounds=max_rounds, max_tool_calls=max_tool_calls,
                    allowed_tools=allowed)
        finished = _collect_result(pid, session, step, result)
        _validate_result(pid, finished)
        with db.transaction():
            assert_lease(step['id'], task.get('attempt', 0))
            db.execute("UPDATE writing_steps SET state='completed',output_blob=? WHERE id=?", (blob(finished), step['id']))
            db.execute("UPDATE writing_sessions SET status='completed',updated_at=? WHERE id=?", (db.now(), session['id']))
        return finished
    except Exception as error:
        from .summary_billing import BudgetExceeded
        status = ('cancelled' if isinstance(error, InterruptedError) else
                  'budget_blocked' if isinstance(error, BudgetExceeded) else
                  'outcome_unknown' if isinstance(error, provider.ProviderError) and error.unknown else
                  'stale' if isinstance(error, HTTPException) and error.status_code == 409 else 'failed')
        with db.transaction():
            live = db.one('SELECT lease_epoch FROM writing_steps WHERE id=?', (step['id'],))
            parent=db.one('SELECT attempt FROM tasks WHERE id=?',(task['id'],))
            if live and parent and parent['attempt']==task.get('attempt',0) and live['lease_epoch'] == task.get('attempt', 0):
                db.execute('UPDATE writing_steps SET state=?,error_json=? WHERE id=?', (status, db.dumps({'message': security.redact(error)}), step['id']))
                db.execute('UPDATE writing_sessions SET status=?,updated_at=? WHERE id=?', (status, db.now(), session['id']))
        raise


def _collect_result(pid, session, step, result):
    rows = db.rows('SELECT * FROM tool_observations WHERE step_id=? ORDER BY rowid', (step['id'],))
    materials, bundles = [], []
    manifest = {'writing_research_session_id': session['id'], 'writing_research_step_id': step['id'],
                'evidence_versions': {}, 'permission_stamps': {}, 'file_ids': [], 'resource_refs': [], 'related_revisions': {}}
    for row in rows:
        value = unblob(row['result_blob'])
        materials.append({'tool_name': row['tool_name'], **_model_observation(row['tool_name'], value)})
        if row['tool_name'] == 'read_writing_materials':
            bundles.append(value['data'])
        if row['tool_name'] in ('read_section', 'read_section_range'):
            arguments = unblob(row['arguments_blob'])
            manifest['related_revisions'][arguments['section_id']] = arguments['revision_id']
    for identity in result['evidence_ids']:
        item = evidence.get(pid, identity, remote=True)
        manifest['evidence_versions'][identity] = item['origin_version']
        manifest['permission_stamps'][identity] = evidence.permission_stamp(pid, identity)
        if item['payload'].get('file_id'):
            manifest['file_ids'].append(item['payload']['file_id'])
    for bundle in bundles:
        related = bundle.get('source_manifest', bundle.get('manifest', {}))
        manifest['file_ids'].extend(bundle.get('compatibility', {}).get('file_ids', related.get('file_ids', [])))
        manifest['resource_refs'].extend(related.get('resource_refs', []))
    manifest['file_ids'] = sorted(set(manifest['file_ids']))
    manifest['resource_refs'] = list({digest(ref): ref for ref in manifest['resource_refs']}.values())
    status = 'limited' if result['limit_reached'] else 'completed'
    return {**result, 'status': status,
            'context': {'dynamic_materials': materials,
                        'dynamic_research': {'summary': result['summary'], 'status': status,
                                             'unresolved': result.get('unresolved', [])}},
            'manifest': manifest, 'material_bundles': bundles,
            'tool_observations': [{key: row[key] for key in ('id', 'tool_name', 'call_id', 'status')} for row in rows]}


def augment_context(pid, context, manifest, investigation):
    """Carry dynamically read products/guidance into every downstream stage.

    Original research passages already travel as registered evidence. This
    adds the other material classes and their version guards without treating
    a personal note or a teaching example as verified research evidence.
    """
    if not investigation.get('material_bundles'):
        return context
    from . import writing_retrieval
    for bundle in investigation['material_bundles']:
        writing_retrieval.validate(pid,bundle,remote=True)
        patch=writing_retrieval.for_writing(bundle)
        for key in ('references','research_products'):
            existing=context.setdefault(key,[])
            def identity(row):
                return digest(row.get('source_ref')) if row.get('source_ref') else row.get('id',digest(row))
            known={identity(row) for row in existing}
            existing.extend(row for row in patch.get(key,[]) if identity(row) not in known)
        context.setdefault('dynamic_material_catalog',[]).append(writing_retrieval.for_model(bundle))
        context.setdefault('dynamic_guidance',[]).extend(deepcopy(bundle.get('guidance',[])))
        source_manifest=bundle.get('source_manifest',{})
        manifest['resource_refs']=list({digest(ref):ref for ref in [*manifest.get('resource_refs',[]),*source_manifest.get('resource_refs',[])]}.values())
        manifest['file_ids']=sorted(set(manifest.get('file_ids',[]))|set(bundle.get('compatibility',{}).get('file_ids',[])))
        manifest['document_reading_versions']={**manifest.get('document_reading_versions',{}),**source_manifest.get('document_reading_versions',{})}
    return context


def _validate_result(pid, result):
    from .resource_refs import validate
    manifest = result.get('manifest', {})
    validate(pid, manifest.get('resource_refs', []), remote=True)
    for sid, rid in manifest.get('related_revisions', {}).items():
        from .writing_agent.provenance import revision_permissions
        revision_permissions(pid, rid)
        section = db.one('SELECT current_revision_id FROM sections WHERE id=? AND project_id=?', (sid, pid))
        if not section or section['current_revision_id'] != rid:
            fail('INPUT_VERSION_CHANGED', '取材所读正文版本已变化', 409)
    for identity, version in manifest.get('evidence_versions', {}).items():
        item = evidence.get(pid, identity, remote=True)
        if item['origin_version'] != version or not item['version_current'] or item['validity'] not in ('current', 'historical'):
            fail('INPUT_VERSION_CHANGED', '取材证据已变化', 409)
        if evidence.permission_stamp(pid, identity) != manifest.get('permission_stamps', {}).get(identity):
            fail('SOURCE_ACCESS_REVOKED', '取材证据权限已变化', 403)
    if result.get('material_bundles'):
        from .writing_retrieval import validate as validate_bundle
        for bundle in result['material_bundles']:
            validate_bundle(pid, bundle, remote=True)
