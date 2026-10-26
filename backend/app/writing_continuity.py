"""Versioned, literal context for writing one part of a continuous paper."""
from fastapi import HTTPException
from . import db,documents

VERSION='paper_continuity.v2.full_manuscript'
POLICY='本次按整篇已保存论文理解任务：outline是全篇结构，related_sections包含其他所有已保存章节的完整正文，current_section_context包含当前章完整正文，paper_context列出实际读取的章节版本与字数。先通读全部正文，再依据本次operation只输出当前章或选区的候选，其他章节只读，不得复制进输出。检查研究问题、方法、结果、术语、数值、范围、章节分工和跨章引用的一致性。已有正文与原始证据冲突时列出疑点，不沿用错误，不把计划改写为已完成。旧稿用于全文一致性参照，不自动成为成果证据。selection_context中的前后文只读。尚未保存或没有正文的章节只有提纲，不得假定其内容。'
OPTIONAL_SCOPES={'complete_boundary_paragraphs','cross_section_passages'}


def outline(pid):
    rows=db.rows("SELECT id,title,kind,goal,parent_id,dependencies,current_revision_id FROM sections WHERE project_id=? AND state!='archived' ORDER BY position,id",(pid,))
    return [{**row,'dependencies':db.loads(row['dependencies'],[])} for row in rows]


def outline_hash(rows):
    from .writing_agent.common import digest
    return digest([{key:value for key,value in row.items() if key!='current_revision_id'} for row in rows])


def collect(pid,sid,required_sections=(),remote=True,permission_check=None):
    rows=outline(pid);at=next(i for i,row in enumerate(rows) if row['id']==sid)
    required=set(required_sections)|set(rows[at]['dependencies'])
    related=[];versions={};files=set();summaries=[];current=None;empty_versions={}
    for i,row in enumerate(rows):
        identity=row['id']
        if not row['current_revision_id']:
            summaries.append({'section_id':identity,'title':row['title'],'revision_id':None,'characters':0,'has_content':False})
            if identity!=sid:empty_versions[identity]=None
            continue
        rev=documents.current(row)
        if remote:
            try:
                if permission_check:
                    allowed,origins=permission_check(rev)
                    if not allowed:raise HTTPException(403,{'code':'SOURCE_ACCESS_REVOKED','message':'正文来源禁止外发'})
                    files.update(origins)
                from .writing_agent.provenance import revision_permissions
                revision_permissions(pid,rev['id'])
            except HTTPException as error:
                if error.status_code!=403:raise
                raise HTTPException(403,{'code':'MANUSCRIPT_CONTEXT_UNAVAILABLE','message':f'无法读取全文上下文：“{row["title"]}”的正文来源不允许外发。请处理来源权限后重试；未退回局部上下文。','section_id':identity}) from error
        item={'section_id':identity,'title':row['title'],'revision_id':rev['id'],'text':rev['text'],'scope':'full_required_dependency' if identity in required else 'full_manuscript_section','relation':'current' if identity==sid else 'earlier' if i<at else 'later','partial':False}
        summaries.append({'section_id':identity,'title':row['title'],'revision_id':rev['id'],'characters':len(rev['text']),'has_content':bool(rev['text'].strip())})
        if identity==sid:current=item
        else:related.append(item);versions[identity]=rev['id']
    summary={'scope':'full_saved_manuscript','version':VERSION,'total_sections':len(rows),'saved_sections':sum(row['has_content'] for row in summaries),'characters':sum(row['characters'] for row in summaries),'sections':summaries,'complete':True}
    payload={'outline':rows,'related_sections':related,'current_section_context':current,'paper_context':summary,'continuity':{'version':VERSION,'policy':POLICY,'coverage':'所有未归档章节已保存的完整正文；无截断、无选段、无自动摘要'}}
    return payload,{'related_revisions':versions,'empty_section_revisions':empty_versions,'continuity_outline_hash':outline_hash(rows),'paper_context':summary},files


def selection_context(document,target_ids):
    targets=set(target_ids);blocks=document.get('content',[])
    positions=[i for i,node in enumerate(blocks) if node.get('attrs',{}).get('nodeId') in targets]
    if not positions:return None
    first,last=min(positions),max(positions)
    def view(nodes):
        return [{'node_id':n.get('attrs',{}).get('nodeId'),'text':documents.plain(n)} for n in nodes]
    return {'before':view(blocks[max(0,first-2):first]),'after':view(blocks[last+1:last+3]),'read_only':True,'instruction':'只输出选中段落的替换稿；承接before，衔接after，保持指代与术语一致，不重复输出前后文。'}


def status(pid,sid,cfg=None):
    """Preview actual saved text coverage without calling a model."""
    from . import provider
    from .source_payload import token_upper_bound
    try:payload,_,_=collect(pid,sid)
    except HTTPException as error:
        if error.status_code!=403:raise
        detail=error.detail if isinstance(error.detail,dict) else {'message':str(error.detail)}
        return {'section_id':sid,'can_load':False,'code':detail.get('code'),'message':detail.get('message'),'scope':'full_saved_manuscript','complete':False}
    cfg=cfg or provider.config();estimate=token_upper_bound(POLICY,db.dumps(payload));reserve=int(cfg.get('max_output_tokens',8192));limit=int(cfg.get('context_limit',100000))
    fits=estimate+reserve<=limit
    return {**payload['paper_context'],'section_id':sid,'can_load':fits,'minimum_input_estimate':estimate,'reserved_output':reserve,'context_limit':limit,'message':'' if fits else '完整论文正文已超过当前接口上下文预算，请调整接口上下文配置或拆分论文项目后重试；未截断正文或退回局部上下文。','code':None if fits else 'CONTEXT_BUDGET_EXCEEDED'}


def fit_optional(payload,manifest,fits):
    """Drop remote excerpts before boundary passages, retaining exact version scope."""
    expanded={row['section_id'] for row in payload.get('expanded_original_passages',[]) if row.get('section_id')}
    optional=[row for row in payload.get('related_sections',[]) if row.get('scope') in OPTIONAL_SCOPES and row['section_id'] not in expanded]
    optional.sort(key=lambda row:row.get('scope')!='cross_section_passages')
    for row in optional:
        if fits():break
        payload['related_sections'].remove(row)
        manifest.get('related_revisions',{}).pop(row['section_id'],None)
        omission={'section_id':row['section_id'],'revision_id':row['revision_id'],'reason':'衔接上下文预算不足；此选段未读取，需要时另行读取完整原文'}
        payload.setdefault('omitted_optional_boundary_sections',[]).append(omission)
        manifest['optional_context_omissions']=list(payload['omitted_optional_boundary_sections'])
