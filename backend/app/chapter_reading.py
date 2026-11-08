"""Receipt-backed chapter reading before either whole-chapter or unit drafting."""
from copy import deepcopy
from fastapi import HTTPException
from . import db, documents, security, provider

SYSTEM=('为本次写作选择必须补读的论文章节。当前章和依赖章已提供，其他章只有目录和导航。'
        '目录不是证据；无需补读时返回空数组。只返回JSON {"chapter_read_requests":['
        '{"section_id":"目录ID","revision_id":"目录版本","node_ids":[],"reason":"与本次任务的关系"}]}。'
        'node_ids为空表示完整章，否则读取指定完整段落；每轮最多3章，最多2轮。'
        '补读旧稿只用于连贯性，不能据此证明实测结果。不得请求目录以外的版本。')


def expand(pid, context, manifest, requests):
    """Only exact frozen catalogue versions and complete author blocks may enter."""
    if not isinstance(requests,list) or len(requests)>3:raise HTTPException(422,'每轮最多补读3章')
    catalogue={r['section_id']:r for r in context.get('section_catalogue',[])}
    additions=[]
    from .writing_agent.provenance import revision_permissions
    for request in requests:
        if not isinstance(request,dict) or set(request)-{'section_id','revision_id','node_ids','reason'}:
            raise HTTPException(422,'章节补读契约无效')
        sid=request.get('section_id');entry=catalogue.get(sid)
        if not entry or request.get('revision_id')!=entry['revision_id']:raise HTTPException(409,'补读章节不在冻结目录内')
        sec=security.require_section(sid)
        if sec['project_id']!=pid or sec['state']=='archived' or sec['current_revision_id']!=entry['revision_id']:
            raise HTTPException(409,'章节补读版本已变化')
        revision_permissions(pid,entry['revision_id'])
        ids=request.get('node_ids',[])
        if not isinstance(ids,list) or len(ids)>30 or any(not isinstance(n,str) for n in ids) or len(set(ids))!=len(ids):
            raise HTTPException(422,'补读段落标识无效')
        reason=request.get('reason','')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>1000:raise HTTPException(422,'补读须说明原因')
        rev=documents.current(sec);blocks=db.loads(rev['document'],{}).get('content',[])
        selected=[{'paragraph_index':i,'node_id':n.get('attrs',{}).get('nodeId'),'text':documents.plain(n)}
                  for i,n in enumerate(blocks) if not ids or n.get('attrs',{}).get('nodeId') in ids]
        if ids and {p['node_id'] for p in selected}!=set(ids):raise HTTPException(409,'补读段落不属于指定版本')
        additions.append({'section_id':sid,'revision_id':rev['id'],'title':sec['title'],
            'scope':'requested_whole_paragraphs' if ids else 'requested_full_section','paragraphs':selected,
            'text':'\n'.join(p['text'] for p in selected) if ids else rev['text'],
            'reason':reason,'partial':bool(ids),'evidence':False})
    # Atomic in memory: errors above cannot leave half of a round provided.
    for row in additions:
        context.setdefault('chapter_readings',[]).append(row)
        manifest.setdefault('chapter_readings',[]).append({k:deepcopy(v) for k,v in row.items() if k not in ('text','paragraphs') } |
            {'node_ids':[p['node_id'] for p in row['paragraphs']], 'content_hash':security.put_blob(row['text'].encode())})
        manifest.setdefault('related_revisions',{})[row['section_id']]=row['revision_id']
    summary=context.get('paper_context',{})
    summary['requested_read_section_ids']=sorted({r['section_id'] for r in context.get('chapter_readings',[])})
    manifest.setdefault('paper_context',{}).update(requested_read_section_ids=summary['requested_read_section_ids'],
        requested_readings=deepcopy(manifest.get('chapter_readings',[])))
    return additions


def prepare(task, section, state):
    context=state['context'];manifest=state['manifest']
    if not context.get('section_catalogue'):return state
    from .writing_flow import _cached_call, _stage_budget
    from .tasks import _validate_model_source_inputs, _validate_model_presend
    trace=[]
    with provider.attempt_budget(2):
        for index in range(2):
            reply=_cached_call(task,SYSTEM,context,manifest,stage='chapter_read_plan',index=index)
            value=provider.parse_json(reply['text'])
            if not isinstance(value,dict) or 'chapter_read_requests' not in value:raise HTTPException(422,'章节阅读计划无效，原响应已保留')
            requests=value['chapter_read_requests']
            rows=expand(task['project_id'],context,manifest,requests)
            trace.append({'round':index,'run_id':reply.get('run_id'),'requests':requests,'loaded_sections':[r['section_id'] for r in rows]})
            _validate_model_source_inputs(task['project_id'],manifest);_validate_model_presend(task,manifest)
            budget=_stage_budget(state['system'],context,manifest)
            if budget['input_upper_bound']+budget['reserved_output']+state['format_repair_input_reserve']>budget['context_limit']:
                raise HTTPException(422,{'code':'CHAPTER_READING_CONTEXT_EXCEEDED','message':'补读的完整原文超过写作预算，未裁剪原文或继续起草','reading_trace':trace})
            if not rows:break
    manifest['chapter_reading_trace']=trace
    context['chapter_reading_status']={'rounds':len(trace),'maximum_rounds':2,'scope':'actual_readings_only',
        'notice':'未读取目录仍待核对；不得声称全文已通读'}
    return {**state,'context':context,'manifest':manifest}
