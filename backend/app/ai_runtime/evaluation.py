"""Paired A/B/C/D reports, without extra model calls or invented semantic scores."""
from copy import deepcopy
from fastapi import HTTPException
from .. import db, security
from ..ai_request_cache import digest
from .policy import frozen
from ..ai_contracts.versions import source_versions


def _input_basis(task, result,axis='runtime'):
    payload = db.loads(task['payload'], {})
    # engine/quality are the experimental variables; the rest must be paired.
    parameters = {key: value for key, value in payload.items()
                  if key not in ('ai_runtime', 'acknowledged_run_ids', 'generation_intent_revision','context_experiment_id')}
    parameters['runtime_parameters'] = deepcopy({key: value for key, value in frozen(task).items()
                                        if axis=='context_mode' or key not in ('engine', 'quality')})
    if axis=='context_mode':
        parameters.pop('context_mode',None)
        flow=parameters['runtime_parameters'].get('writing_flow_policy')
        if isinstance(flow,dict):flow.pop('context_mode',None)
    if task['kind'] == 'agent_step':
        step = db.one('SELECT * FROM writing_steps WHERE task_id=?', (task['id'],))
        if step:
            session = db.one('SELECT * FROM writing_sessions WHERE id=?', (step['session_id'],))
            parameters.pop('step_id', None)
            parameters['agent'] = {'stage': step['stage'], 'iteration': step['iteration'],
                'section_id': session['section_id'], 'operation': session['operation'],
                'base_versions': db.loads(session['base_versions_json'], {}),
                'policy': db.loads(session['policy_json'], {})}
    sections = result.get('paper_sections') or []
    manifests = [(item.get('section_id'), item.get('manifest') or {}) for item in sections]
    if not manifests:
        manifests = [(parameters.get('section_id'), result.get('manifest') or {})]
    sources = [{'section_id': sid, 'versions': source_versions(manifest),
                'quality_policy': manifest.get('writing_flow_policy') or manifest.get('writing_quality_policy'),
                'prompt_layout': manifest.get('prompt_layout_version'),
                'verification_scope': manifest.get('verification_scope_version')}
               for sid, manifest in manifests]
    if axis=='context_mode':
        sources=deepcopy(sources)
        for item in sources:
            # Historical manifests store a hash rather than the policy object.
            # Decode only when it exactly matches this task's frozen policy.
            from ..writing_agent.common import digest as policy_digest
            frozen_flow=frozen(task).get('writing_flow_policy')
            if isinstance(item['quality_policy'],str) and frozen_flow and item['quality_policy']==policy_digest(frozen_flow):
                item['quality_policy']=deepcopy(frozen_flow)
            if isinstance(item['quality_policy'],dict):item['quality_policy'].pop('context_mode',None)
            state=item['versions'].get('writing_state')
            if isinstance(state,dict):
                state.pop('paper_context',None)
                state.pop('writing_task_id',None)
                bundle=state.get('evidence_bundle')
                if isinstance(bundle,dict):
                    for key in ('bundle_id','hash','retrieval_trace','timings_ms','created_at'):bundle.pop(key,None)
    complete = all(manifest.get('rule_package_id') and
        ('base_revision_id' in manifest or 'core_version_id' in manifest) and
        any(key in manifest for key in ('evidence_versions', 'fact_revisions', 'fact_versions',
            'source_ids', 'knowledge_unit_versions', 'document_reading_versions', 'resource_refs'))
        for _, manifest in manifests)
    return digest({'kind': task['kind'], 'parameters': parameters, 'sources': sources}), bool(complete)


def context_observations(task,result,requests):
    manifests=[row.get('manifest') or {} for row in result.get('paper_sections',[])] or [result.get('manifest') or {}]
    coverage=[deepcopy(value) for manifest in manifests if (value:=manifest.get('paper_context') or (manifest.get('writing_state') or {}).get('paper_context'))]
    completed=[row for row in requests if row['status']=='completed']
    usage=[db.loads(row['usage'],{}) for row in completed]
    def measured(key):
        return sum(row[key] for row in usage) if usage and all(type(row.get(key)) is int and row[key]>=0 for row in usage) else None
    mode=(frozen(task).get('writing_flow_policy') or {}).get('context_mode','unknown')
    return {'mode':'hierarchical_v1' if mode=='argument_scoped' else mode,
            'coverage':coverage,'input_tokens':measured('input_tokens'),'output_tokens':measured('output_tokens'),
            'usage_scope':'reported_completed_generation_records','human_citation_support_rate':None,
            'human_omission_count':None,'human_edit_amount':None}


def _candidate_available(result):
    from ..documents import plain
    sections = result.get('paper_sections')
    if sections:
        return all(_candidate_available(item) for item in sections)
    document = result.get('document')
    return (isinstance(document, dict) and bool(plain(document).strip()) or
            isinstance(result.get('text'), str) and bool(result['text'].strip()))


def _transport_facts(task, requests,axis='runtime'):
    from ..provider_identity import effective, config_for
    intents = db.rows('SELECT * FROM request_intents WHERE project_id=? AND task_id=?',
                     (task['project_id'], task['id']))
    attempts = db.rows('SELECT a.*,i.stage FROM provider_attempts a JOIN request_intents i ON i.id=a.intent_id '
                      'WHERE i.project_id=? AND i.task_id=? ORDER BY i.created_at,i.id,a.ordinal',
                      (task['project_id'], task['id']))
    events = db.rows('SELECT e.attempt_id,e.event_type,e.detail FROM request_execution_events e '
        'JOIN request_intents i ON i.id=e.intent_id WHERE i.project_id=? AND i.task_id=?',
        (task['project_id'], task['id']))
    sent_ids = {row['attempt_id'] for row in events if row['event_type']=='send_started'}
    before_send_ids = {row['attempt_id'] for row in events if row['event_type']=='known_rejected'
        and db.loads(row['detail'], {}).get('failure_phase') in ('connect_before_http', 'policy_before_http',
            'cancel_before_http', 'attempt_preflight_before_http', 'attempt_budget_before_http')}
    # Terminal response states also attest sending when imported event detail is absent.
    sent = [row for row in attempts if row['id'] in sent_ids or row['state'] in
            ('send_started', 'response_received', 'persisted', 'outcome_unknown')]
    known_rejected = [row for row in attempts if row['state']=='known_rejected']
    links = db.rows('SELECT r.generation_run_id,r.intent_id FROM request_intent_runs r '
        'JOIN request_intents i ON i.id=r.intent_id WHERE i.project_id=? AND i.task_id=?',
        (task['project_id'], task['id']))
    linked = {row['generation_run_id'] for row in links}
    active_intents = {row['intent_id'] for row in attempts}
    attempted_runs = {row['generation_run_id'] for row in links if row['intent_id'] in active_intents}
    actual = []
    required = ('base_url', 'model', 'protocol', 'credential_hash')
    def add_identity(identity, stage, known):
        actual.append({'model': identity.get('model'), 'protocol': identity.get('protocol'),
            'stage': stage, 'actual_known': bool(known and all(identity.get(key) for key in required)),
            'identity_hash': digest({key: identity.get(key) for key in required})})
    for attempt in sent:
        add_identity(db.loads(attempt['identity'], {}), attempt['stage'], True)
    configuration_signatures = []
    parameters_known = True
    for request in requests:
        meta = db.loads(request['manifest'], {})
        stage = meta.get('writing_stage') or meta.get('stage') or task['kind']
        if request['status'] in ('completed', 'completed_invalid', 'manually_settled'):
            identity = effective(request)
            if request['id'] not in attempted_runs:
                add_identity(identity['effective'], stage, identity['actual_known'])
            cfg = config_for(request)
            parameters_known = parameters_known and type(cfg.get('max_output_tokens')) is int and cfg['max_output_tokens'] > 0
            configuration_signatures.append(digest({'stage': stage if axis=='runtime' else 'context_experiment', 'parameters': {key: cfg.get(key) for key in
                ('max_output_tokens', 'temperature', 'top_p', 'seed', 'thinking_enabled', 'reasoning_effort', 'json_mode')}}))
    per_intent = {}
    switches = 0
    for attempt in sent:
        identity = db.loads(attempt['identity'], {})
        destination = digest([attempt['endpoint_id'], {key: identity.get(key) for key in required}])
        previous = per_intent.setdefault(attempt['intent_id'], [])
        if previous and previous[-1] != destination:
            switches += 1
        previous.append(destination)
    coverage_complete = (bool(requests) and all(r['id'] in attempted_runs or r['status']=='failed_before_send'
        for r in requests) and all(row['state'] not in ('prepared', 'reserved') for row in attempts)
        and all(row['id'] in sent_ids or row['id'] in before_send_ids for row in known_rejected))
    observed_retries = sum(max(0, len(values)-1) for values in per_intent.values())
    return actual, {
        'generation_records': len(requests), 'logical_requests': len(intents) + sum(r['id'] not in linked for r in requests),
        'attempt_records': len(attempts), 'actual_sends': len(sent) if coverage_complete else None,
        'observed_send_starts': len(sent), 'attempt_coverage_complete': coverage_complete,
        'retries': observed_retries if coverage_complete else None,
        'endpoint_switches': switches if coverage_complete else None,
        'before_send_rejections': sum(row['id'] in before_send_ids and row['id'] not in sent_ids for row in known_rejected),
        'unknown_attempts': sum(row['state'] in ('prepared', 'reserved', 'send_started', 'outcome_unknown') for row in attempts),
        'count_scope':'local_send_start_records_not_remote_acceptance'}, sorted(set(configuration_signatures)), bool(configuration_signatures) and parameters_known


@db.read_session()
def compare(pid, body):
    security.require_project(pid)
    axis=body.get('comparison_axis','runtime')
    if axis not in ('runtime','context_mode'):raise HTTPException(422,'对照维度无效')
    ids=body.get('task_ids')
    if not isinstance(ids,list) or not 2<=len(ids)<=200 or not all(isinstance(value,str) for value in ids) or len(set(ids))!=len(ids):
        raise HTTPException(422,'需提供2至200个不重复的本项目任务ID')
    rows=[]
    for identity in ids:
        task=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(identity,pid))
        if not task:raise HTTPException(404,'对照任务不存在或不属于项目')
        result=db.loads(task['result'],{}) or {}
        policy=frozen(task)
        group=('B' if policy['engine']=='langgraph_v1' else 'A') if policy['quality']=='legacy' else ('D' if policy['engine']=='langgraph_v1' else 'C')
        basis_hash, evidence_complete = _input_basis(task, result,axis)
        requests=db.rows('SELECT * FROM generation_runs WHERE task_id=?',(identity,))
        actual, counts, configurations, parameters_known = _transport_facts(task, requests,axis)
        quality=result.get('writing_quality') or {}
        context=context_observations(task,result,requests)
        from .context_experiments import assessment
        human=assessment(pid,identity,result)
        if human:
            context.update(human_citation_support_rate=human['citation_support_rate'],
                human_omission_count=human['omission_count'],human_edit_amount=human['author_edit_characters'])
        if axis=='context_mode':group=context['mode']
        observed=db.rows("SELECT duration_ms FROM ai_runtime_events WHERE project_id=? AND task_id=? AND event_kind='task_total' ORDER BY id DESC LIMIT 1",(pid,identity))
        reasons = []
        if not evidence_complete: reasons.append('input_snapshot_incomplete')
        if axis=='context_mode' and (context['mode']=='unknown' or not context['coverage']):reasons.append('context_coverage_unknown')
        if task['status'] != 'succeeded': reasons.append('task_not_succeeded')
        if not _candidate_available(result): reasons.append('candidate_missing')
        if not any(r['status']=='completed' for r in requests): reasons.append('completed_response_missing')
        if not actual or not all(item['actual_known'] for item in actual): reasons.append('actual_provider_unknown')
        if not parameters_known: reasons.append('model_parameters_unknown')
        unknown = sum(r['status'] in ('pending','sent','outcome_unknown') for r in requests)
        if unknown or counts['unknown_attempts']: reasons.append('remote_outcome_unknown')
        rows.append({'task_id':identity,'group':group,'input_basis_hash':basis_hash,'status':task['status'],
            'input_evidence_complete':evidence_complete,
            'requests':len(requests),'request_count_scope':'generation_records', 'execution_counts':counts,
            'outcome_unknown':unknown, 'comparison_blockers':reasons,
            'provider_configuration_hashes':configurations,
            'provider_parameters_known':parameters_known,
            'actual_providers':actual,'quality_status':quality.get('status','not_available'),
            'obligations':quality.get('obligations'),'elapsed_ms':observed[0]['duration_ms'] if observed else None,
            'human_semantic_rating':None,'human_assessment':human,'result_hash':digest(result),'context_observations':context})
    from ..budget_usage import charge_tasks
    for row in rows:
        row['budget_occupancy']=charge_tasks(pid,[row['task_id']])
    paired={}
    for row in rows:paired.setdefault(row['input_basis_hash'],[]).append(row['task_id'])
    groups=[]
    for key,values in paired.items():
        matching=[row for row in rows if row['task_id'] in values]
        providers={digest(sorted({(item['stage'] if axis=='runtime' else 'context_experiment',item['identity_hash']) for item in row['actual_providers']})) for row in matching}
        configurations={digest(row['provider_configuration_hashes']) for row in matching}
        blockers = sorted({reason for row in matching for reason in row['comparison_blockers']})
        if len(matching) < 2: blockers.append('paired_input_missing')
        if len(providers) != 1: blockers.append('actual_providers_differ')
        if len(configurations) != 1: blockers.append('model_parameters_differ')
        if axis=='context_mode' and len({row['group'] for row in matching})<2:blockers.append('different_context_modes_missing')
        groups.append({'input_basis_hash':key,'task_ids':values,'ready_for_human_comparison':not blockers,
                       'comparison_blockers':blockers, 'input_pair_available':len(matching)>1})
    return {'schema':'ai.comparison.v1','comparison_axis':axis,'tasks':rows,
        'paired_input_groups':groups,
        'semantic_improvement_verified':False,'new_model_requests':0,
        'note':'先核对来源、实际模型和对照配对，再人工盲评。费用占用包含保守预留；未核实费用不按零计算。'}
