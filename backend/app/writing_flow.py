"""Shared, bounded evidence-driven writing for ordinary and whole-paper tasks.

The module reuses the task ledger and immutable writing-step checkpoints. It
never adopts a draft or interprets a model's support label as human approval.
"""
from copy import deepcopy
import math
import re
import time

from fastapi import HTTPException
from . import db, provider, security
from .writing_agent.common import digest

VERSION = 'writing.flow.v1'
DEFAULTS = {'enabled': True, 'semantic_review': True, 'originality_check': True,
            'max_revision_rounds': 2, 'max_tool_calls': 12, 'max_research_rounds': 4,
            'unit_target_words': 1000, 'unit_threshold_words': 2600,
            'context_mode': 'full_manuscript', 'max_material_chars': 24000}
LIMITS = {'max_revision_rounds': (0, 2), 'max_tool_calls': (1, 24),
          'max_research_rounds': (1, 6), 'unit_target_words': (400, 1800),
          'unit_threshold_words': (1200, 20000), 'max_material_chars': (2000, 100000)}


def settings(pid):
    security.require_project(pid)
    return {**DEFAULTS, **db.setting('writing_flow:' + pid, {})}


def save_settings(pid, values):
    security.require_project(pid)
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise HTTPException(422, '写作流程参数包含未知字段')
    for key, value in values.items():
        if key in LIMITS:
            lo, hi = LIMITS[key]
            if type(value) is not int or not lo <= value <= hi:
                raise HTTPException(422, '写作流程参数超范围：' + key)
        elif key == 'context_mode':
            if value not in ('full_manuscript', 'argument_scoped'):
                raise HTTPException(422, '上下文模式无效')
        elif type(value) is not bool:
            raise HTTPException(422, '写作流程开关无效：' + key)
    updated = {**settings(pid), **values}
    db.set_setting('writing_flow:' + pid, updated)
    db.audit(pid, 'writing_flow_settings_saved', values)
    return updated


def merge_context(context, patch):
    """Merge server-read records only; ID presence alone is never reading proof."""
    for key, rows in patch.items():
        if not isinstance(rows, list):
            continue
        existing = context.setdefault(key, [])
        if key=='knowledge_units':
            from .writing_evidence_quality import merge_unit_readings
            normalized=[];positions={}
            for prior in existing:
                uid=prior.get('unit_id') or prior.get('id')
                if uid in positions:normalized[positions[uid]]=merge_unit_readings(normalized[positions[uid]],prior)
                else:positions[uid]=len(normalized);normalized.append(deepcopy(prior))
            context[key]=existing=normalized
        def identity(row):
            ref = row.get('source_ref')
            return (ref['type'], ref['id'], ref['version']) if ref else row.get('unit_id') or row.get('reading_id') or row.get('id') or row.get('file_id')
        by_id = {identity(row): index for index, row in enumerate(existing)}
        for row in rows:
            ident = identity(row)
            if ident in by_id:
                if key=='knowledge_units':
                    from .writing_evidence_quality import merge_unit_readings
                    existing[by_id[ident]]=merge_unit_readings(existing[by_id[ident]],row)
                else:existing[by_id[ident]] = deepcopy(row)
            else:
                by_id[ident] = len(existing)
                existing.append(deepcopy(row))
    return context


def _manifest_bundle(manifest, bundle):
    from . import writing_retrieval
    writing_retrieval.validate(bundle['project_id'], bundle, remote=True)
    replacements = {(r['type'], r['id']): r for r in bundle['source_manifest'].get('resource_refs', [])}
    refs = [r for r in manifest.get('resource_refs', []) if (r['type'], r['id']) not in replacements]
    for ref in bundle['source_manifest'].get('resource_refs', []):
        if ref not in refs:
            refs.append(ref)
    manifest['resource_refs'] = refs
    compatibility = bundle['compatibility']
    manifest['file_ids'] = sorted(set(manifest.get('file_ids', [])) | set(compatibility.get('file_ids', [])))
    manifest['source_ids'] = sorted(set(manifest.get('source_ids', [])) | set(compatibility.get('source_ids', [])))
    manifest['evidence_bundle'] = bundle
    manifest['evidence_bundle_hash'] = bundle['hash']


def sync_manifest(pid, context, manifest):
    """Freeze precisely the server-read context after a bounded retrieval step."""
    from .writing_agent import evidence
    from .resource_refs import source_files
    manifest.setdefault('resource_refs',[])
    retained=deepcopy(manifest) if manifest.get('retained_candidate_provenance') else {}
    def version_union(key,current):
        prior=retained.get(key,{})
        for identity,version in current.items():
            if identity in prior and str(prior[identity])!=str(version):
                raise HTTPException(409,'保留候选与本次读取使用了不同来源版本：'+key)
        return {**prior,**current}
    versions, permissions = {}, {}
    for item in context.get('project_evidence', []):
        current = evidence.get(pid, item['id'], remote=True)
        if not current['version_current']:
            raise HTTPException(409, '本次项目证据已失效')
        versions[item['id']] = current['origin_version']
        permissions[item['id']] = evidence.permission_stamp(pid, item['id'])
    manifest['evidence_versions'] = version_union('evidence_versions',versions)
    manifest['permission_stamps'] = version_union('permission_stamps',permissions)
    manifest['fact_revisions'] = version_union('fact_revisions',{f['id']: f['revision'] for f in context.get('facts', [])})
    previous_units=manifest.get('knowledge_unit_versions',{})
    manifest['knowledge_unit_versions'] = {}
    from .knowledge_units import get as get_unit
    for unit in context.get('knowledge_units',[]):
        uid=unit.get('unit_id') or unit['id']
        authoritative=get_unit(pid,uid,remote=True)
        expected=unit.get('unit_hash') or unit.get('content_hash') or previous_units.get(uid)
        if expected and expected!=authoritative['content_hash']:raise HTTPException(409,'本次已读原文单元已变化')
        if not authoritative['current']:raise HTTPException(409,'本次已读原文单元已过期')
        manifest['knowledge_unit_versions'][uid]=authoritative['content_hash']
    manifest['knowledge_unit_versions']=version_union('knowledge_unit_versions',manifest['knowledge_unit_versions'])
    manifest['document_reading_versions'] = version_union('document_reading_versions',{r.get('reading_id') or r['id']: r['version'] for r in context.get('document_readings', [])})
    scopes = deepcopy(retained.get('knowledge_read_scopes',{}))
    from .writing_evidence_quality import resolve_unit_readings
    for unit in context.get('knowledge_units', []):
        uid = unit.get('unit_id') or unit['id']
        try:observations=resolve_unit_readings(pid,unit,remote=True)
        except ValueError as error:raise HTTPException(409,str(error)) from error
        scope=scopes.setdefault(uid,{'parse_id':unit.get('parse_id') or get_unit(pid,uid,remote=True)['parse_id'],
                    'scope':unit.get('scope','question_bound_reading_range'),'passage_ids':[],'reading_ranges':[]})
        if unit.get('scope')=='disjoint_read_observations':scope['scope']='disjoint_read_observations'
        for observed in observations:
            passage=observed['unit_id']
            if passage not in scope.setdefault('passage_ids',[]):scope['passage_ids'].append(passage)
            actual_range={key:observed[key] for key in ('unit_id','char_start','char_end','reading_id')}
            if actual_range not in scope.setdefault('reading_ranges',[]):scope['reading_ranges'].append(actual_range)
            prior_version=manifest['knowledge_unit_versions'].get(passage)
            if prior_version and prior_version!=observed['unit_hash']:raise HTTPException(409,'相邻已读原文片段版本不一致')
            manifest['knowledge_unit_versions'][passage]=observed['unit_hash']
    manifest['knowledge_read_scopes'] = scopes
    manifest['source_ids'] = sorted(set(retained.get('source_ids',[]))|{r['id'] for r in context.get('sources', [])})
    manifest['file_ids'] = sorted(set(manifest.get('file_ids', [])) | {u['file_id'] for u in context.get('knowledge_units', [])}
                                  | {r['file_id'] for r in context.get('sources', [])}
                                  | set(source_files(pid, manifest.get('resource_refs', []))))
    if manifest.get('writing_state') is not None:
        manifest['writing_state'].update({key: deepcopy(manifest[key]) for key in (
            'evidence_versions', 'permission_stamps', 'fact_revisions', 'knowledge_unit_versions',
            'knowledge_read_scopes', 'document_reading_versions', 'resource_refs')})
        manifest['writing_state']['fact_versions']=deepcopy(manifest['fact_revisions'])
        manifest['writing_state']['source_ids']=deepcopy(manifest['source_ids'])
    return manifest


def stable_input(value):
    if isinstance(value, dict):
        return {k: stable_input(v) for k, v in value.items()
                if k not in {'bundle_id', 'hash', 'retrieval_trace', 'timings_ms', 'duration_ms'}}
    if isinstance(value, list):
        return [stable_input(v) for v in value]
    return value


def prepare(pid, section, context, knowledge, guidance, query):
    from . import writing_argument, writing_retrieval
    policy = settings(pid)
    plan = writing_argument.build(pid, section, context, context.get('writing_brief', {}))
    context['argument_plan'] = writing_argument.for_model(plan)
    context['manuscript_state'] = writing_argument.manuscript_state(context)
    context, coverage = writing_argument.scoped_context(context, policy['context_mode'])
    if not policy['enabled']:
        return context, None, plan, coverage
    bundle = writing_retrieval.prepare(pid, section['id'], query[:5000],
             operation=context.get('operation', 'generate'), max_chars=policy['max_material_chars'],
             library_context=knowledge, guidance_context=guidance)
    merge_context(context, writing_retrieval.for_writing(bundle))
    context['evidence_bundle'] = writing_retrieval.for_model(bundle)
    context['writing_policy'] = {**policy, 'quality_status': 'candidate_requires_verification',
                                'scope': '生成中可提出retrieval_requests，未读目录不能成为证据'}
    return context, bundle, plan, coverage


def _cached_call(task, system, context, manifest, *, stage, index=0):
    """Reuse a completed exact request; never resend an unresolved remote call."""
    from . import tasks
    live=db.one('SELECT status,attempt,cancel_requested FROM tasks WHERE id=? AND project_id=?',(task['id'],task['project_id']))
    if not live or live['cancel_requested']:raise InterruptedError('写作任务已取消或不存在')
    if live['status']!='running' or live['attempt']!=task.get('attempt',live['attempt']):
        raise HTTPException(409,'写作任务代次已变化，不能继续发送或复用旧阶段')
    from .writing_agent.impact import check_manifest
    guard={**manifest.get('writing_state',{}),**{k:v for k,v in manifest.items() if k in ('evidence_bundle','resource_refs','section_materials')}}
    conflicts=check_manifest(task['project_id'],guard) if manifest.get('writing_state') else []
    if manifest.get('evidence_bundle'):
        from .writing_retrieval import validate
        validate(task['project_id'],manifest['evidence_bundle'],remote=True)
    if conflicts:raise HTTPException(409,{'code':'WRITING_INPUT_CHANGED','message':'写作依据或权限已变化','conflicts':conflicts})
    request_hash = digest([VERSION, system, stable_input(context), manifest.get('rule_package_id'),
                           provider.config(), stage, index,manifest.get('request_options')])
    from .source_payload import token_upper_bound
    output=int(manifest.get('request_options',{}).get('max_output_tokens',provider.config().get('max_output_tokens',8192)))
    if token_upper_bound(system,db.dumps(context))+output>int(provider.config().get('context_limit',32000)):
        raise HTTPException(422,{'code':'WRITING_CONTEXT_BUDGET_EXCEEDED','message':'本阶段完整原文、任务与预留输出超出上下文容量，已保存完成阶段；请缩小写作单元或调整接口容量。'})
    for run in db.rows('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at DESC', (task['id'],)):
        meta = db.loads(run['manifest'], {})
        if meta.get('writing_flow_request_hash') != request_hash:
            continue
        if run['status'] in ('pending', 'sent', 'outcome_unknown'):
            acknowledged=set(db.loads(task.get('payload'),{}).get('acknowledged_run_ids',[]))
            if run['id'] in acknowledged:continue
            raise provider.ProviderError('该写作阶段的远端结果未知，不能自动重复请求', unknown=True)
        if run['status'] == 'completed' and meta.get('output_blob'):
            return {'text': security.safe_object(meta['output_blob']).read_text(encoding='utf-8'),
                    'run_id': run['id'], 'cache_hit': True}
    return tasks.model_call(task, system, db.dumps(context),
             {**manifest, 'writing_stage': stage, 'writing_unit_index': index,
              'writing_flow_request_hash': request_hash})


def generate(task, section, system, context, manifest, parser):
    """Generate a whole short chapter or checkpointed complete argument units."""
    from .writing_tool_runtime import ensure_scope, save_scope
    policy = settings(task['project_id'])
    words = int(section.get('target_words') or 1500)
    unit_mode = policy['enabled'] and words >= policy['unit_threshold_words'] and context.get('operation') == 'generate'
    if not unit_mode:
        return _cached_call(task, system, context, manifest, stage='draft')
    scope = ensure_scope(task, section, context.get('operation', 'generate'), stable_input(manifest),
                         stage='argument_units', input_key=digest([system, stable_input(context), policy]))
    saved = scope.get('output') or {'units': [], 'schema': VERSION}
    total = min(12, max(2, math.ceil(words / policy['unit_target_words'])))
    cards = (context.get('argument_plan') or {}).get('paragraph_cards', [])
    paragraphs, missing, visuals,unresolved = [], [], [],[]
    for index in range(total):
        if index < len(saved['units']):
            data = saved['units'][index]['candidate']
        else:
            from .tasks import event
            event(task['id'], f'按论证单元生成 {index + 1}/{total}', 40 + 35 * index / total)
            unit_cards = cards[index * len(cards) // total:(index + 1) * len(cards) // total]
            unit_context = {**context, 'argument_unit': {'index': index + 1, 'total': total,
                            'target_words': math.ceil(words / total), 'paragraph_cards': unit_cards,
                            'previous_actual_paragraphs': paragraphs,
                            'policy': '仅输出本单元完整论证；不重写先前段落；不是每个单元都写章节引言和小结。'}}
            unit_system = system + ' 本次按argument_unit生成论证单元，章节全局计划仅供组织。正文段落必须推进当前问题，承接前文已成立结论。'
            reply = _cached_call(task, unit_system, unit_context, manifest, stage='argument_unit', index=index)
            data, raw, problem = parser(reply['text'])
            for attempt in range(2):
                if data is not None:
                    break
                reply = _cached_call(task, unit_system + ' 响应格式错误：' + problem + '；只返回合法章节JSON。',
                                    unit_context, manifest, stage='unit_format_repair', index=index * 2 + attempt)
                data, raw, problem = parser(reply['text'])
            if data is None:
                save_scope(task, scope, saved, status='needs_review')
                raise ValueError('论证单元响应无效，已完成单元已保留：' + problem)
            saved['units'].append({'index': index, 'candidate': data, 'run_id': reply.get('run_id')})
            save_scope(task, scope, saved)
        offset = len(paragraphs)
        for visual in data.get('visuals', []):
            visuals.append({**visual, 'after_paragraph': int(visual.get('after_paragraph', 0)) + offset})
        paragraphs.extend(data['paragraphs'])
        missing.extend(data.get('missing_materials', []))
        unresolved.extend(data.get('unresolved_claims',[]))
    save_scope(task, scope, saved, status='completed')
    manifest['argument_units'] = {'count': total, 'step_id': scope['step_id'], 'checkpointed': True}
    requests = [r for unit in saved['units'] for r in unit['candidate'].get('retrieval_requests', [])]
    return {'text': db.dumps({'paragraphs': paragraphs, 'missing_materials': list(dict.fromkeys(missing)),
                             'visuals': visuals, 'retrieval_requests': requests[:8],
                             'unresolved_claims':list(dict.fromkeys(unresolved))}), 'unit_count': total}


def _semantic(task, section, paragraphs, context, manifest, local):
    """Independent, coverage-checked source entailment and argument review."""
    policy = settings(task['project_id'])
    checks = local['claim_checks']
    if not policy['semantic_review']:
        return {'status': 'not_checked', 'coverage_complete': False, 'claims': [], 'findings': [], 'point_reviews': []}
    if not checks:
        return {'status': 'not_applicable', 'coverage_complete': True, 'claims': [], 'findings': [], 'point_reviews': [],
                'scope': '未识别到需来源核验的实质主张，不代表全文事实正确'}
    system = ('独立核验论文候选，只按实际提供原文核对具体主张、适用条件、数字和推理强度。'
              '不要采信生成器自评，不把目录、规范、范例、源码或计划提升成实测结果。'
              '返回JSON {"claim_reviews":[{"claim_id":"给定编号","support":"supported|partial|unsupported|contradicted|unknown",'
              '"source_quotes":[{"source_id":"实际给定source/unit/evidence/fact编号","quote":"逐字原文"}],"reason":"支持范围和原因"}],'
              '"point_reviews":[{"point_id":"任务点编号","status":"complete|partial|missing|unknown","paragraph_indices":[0],"evidence_text":"完成该任务点的正文原句","reason":"正文依据"}],'
              '"findings":[{"kind":"argument|coherence|condition|number","paragraph_index":0,"message":"具体疑点",'
              '"repair_query":"补证问题"}],"summary":"实际核对范围"}。每个给定主张必须返回一次；'
              'supported必须附可逐字核对的来源摘录；段间衔接要核对前提，不只看连接词。')
    sources = []
    for key in ('sources', 'knowledge_units', 'project_evidence', 'facts'):
        for row in context.get(key, []):
            payload = row.get('payload') or {}
            text = row.get('text') or row.get('statement') or payload.get('text', '')
            fragments=row.get('read_observations') or [{'text':text}]
            for fragment in fragments:
                if not fragment.get('text'):continue
                sources.append({'id': row.get('unit_id') or row.get('id'), 'text': fragment['text'],
                                'kind': row.get('kind', key), 'conditions': row.get('conditions', []),
                                'status': row.get('status'), 'verification': row.get('verification'),
                                'char_start':fragment.get('char_start'),'char_end':fragment.get('char_end'),
                                'scope':'actual_read_fragment'})
    if not sources:
        assertions=[r for r in checks if r['support']!='not_assessed' or re.search(
            r'采用|实现了|已实现|能够|显著|提高|降低|研究发现|测试表明|\d+(?:\.\d+)?\s*(?:%|毫秒|ms|QPS)',r['text'])]
        return {'status': 'incomplete', 'coverage_complete': False, 'claims': [], 'point_reviews': [],
                'unassessed_claims':[r['claim_id'] for r in checks if r not in assertions],
                'findings': [{'kind': 'evidence_missing', 'message': '存在需依据主张，但本次未读到可用于支持的原文',
                              'paragraph_index': r['paragraph_index'], 'repair_query': r['text'], 'claim_id': r['claim_id']}
                             for r in assertions]}
    known={}
    for source in sources:known.setdefault(source['id'],[]).append(source['text'])
    reviews, findings, point_reviews = [], [], []
    complete = True
    for offset in range(0, len(checks), 16):
        chunk = checks[offset:offset + 16]
        request = {'section': section, 'project': context.get('project', {}),
                   'project_evidence': context.get('project_evidence', []),
                   'claims': chunk, 'actual_sources': sources,
                   'candidate_paragraphs': [{'text': p.get('text', '')} for p in paragraphs],
                   'related_sections':context.get('related_sections',[]),
                   'paper_draft_sections':context.get('paper_draft_sections',[]),
                   'task_points': context.get('writing_brief', {}).get('points', [])}
        meta = {**manifest, 'request_options': {**manifest.get('request_options', {}), 'max_output_tokens': min(4096, int(provider.config().get('max_output_tokens', 8192)))}}
        reply = _cached_call(task, system, request, meta, stage='semantic_review', index=offset)
        try:
            value = provider.parse_json(reply['text'])
            actual = value.get('claim_reviews', [])
            expected = {r['claim_id']: r for r in chunk}
            if not isinstance(actual, list):
                raise ValueError('核验结果没有逐项主张列表')
            seen = set()
            for review in actual:
                identity = review.get('claim_id')
                if identity not in expected or identity in seen:
                    raise ValueError('核验返回未知或重复主张')
                seen.add(identity)
                state = review.get('support', 'unknown')
                quotes = review.get('source_quotes', [])
                if state not in {'supported', 'partial', 'unsupported', 'contradicted', 'unknown'}:
                    state = 'unknown'
                valid = isinstance(quotes, list) and bool(quotes) and all(
                    q.get('source_id') in known and isinstance(q.get('quote'), str) and q['quote'].strip() and
                    any(q['quote'] in fragment for fragment in known[q['source_id']]) for q in quotes)
                if state == 'supported' and not valid:
                    state = 'unknown'
                entry = {**review, 'support': state, 'method': 'model_assisted',
                         'paragraph_index': expected[identity]['paragraph_index'], 'quote_verified': valid}
                reviews.append(entry)
                if state != 'supported':
                    findings.append({'kind': 'source_support_' + state, 'claim_id': identity,
                                     'paragraph_index': entry['paragraph_index'], 'message': review.get('reason') or '本主张来源支持仍不充分',
                                     'repair_query': expected[identity]['text']})
            if seen != set(expected):
                complete = False
                findings.append({'kind': 'verification_incomplete', 'message': '核验没有覆盖本批全部主张', 'repair_query': section.get('goal', '')})
            for finding in value.get('findings', []):
                if isinstance(finding, dict) and isinstance(finding.get('message'), str):
                    idx = finding.get('paragraph_index')
                    if idx is None or type(idx) is int and 0 <= idx < len(paragraphs):
                        findings.append(finding)
            for point in value.get('point_reviews', []):
                if isinstance(point, dict):
                    known_points={p['id'] for p in context.get('writing_brief',{}).get('points',[])}
                    if point.get('point_id') not in known_points:continue
                    indices=point.get('paragraph_indices',[])
                    status=point.get('status','unknown')
                    excerpt=point.get('evidence_text','')
                    valid=isinstance(indices,list) and bool(indices) and all(type(i)is int and 0<=i<len(paragraphs) for i in indices)
                    if status=='complete' and not(valid and isinstance(excerpt,str) and excerpt.strip() and any(excerpt in paragraphs[i].get('text','') for i in indices)):
                        status='unknown'
                    point_reviews.append({**point,'status':status,'method':'model_assisted'})
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            complete = False
            findings.append({'kind': 'verification_incomplete', 'message': '独立核验响应未通过契约：' + str(exc),
                             'repair_query': section.get('goal', '')})
    point_map={}
    for point in point_reviews:
        previous=point_map.get(point['point_id'])
        if previous and previous.get('status')!=point.get('status'):
            point_map[point['point_id']]={**point,'status':'unknown','reason':'不同核验批次对任务覆盖存在分歧，需复核实际正文'}
        elif not previous:point_map[point['point_id']]=point
    required_points=[p for p in context.get('writing_brief',{}).get('points',[]) if p.get('required')]
    points_complete=all(point_map.get(p['id'],{}).get('status')=='complete' for p in required_points)
    for point in required_points:
        review=point_map.get(point['id'],{})
        if review.get('status') in ('partial','missing'):
            findings.append({'kind':'task_point_incomplete','message':'实际正文尚未完成任务点：'+point['text'],
                             'repair_query':point['text'],'point_id':point['id'],
                             'paragraph_index':next(iter(review.get('paragraph_indices',[])),None)})
    return {'status': 'checked' if complete else 'incomplete', 'coverage_complete': complete,
            'claims': reviews, 'findings': findings, 'point_reviews': point_reviews,
            'task_coverage':{'status':'checked' if points_complete else 'incomplete','complete':points_complete,
                             'required':len(required_points),'covered':sum(point_map.get(p['id'],{}).get('status')=='complete' for p in required_points)},
            'method': 'model_assisted', 'human_review': 'pending'}


def _local_quality(task, paragraphs, context, manifest):
    from .writing_evidence_quality import check_candidate
    return check_candidate(task['project_id'], paragraphs, context, manifest)


def _originality(pid, section, paragraphs, context):
    if not settings(pid)['originality_check']:
        return {'findings':[],'matches':[],'coverage':{'state':'not_checked'}}
    from .writing_originality import check
    return check(paragraphs,related=[*context.get('related_sections',[]),*context.get('paper_draft_sections',[])],
                 sources=[*context.get('sources',[]),*context.get('knowledge_units',[])],
                 section_kind=section.get('kind','body'),pid=pid)


def _repairable(findings):
    return [r for r in findings if r.get('kind') not in {'verification_incomplete'}]


def finish(task, section, data, system, context, manifest, parser, *, bundle=None):
    """Check, retrieve missing evidence, locally revise and regress, max two rounds."""
    from . import writing_retrieval
    policy = settings(task['project_id'])
    if not policy['enabled']:
        return data, context, manifest, {'schema': VERSION, 'status': 'not_checked', 'findings': []}, bundle
    started = time.perf_counter()
    rounds, research_calls = [], 0
    current = deepcopy(data)
    local = _local_quality(task, current['paragraphs'], context, manifest)
    semantic = _semantic(task, section, current['paragraphs'], context, manifest, local)
    originality=_originality(task['project_id'],section,current['paragraphs'],context)
    findings = [*local['findings'], *semantic['findings'],*originality['findings']]
    for iteration in range(policy['max_revision_rounds']):
        requests = current.get('retrieval_requests', [])
        issues = _repairable(findings)
        if not requests and not issues:
            break
        from .tasks import event
        event(task['id'], f'补充依据并定向修订，第{iteration + 1}轮', 78 + iteration * 3)
        queries = [str(r.get('query') or r.get('question') or '') for r in requests if isinstance(r, dict)]
        queries += [str(r.get('repair_query') or r.get('message', '')) for r in issues[:6]]
        query = '；'.join(dict.fromkeys(q for q in queries if q))[:5000] or section['title']
        additional = writing_retrieval.prepare(task['project_id'], section['id'], query,
                       operation=context.get('operation', 'revise'), max_chars=policy['max_material_chars'])
        merge_context(context, writing_retrieval.for_writing(additional))
        bundle=writing_retrieval.combine(task['project_id'],section['id'],[old for old in (bundle,additional) if old],query,operation=context.get('operation','generate'))
        _manifest_bundle(manifest,bundle)
        context['evidence_bundle'] = writing_retrieval.for_model(bundle)
        sync_manifest(task['project_id'], context, manifest)
        dynamic = None
        if requests and research_calls < policy['max_tool_calls']:
            from .writing_tool_runtime import collect
            dynamic = collect(task, section, query, operation=context.get('operation', 'generate'),
                              initial_context={'requests': requests, 'rules': context.get('rules'),
                                               'terminology': context.get('terminology', [])},
                              initial_manifest=manifest, max_rounds=policy['max_research_rounds'],
                              max_tool_calls=policy['max_tool_calls'] - research_calls)
            research_calls += len(dynamic.get('tool_observations', []))
            dynamic_bundles=dynamic.get('material_bundles', [])
            for read_bundle in dynamic_bundles:
                merge_context(context, writing_retrieval.for_writing(read_bundle))
            if dynamic_bundles:
                bundle=writing_retrieval.combine(task['project_id'],section['id'],[bundle,*dynamic_bundles],query,operation=context.get('operation','generate'))
                _manifest_bundle(manifest,bundle)
                context['evidence_bundle']=writing_retrieval.for_model(bundle)
            from .writing_agent import evidence as evidence_service
            dynamic_evidence=[evidence_service.get(task['project_id'],eid,remote=True) for eid in dynamic.get('evidence_ids',[])]
            merge_context(context,{'project_evidence':[{**evidence_service.for_model(e),'origin_type':e['origin_type'],
                             'origin_version':e['origin_version'],'file_id':e['payload'].get('file_id')} for e in dynamic_evidence]})
            context.update(dynamic.get('context', {}))
            for key, value in dynamic.get('manifest', {}).items():
                if key in ('evidence_versions', 'permission_stamps'):
                    manifest[key] = {**manifest.get(key, {}), **value}
            sync_manifest(task['project_id'], context, manifest)
        candidate_context = {**context, 'candidate_to_repair': current,
                             'repair_findings': issues, 'repair_iteration': iteration + 1,
                             'repair_policy': '仅修改问题句段；正确内容原样保留。补读仍不支持时收缩/删除断言或明确待补；不得只删引文而保留强断言。'}
        reply = _cached_call(task, system + ' 当前是依据补齐后的局部修订。返回完整候选JSON，但只改有问题句段；原有事实、数值、条件和引用不得无故改变。',
                             candidate_context, manifest, stage='evidence_revision', index=iteration)
        revised, raw, problem = parser(reply['text'])
        if revised is None:
            rounds.append({'iteration': iteration + 1, 'accepted': False, 'reason': problem})
            break
        next_local = _local_quality(task, revised['paragraphs'], context, manifest)
        next_semantic = _semantic(task, section, revised['paragraphs'], context, manifest, next_local)
        next_originality=_originality(task['project_id'],section,revised['paragraphs'],context)
        next_findings = [*next_local['findings'], *next_semantic['findings'],*next_originality['findings']]
        # A repair cannot improve by removing all substantive prose or changing
        # unrelated already-supported propositions. Count is subordinate to this.
        supported={r['claim_id'] for r in semantic['claims'] if r['support']=='supported'}
        affected_claims={r.get('claim_id') for r in issues if r.get('claim_id')}
        protected_claims=[r for r in local['claim_checks'] if r['claim_id'] in supported-affected_claims]
        retained=True
        evidence_fields=('source_ids','reference_ids','fact_ids','evidence_ids','knowledge_unit_ids','document_reading_ids')
        def claim_links(paragraph,text):
            values={key:set(paragraph.get(key,[])) for key in evidence_fields}
            matching_spans=[span for span in paragraph.get('claim_spans',[]) if span.get('text') in text or text in span.get('text','')]
            ids={span.get('local_id') for span in matching_spans}
            for span in matching_spans:
                for key in evidence_fields:values[key].update(span.get(key,[]))
            intents=[]
            for intent in paragraph.get('citation_intents',[]):
                if intent.get('claim_id') not in ids:continue
                intents.append({key:intent.get(key) for key in ('reference_id','kind','quote','source_version')})
                if intent.get('reference_id'):values['reference_ids'].add(intent['reference_id'])
                for key in evidence_fields:values[key].update(intent.get(key,[]))
            return values,intents
        for claim in protected_claims:
            matching=[p for p in revised['paragraphs'] if claim['text'] in p.get('text','')]
            old=current['paragraphs'][claim['paragraph_index']]
            old_links,old_intents=claim_links(old,claim['text'])
            def preserves(paragraph):
                new_links,new_intents=claim_links(paragraph,claim['text'])
                return all(old_links[key]<=new_links[key] for key in evidence_fields) and all(intent in new_intents for intent in old_intents)
            if not any(preserves(p) for p in matching):
                retained=False;break
        critical=lambda rows:sum(r.get('severity') in ('blocker','error') or r.get('kind') in ('result_evidence_level','source_support_contradicted') for r in rows)
        no_regression=critical(next_findings)<=critical(findings)
        improved = retained and no_regression and (len(next_findings) < len(findings) or bool(requests) and not revised.get('retrieval_requests') and len(next_findings) <= len(findings))
        rounds.append({'iteration': iteration + 1, 'accepted': improved,
                       'before_findings': len(findings), 'after_findings': len(next_findings),
                       'correct_paragraphs_preserved': retained, 'dynamic_research': bool(dynamic)})
        if not improved:
            break
        current, local, semantic, findings,originality = revised, next_local, next_semantic, next_findings,next_originality
    coverage_complete=semantic['coverage_complete'] and not local['findings'] and semantic.get('task_coverage',{}).get('complete',False)
    report_status='needs_review' if findings else 'checked' if coverage_complete else 'not_checked' if semantic['status']=='not_checked' else 'incomplete'
    report = {'schema': VERSION, 'status':report_status,
              'mapping': {'status': 'issues' if local['findings'] else 'checked', 'method': 'deterministic'},
              'semantic': semantic, 'originality':originality,'findings': findings, 'repair_rounds': rounds,
              'tool_calls': research_calls, 'duration_ms': round((time.perf_counter() - started) * 1000, 2),
              'coverage_complete':coverage_complete,
              'candidate_hash': digest(current), 'argument_plan_hash': context.get('argument_plan', {}).get('hash'),
              'human_review': 'pending', 'policy': '模型辅助检查不代替人工审阅；未覆盖范围不计通过'}
    current['missing_materials'] = list(dict.fromkeys([*current.get('missing_materials', []),*current.get('unresolved_claims',[]),
        *[f['message'] for f in findings if f.get('kind') in ('claim_evidence_gap', 'evidence_missing', 'result_evidence_level')]]))
    report['candidate_hash']=digest(current)
    report['hash'] = digest(report)
    manifest['writing_quality_hash'] = report['hash']
    manifest['writing_quality_policy'] = digest(policy)
    manifest['writing_quality_blockers']=[f for f in findings if f.get('kind') in (
        'result_evidence_level','claim_mapping_invalid','citation_mapping_invalid','citation_source_invalid','claim_span_invalid','citation_evidence_gap')]
    return current, context, manifest, report, bundle
