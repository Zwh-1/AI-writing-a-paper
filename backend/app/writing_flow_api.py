"""Authenticated controls and readable plans/reports for the shared writing flow."""
from fastapi import APIRouter, HTTPException
from . import db, security, writing_flow

router = APIRouter()


@router.get('/projects/{pid}/writing-flow/metrics')
def metrics(pid: str):
    """Observed costs and coverage only; never manufacture quality improvements."""
    security.require_project(pid)
    runs=db.rows("SELECT g.* FROM generation_runs g JOIN tasks t ON t.id=g.task_id WHERE g.project_id=? AND t.kind IN ('write','write_paper','studio')",(pid,))
    by_stage={};unknown=0;known_cost=0.0
    for run in runs:
        manifest=db.loads(run['manifest'],{})
        stage=manifest.get('writing_stage') or manifest.get('studio_action') or manifest.get('stage') or 'draft'
        value=by_stage.setdefault(stage,{'requests':0,'input_tokens':0,'output_tokens':0,'known_cost':0.0,'unknown_cost_requests':0,'statuses':{}})
        value['requests']+=1
        value['statuses'][run['status']]=value['statuses'].get(run['status'],0)+1
        usage=db.loads(run['usage'],{})
        for key in ('input_tokens','output_tokens'):
            token=usage.get(key)
            if isinstance(token,(int,float)) and not isinstance(token,bool):value[key]+=token
        if run['cost'] is None:
            unknown+=1;value['unknown_cost_requests']+=1
        else:
            value['known_cost']+=run['cost'];known_cost+=run['cost']
    tasks_rows=db.rows("SELECT result FROM tasks WHERE project_id=? AND kind IN ('write','write_paper') AND result IS NOT NULL",(pid,))
    checked=0;incomplete=0;durations=[];words=0
    from .paper_review_checks import word_count
    for row in tasks_rows:
        result=db.loads(row['result'],{})
        for item in result.get('paper_sections',[result]):
            quality=item.get('writing_quality')
            if not quality:continue
            if quality.get('coverage_complete') and not quality.get('findings'):
                checked+=1;words+=word_count(item.get('text',''))
            else:incomplete+=1
            if isinstance(quality.get('duration_ms'),(int,float)):durations.append(quality['duration_ms'])
    durations.sort()
    p95=durations[max(0,__import__('math').ceil(len(durations)*.95)-1)] if durations else None
    return {'project_id':pid,'stages':by_stage,'known_cost':known_cost,'unknown_cost_requests':unknown,
            'checked_candidates':checked,'incomplete_candidates':incomplete,'checked_candidate_words':words,
            'known_cost_per_checked_thousand_words':known_cost*1000/words if words and not unknown else None,
            'quality_p95_ms':p95,'quality_observations':len(durations),
            'scope':'按当前本地记录计算；模型辅助检查不是人工验收，未知费用不按零计，未建立对照基线不报告提升百分比'}


@router.get('/projects/{pid}/writing-flow/settings')
def get_settings(pid: str):
    return writing_flow.settings(pid)


@router.put('/projects/{pid}/writing-flow/settings')
def put_settings(pid: str, body: dict):
    return writing_flow.save_settings(pid, body)


def _plan(pid, sid):
    from . import writing_argument, writing_brief, writing_ops, writing_retrieval
    section = security.require_section(sid)
    if section['project_id'] != pid:
        raise HTTPException(404, '章节不属于当前项目')
    project = security.require_project(pid)
    bundle = writing_retrieval.prepare(pid, sid, section['title'] + ' ' + section['goal'], max_chars=4000)
    context = writing_retrieval.for_writing(bundle)
    head = db.one('SELECT * FROM writing_project_state WHERE project_id=?', (pid,))
    core = db.one('SELECT content_json FROM thesis_core_versions WHERE id=? AND project_id=?',
                  (head['core_version_id'], pid)) if head and head['core_version_id'] else None
    contract_id = db.loads(head['contract_heads_json'], {}).get(sid) if head else None
    contract = db.one('SELECT content_json FROM section_contract_versions WHERE id=? AND project_id=?',
                      (contract_id, pid)) if contract_id else None
    context['thesis_core'] = db.loads(core['content_json'], {}) if core else {}
    context['section_contract'] = db.loads(contract['content_json'], {}) if contract else {}
    brief = writing_brief.build(section, project, context['section_contract'], context['facts'], context['sources'],
                                 [], context['thesis_core'].get('research_question', ''), writing_ops.params_for(pid, 'section'))
    return writing_argument.build(pid, section, context, brief)


@router.get('/projects/{pid}/writing-plan')
def get_plan(pid: str, section_id: str):
    return _plan(pid, section_id)


@router.put('/projects/{pid}/writing-plan')
def put_plan(pid: str, body: dict, section_id: str = ''):
    from .writing_argument import save
    sid = section_id or body.get('section_id')
    return save(pid, sid, _plan(pid, sid), body)


@router.get('/projects/{pid}/writing-quality/{tid}')
def quality(pid: str, tid: str, section_id: str = ''):
    security.require_project(pid)
    task = db.one('SELECT * FROM tasks WHERE id=? AND project_id=?', (tid, pid))
    if not task:
        raise HTTPException(404, '写作任务不存在')
    result = db.loads(task['result'], {}) or {}
    if result.get('paper_sections'):
        items = result['paper_sections']
        if section_id:
            items = [r for r in items if r['section_id'] == section_id]
            if not items:
                raise HTTPException(404, '任务没有该章节候选')
    else:
        items = [result]
    from .writing_agent.impact import check_manifest
    return {'task_id': tid, 'status': task['status'], 'sections': [
        {'section_id': item.get('section_id'), 'argument_plan': item.get('argument_plan'),
         'evidence_bundle': item.get('evidence_bundle'), 'writing_quality': item.get('writing_quality'),
         'context_coverage': item.get('context_coverage'),
         'stale_reasons': check_manifest(pid, item.get('manifest', {})) if item.get('manifest') else []}
        for item in items], 'scope': '候选与模型辅助报告；不表示人工审阅通过'}
