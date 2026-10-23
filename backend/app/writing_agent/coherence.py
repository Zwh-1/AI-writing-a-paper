from pydantic import Field
from .models import Verification,Strict
from .common import fail

def validate(data,sentence_ids,node_ids):
    result=Verification.model_validate(data).model_dump()
    for f in result['findings']:
        if f['sentence_id'] and f['sentence_id'] not in sentence_ids:fail('INVALID_FINDING','核验结果指向不存在的句子')
        if f['node_id'] and f['node_id'] not in node_ids:fail('INVALID_FINDING','核验结果指向不存在的节点')
    return result

class ContextRequest(Strict):
    section_id:str
    revision_id:str
    node_id:str=''
    before:int=Field(default=2,ge=0,le=8)
    after:int=Field(default=2,ge=0,le=8)
    reason:str=Field(min_length=1,max_length=1000)

class ExpandedReview(Verification):
    context_requests:list[ContextRequest]=Field(default_factory=list,max_length=4)

def run(task,profile,payload,manifest,step_id,assert_active=lambda:None):
    """Resolve missing premises by bounded, logged reads of immutable original paragraphs."""
    from .. import db,provider
    from . import provider_adapter,context,tools,profiles,prompts
    from .common import digest
    from .provenance import revision_permissions
    from .sentences import paragraphs
    pid=task['project_id'];_,cfg,_=profiles.for_session(pid,profile['id']);data=dict(payload);versions={};history=[]
    for s in db.rows("SELECT id,title,current_revision_id FROM sections WHERE project_id=? AND state!='archived' AND current_revision_id IS NOT NULL",(pid,)):
        versions[s['id']]=s['current_revision_id']
    data['available_section_versions']=[{'section_id':sid,'revision_id':rid} for sid,rid in versions.items()]
    data['expanded_original_passages']=[]
    system=prompts.COHERENCE+' 若相关定义、指代或限定不在当前原文中，返回context_requests列表，每项含section_id、revision_id、node_id（空表示从首段开始）、before、after、reason。只能请求给定正文版本；工具会读取完整段落。已读范围返回前后节点供继续扩展。请求尚未满足时coverage_complete必须为false；最多扩展3轮。'
    for turn in range(4):
        assert_active();context.save_context(step_id,data,manifest,system,cfg)
        raw=provider_adapter.call(task,profile,system,data,{**manifest,'coherence_expansion_round':turn})
        result=ExpandedReview.model_validate(provider.parse_json(raw['text'])).model_dump();requests=result.pop('context_requests');history.append({'round':turn,'requests':requests,'result':result})
        if not requests:return {**result,'expansion_history':history,'expanded_original_passages':data['expanded_original_passages']},manifest
        if turn==3:return {**result,'coverage_complete':False,'unresolved_context_requests':requests,'expansion_history':history},manifest
        additions=[];new_manifest={**manifest,'related_revisions':dict(manifest.get('related_revisions',{}))}
        for index,request in enumerate(requests):
            sid=request['section_id'];rid=request['revision_id']
            if versions.get(sid)!=rid:fail('INPUT_VERSION_CHANGED','扩展请求不属于本次列出的冻结正文版本',409)
            revision_permissions(pid,rid)
            arguments={key:request[key] for key in ('section_id','revision_id','node_id','before','after')}
            observed=tools.execute(pid,step_id,'read_section_range',arguments,'coherence-'+str(turn)+'-'+str(index)+'-'+digest(arguments)[:12],manifest['target_snapshot_id'])
            additions.append(observed['data']);new_manifest['related_revisions'][sid]=rid
        new_data={**data,'expanded_original_passages':[*data['expanded_original_passages'],*additions]}
        try:context.save_context(step_id,new_data,new_manifest,system,cfg)
        except Exception as error:
            from fastapi import HTTPException
            if not isinstance(error,HTTPException) or not isinstance(error.detail,dict) or error.detail.get('code')!='CONTEXT_BUDGET_EXCEEDED':raise
            return {**result,'coverage_complete':False,'unresolved_context_requests':requests,'context_budget_gap':error.detail,'expansion_history':history},manifest
        data=new_data;manifest=new_manifest
