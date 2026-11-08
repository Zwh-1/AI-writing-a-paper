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


def collect(pid,sid,required_sections=(),remote=True,permission_check=None,*,mode=None,task=None):
    from .writing_flow import task_settings
    mode=mode or task_settings(pid,task)['context_mode']
    hierarchical=mode in ('argument_scoped','hierarchical_v1')
    rows=outline(pid);at=next(i for i,row in enumerate(rows) if row['id']==sid)
    required=set(required_sections)|set(rows[at]['dependencies'])
    by_id={row['id']:row for row in rows}
    seen=set();active={sid}
    def visit(identity):
        if identity in active:raise HTTPException(409,'章节依赖存在循环，无法准备上下文')
        if identity not in by_id:raise HTTPException(409,'章节依赖已不存在或已归档')
        if identity in seen:return
        active.add(identity)
        for dependency in by_id[identity]['dependencies']:
            required.add(dependency);visit(dependency)
        active.remove(identity);seen.add(identity)
    for identity in list(required):visit(identity)
    related=[];versions={};files=set();summaries=[];current=None;empty_versions={};navigation=[];anchors=[]
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
        if hierarchical:
            from .manuscript_anchors import catalogue as anchor_catalogue
            anchors.extend(anchor_catalogue(row,rev,[(j,n,documents.plain(n)) for j,n in enumerate(db.loads(rev['document'],{}).get('content',[]))]))
        item={'section_id':identity,'title':row['title'],'revision_id':rev['id'],'text':rev['text'],'scope':'full_required_dependency' if identity in required else 'full_manuscript_section','relation':'current' if identity==sid else 'earlier' if i<at else 'later','partial':False}
        summaries.append({'section_id':identity,'title':row['title'],'revision_id':rev['id'],'characters':len(rev['text']),'has_content':bool(rev['text'].strip())})
        if identity==sid:current=item
        else:
            versions[identity]=rev['id']
            if not hierarchical or identity in required:related.append(item)
            else:
                from .project_memory import chapter_navigation
                nav=chapter_navigation(row,rev,limit=500)
                navigation.append({'section_id':identity,'revision_id':rev['id'],'title':row['title'],
                    'goal':row.get('goal',''),'characters':len(rev['text']),
                    'scope':'catalogue_and_selected_paragraphs','read_tool':'read_section','evidence':False,
                    'navigation':nav})
    summary={'scope':'full_saved_manuscript','version':VERSION,'total_sections':len(rows),'saved_sections':sum(row['has_content'] for row in summaries),'characters':sum(row['characters'] for row in summaries),'sections':summaries,'complete':True}
    payload={'outline':rows,'related_sections':related,'current_section_context':current,'paper_context':summary,'continuity':{'version':VERSION,'policy':POLICY,'coverage':'所有未归档章节已保存的完整正文；无截断、无选段、无自动摘要'}}
    if hierarchical:
        summary.update(scope='current_and_dependencies',version='paper_continuity.v3.hierarchical',
            complete=not navigation,full_manuscript_provided=not navigation,
            provided_section_ids=[r['section_id'] for r in [*related,*([current] if current else [])]],
            catalogue_section_ids=[r['section_id'] for r in navigation],
            provided_characters=sum(len(r['text']) for r in [*related,*([current] if current else [])]))
        payload['section_catalogue']=navigation
        payload['manuscript_anchor_index']={'scope':'literal_numeric_mentions_for_navigation','evidence':False,
            'entries':anchors,'notice':'数值来自旧稿，须按版本和段落补读核对；同名指标不同值仅为待核对线索'}
        payload['continuity']={'version':summary['version'],'policy':
            '本次只提供当前章及明确依赖章的完整原文。section_catalogue只是未阅读目录，不支持事实或全文已通读声明。'
            '需要其他章时在阅读阶段按目录版本请求chapter_read_requests，或使用read_section；读取结果仍需核对来源，旧稿不成为项目成果证据。'
            '检查本次实际读取范围内的衔接、术语、数值和章节分工，未读取范围保留待检查状态。',
            'coverage':'当前章与依赖章完整原文；其他已保存章节仅提供版本目录'}
    from .project_memory import task_context
    memory=task_context(pid,rows[at]['title']+' '+rows[at].get('goal',''),sid,task)
    memory_visible=bool(memory['items'] or memory.get('deferred') or memory.get('conflicts'))
    if memory_visible:payload['project_memory']=memory
    manifest={'related_revisions':versions,'empty_section_revisions':empty_versions,'continuity_outline_hash':outline_hash(rows),'paper_context':summary}
    if memory_visible:
        manifest['project_memory']=memory
        from .resource_refs import source_files
        files.update(resource_file for item in memory['items'] for resource_file in source_files(pid,item['source_refs']))
    return payload,manifest,files


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
    cfg=cfg or provider.config();estimate=token_upper_bound(payload['continuity']['policy'],db.dumps(payload));reserve=int(cfg.get('max_output_tokens',8192));limit=int(cfg.get('context_limit',100000))
    fits=estimate+reserve<=limit
    message=''
    if not fits:
        message=('当前章、依赖章和必要导航已超过接口上下文预算，请调整容量或检查章节依赖；未截断必要原文。'
                 if payload['paper_context']['scope']=='current_and_dependencies' else
                 '完整论文正文已超过接口上下文预算，请选择分层模式或调整接口容量；未截断正文。')
    return {**payload['paper_context'],'section_id':sid,'can_load':fits,'minimum_input_estimate':estimate,'reserved_output':reserve,'context_limit':limit,'message':message,'code':None if fits else 'CONTEXT_BUDGET_EXCEEDED'}


def fit_optional(payload,manifest,fits):
    """Drop remote excerpts before boundary passages, retaining exact version scope."""
    if not fits() and payload.get('section_catalogue'):
        # Keep the version catalogue while deferring whole navigation extracts.
        for item in payload['section_catalogue']:
            if fits():break
            if item.get('navigation'):
                item.pop('navigation');item['scope']='catalogue_only'
                manifest.setdefault('navigation_omissions',[]).append(item['section_id'])
    if not fits() and payload.get('knowledge_context'):
        payload.pop('knowledge_context')
        manifest.setdefault('knowledge_omissions',[]).append({'reason':'派生综合超过上下文预算；保留原文证据、完整正文与强制规则'})
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
