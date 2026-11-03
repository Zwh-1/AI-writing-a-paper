"""Prepare actual document reading for writing; ranking only selects documents."""
from fastapi import HTTPException
from . import db,security,knowledge_units as units,document_reading as reading


def prepare(pid,query,sid,limit=8,max_chars=60000,findings=None,max_bytes=None):
    from .knowledge_service import search
    security.require_project(pid)
    section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于当前项目')
    findings=findings if findings is not None else search(pid,query,limit,remote=True)
    selected=[r['unit_id'] for r in db.rows('SELECT unit_id FROM knowledge_selections WHERE section_id=?',(sid,))]
    document_candidates=[];seen=set()
    for hit in findings['items']:
        if hit['file_id'] not in seen:
            seen.add(hit['file_id']);document_candidates.append({'file_id':hit['file_id'],'title':hit.get('reference_title') or hit['name'],'selection_basis':'retrieval_candidate_not_factual_support'})
    records=[];gaps=[];passages=[];reading_contexts=[];stale_files=set();covered_files=set()
    chapter_readings=db.rows("SELECT id,file_id FROM document_readings WHERE project_id=? AND section_id=? AND intent='research' ORDER BY (question=?) DESC,updated_at DESC",(pid,sid,query))
    remaining=max_chars
    byte_remaining=max_bytes
    for row in chapter_readings:
        try:context=reading.context(pid,row['id'],remote=True)
        except HTTPException as exc:
            if exc.status_code not in (403,404,409):raise
            gaps.append({'reading_id':row['id'],'reason':'该章节阅读的原件、任务、用途或权限已变化，请重新阅读核对；未用检索片段替代'})
            stale_files.add(row['file_id'])
            continue
        if context['passages']:
            size=sum(len(p['text']) for p in context['passages'])
            byte_size=sum(len(p['text'].encode('utf-8')) for p in context['passages'])
            if size>remaining or byte_remaining is not None and byte_size>byte_remaining:
                gaps.append({'reading_id':row['id'],'reason':'当前阅读范围超过剩余文本预算，请缩小本次问题范围；未截断原文'})
                continue
            remaining-=size
            if byte_remaining is not None:byte_remaining-=byte_size
            if context.get('parsed_text_coverage_complete'):covered_files.add(context['file_id'])
            reading_contexts.append(context)
            for passage in context['passages']:
                item=units.get(pid,passage['unit_id'],remote=True)
                passages.append({'id':passage['unit_id'],'unit_id':passage['unit_id'],'unit_hash':passage['unit_hash'],
                    'parse_id':context['parse_id'],'file_id':context['file_id'],'file_hash':context['file_hash'],
                    'locator':passage['locator'],'text':passage['text'],'char_start':passage['char_start'],'char_end':passage['char_end'],
                    'complete':passage['char_start']==0 and passage['char_end']==len(item['text']),
                    'reading_id':context['reading_id'],'semantic_context_status':context['semantic_context_status'],
                    'material_use':context['material_use'],'content_roles':item['content_roles'],'scope':'question_bound_reading_range',
                    'requested_scope_complete':True,'context_complete':True,'disputes':item['disputes'],'issues':item['issues']})
            if context['missing_topics']:gaps.append({'reading_id':context['reading_id'],'reason':'本次文献阅读仍待核对：'+'、'.join(context['required_topics'][t] for t in context['missing_topics'])})
        else:gaps.append({'reading_id':context['reading_id'],'reason':'已建立阅读任务但尚未实际读取原文，请先按问题阅读'})
    # Empty/stale readings for one document must not hide new retrieval hits.
    # A stale explicit reading still requires review for that same original.
    if selected or document_candidates:
        chosen=[]
        for uid in selected:
            item=units.get(pid,uid,remote=True)
            if item['file_id'] not in chosen and item['file_id'] not in covered_files|stale_files:chosen.append(item['file_id'])
        automatic=not chosen
        for item in document_candidates:
            if item['file_id'] not in chosen and item['file_id'] not in covered_files|stale_files:chosen.append(item['file_id'])
        for fid in chosen:
            original=db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1',(fid,pid))
            from .material_policy import source_context
            if not original or not security.file_allows_remote(fid,pid) or not source_context(original,'research')['allowed_for_intent']:continue
            directory=reading.outline(pid,fid,'research',ensure=False)
            if directory.get('needs_structure') or not directory.get('current'):
                # Read a legacy whole document, not its ranked fragments, until the structural index is ready.
                rows=db.rows("SELECT s.*,p.page_number FROM sources s LEFT JOIN pages p ON p.id=s.page_id WHERE s.file_id=? AND (p.review_state IS NULL OR p.review_state!='not_applicable') ORDER BY p.page_number,json_extract(s.locator,'$.char_start'),s.id",(fid,))
                size=sum(len(r['text']) for r in rows)
                byte_size=sum(len(r['text'].encode('utf-8')) for r in rows)
                if byte_remaining is not None and byte_size>byte_remaining:
                    gaps.append({'file_id':fid,'reason':'完整候选文献超过本次字节预算，继续查找其他完整文献；未裁剪片段'});continue
                if size>remaining:
                    gaps.append({'file_id':fid,'reason':'整篇原文超过本次读取预算，请先构建结构版本并按问题阅读相关章节'});continue
                remaining-=size
                if byte_remaining is not None:byte_remaining-=byte_size
                records.extend(rows)
                gaps.append({'file_id':fid,'reason':'已读取旧版文档来源文本；解析完整性与语义条件尚未核对'})
                continue
            rows=[units.get(pid,r['unit_id'],remote=True) for r in directory['units'] if r['review_state']!='excluded']
            existing=[p for p in passages if p['file_id']==fid]
            reclaimed=sum(len(p['text']) for p in existing)
            reclaimed_bytes=sum(len(p['text'].encode('utf-8')) for p in existing)
            total=sum(len(r['text']) for r in rows)
            byte_size=sum(len(r['text'].encode('utf-8')) for r in rows)
            if byte_remaining is not None and byte_size>byte_remaining+reclaimed_bytes:
                gaps.append({'file_id':fid,'reason':'完整候选文献超过本次字节预算，继续查找其他完整文献；未裁剪片段'});continue
            if total>remaining+reclaimed:
                gaps.append({'file_id':fid,'reason':f'整篇候选文献为{total}字，超过本次读取预算；未将检索片段冒充全文，请在文献阅读中选择章节并核对方法、条件、结果及局限'})
                continue
            remaining+=reclaimed-total
            if byte_remaining is not None:byte_remaining+=reclaimed_bytes-byte_size
            passages[:]=[p for p in passages if p['file_id']!=fid]
            for row in rows:
                passages.append({'id':row['id'],'unit_id':row['id'],'unit_hash':row['content_hash'],'parse_id':row['parse_id'],
                    'file_id':fid,'file_hash':row['file_hash'],'locator':row['locator'],'text':row['text'],'char_start':0,'char_end':len(row['text']),
                    'complete':True,'scope':'full_parsed_document_text','requested_scope_complete':True,'context_complete':True,
                    'semantic_context_status':'unassessed','material_use':row['material_use'],'content_roles':row['content_roles'],
                    'disputes':row['disputes'],'issues':row['issues']})
            gaps.append({'file_id':fid,'reason':'已提供该文献的全部解析文本，仍未确认方法、条件与局限是否支持当前论断'})
    # Keep exact source IDs only when their canonical text was actually read.
    from .reference_knowledge import decorate
    file_ids={p['file_id'] for p in passages}
    for fid in file_ids:
        bodies=[p['text'] for p in passages if p['file_id']==fid]
        full_document=all(p['scope']=='full_parsed_document_text' for p in passages if p['file_id']==fid)
        combined=' '.join(' '.join(text.split()) for text in bodies) if full_document else ''
        for source in db.rows('SELECT s.*,f.name,f.extension,p.page_number FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.file_id=? ORDER BY p.page_number,s.id',(fid,)):
            normalized=' '.join(source['text'].split())
            if source['text'].strip() and (any(source['text'].strip() in text for text in bodies) or combined and normalized in combined):records.append(source)
    unique={r['id']:r for r in records}
    sources=decorate(pid,list(unique.values()))[:limit]
    contexts=[]
    for p in passages:
        contexts.append({**p,'status':'read','passages':[{'id':p['unit_id'],'text':p['text'],'locator':p['locator'],'char_start':p['char_start'],'char_end':p['char_end']}],
                         'semantic_context_complete':None,'reading_scope_note':'已读取文本不等于该论断已获得支持；适用条件、方法和局限必须单独核对'})
    return {'sources':sources,'knowledge_units':contexts,'gaps':gaps,'selected_unit_ids':selected,
            'document_candidates':document_candidates,'document_readings':reading_contexts,'reading_policy':'document_first_no_fragment_fallback'}
