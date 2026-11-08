"""Bounded whole-manuscript review with explicit paragraph coverage."""
from . import db,documents,security,provider
from .source_payload import token_upper_bound
from fastapi import HTTPException
import re


SYSTEM=('按已确认规则检查给定正文片段的逻辑、术语、数字、计划与已完成工作、摘要与结论、研究问题和章节衔接。'
        '其他章节目录只是导航；不声称阅读未提供原文。只报告有逐字引句、可定位的疑点，不证明事实真实。'
        '返回JSON {"coverage_complete":true,"findings":[{"section_id":"给定ID","quote":"逐字原句",'
        '"message":"疑点","evidence":"原文和理由","severity":"warning|info"}]}。不能覆盖时coverage_complete=false。')


def prepare(pid,context_limit,reserved_output=8192):
    p=security.require_project(pid)
    if not p['rule_package_id']:raise HTTPException(422,'请先发布规则包')
    package=db.one('SELECT * FROM rule_packages WHERE id=? AND project_id=?',(p['rule_package_id'],pid))
    rules=db.loads(package['rules'],[]);catalogue=[];sections=[];rule_files=set()
    for rule in rules:
        if not rule.get('source_id'):continue
        origin=db.one('SELECT file_id FROM sources WHERE id=?',(rule['source_id'],))
        if not origin or not security.file_allows_remote(origin['file_id'],pid):raise HTTPException(403,'审阅规则原件不允许外发')
        rule_files.add(origin['file_id'])
    from .writing_agent.provenance import revision_permissions
    for sec in db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position,id",(pid,)):
        rev=documents.current(sec)
        if not rev:continue
        revision_permissions(pid,rev['id'])
        paragraphs=[{'paragraph_index':i,'node_id':node.get('attrs',{}).get('nodeId'),'text':documents.plain(node)}
                    for i,node in enumerate(db.loads(rev['document'],{}).get('content',[])) if documents.plain(node).strip()]
        sections.append((sec,rev,paragraphs))
        catalogue.append({'section_id':sec['id'],'title':sec['title'],'goal':sec['goal'],'revision_id':rev['id'],'paragraphs':len(paragraphs)})
    if not sections:raise HTTPException(422,'尚无正文')
    chunks=[]
    for sec,rev,paragraphs in sections:
        selected=[]
        base={'rules':rules,'section':{'id':sec['id'],'title':sec['title'],'revision_id':rev['id']},'catalogue':catalogue,
              'scope':'exact_paragraph_batch','unread_catalogue_is_evidence':False}
        for paragraph in paragraphs:
            trial={**base,'paragraphs':[*selected,paragraph]}
            if token_upper_bound(SYSTEM,db.dumps(trial))+reserved_output>context_limit:
                if selected:chunks.append({**base,'paragraphs':selected});selected=[]
                if token_upper_bound(SYSTEM,db.dumps({**base,'paragraphs':[paragraph]}))+reserved_output>context_limit:
                    raise HTTPException(422,{'code':'REVIEW_PARAGRAPH_TOO_LARGE','message':'单个完整段落或必要规则超出审阅容量，请调整容量或先拆分段落','section_id':sec['id'],'paragraph_index':paragraph['paragraph_index']})
            selected.append(paragraph)
        if selected:chunks.append({**base,'paragraphs':selected})
    manifest={'rule_package_id':package['id'],'required_rule_ids':[r['id'] for r in rules],
              'loaded_rule_ids':[r['id'] for r in rules],'related_revisions':{sec['id']:rev['id'] for sec,rev,_ in sections},'remote':True,
              'review_package_version':package['id'],'file_ids':sorted(rule_files),
              'manuscript_review_revisions':{sec['id']:rev['id'] for sec,rev,_ in sections}}
    return chunks,manifest


def cross_section_windows(chunks,limit,reserve):
    """Pair actual paragraphs, retaining exactly which paragraphs were omitted."""
    sections={}
    for chunk in chunks:
        sid=chunk['section']['id']
        entry=sections.setdefault(sid,{'section':chunk['section'],'paragraphs':[]})
        entry['paragraphs'].extend(chunk['paragraphs'])
    ids=list(sections);edges={(a,b) for a,b in zip(ids,ids[1:])}
    from .manuscript_anchors import labels
    mentions={}
    for sid,entry in sections.items():
        for paragraph in entry['paragraphs']:
            for mention in labels(paragraph['text']):mentions.setdefault(mention['label'],set()).add(sid)
    for members in mentions.values():
        ordered=sorted(members)
        edges.update((a,b) for i,a in enumerate(ordered) for b in ordered[i+1:])
    for sid,entry in sections.items():
        sec=security.require_section(sid)
        edges.update((dependency,sid) for dependency in db.loads(sec['dependencies'],[]) if dependency in sections and dependency!=sid)
        if sec['kind'] in ('abstract','abstract_en','conclusion'):
            edges.update((other,sid) for other in ids if other!=sid)
    windows=[]
    for first,second in sorted(edges):
        base={'rules':chunks[0]['rules'],'operation':'cross_section_review','sections':[],
              'scope':'exact_selected_paragraphs','policy':'仅核对实际提供的跨章原文，目录和未提供段落不构成依据；优先核对数值、术语、计划完成状态和结论范围。'}
        candidates=[]
        for sid in (first,second):
            row=sections[sid];base['sections'].append({'section':row['section'],'paragraphs':[]})
            for position,p in enumerate(row['paragraphs']):
                score=5*bool(re.search(r'\d|结果|结论|局限|范围|定义|计划|尚未|未执行',p['text']))+(position==0 or position==len(row['paragraphs'])-1)
                candidates.append((score,sid,p))
        candidates.sort(key=lambda row:(-row[0],row[1],row[2]['paragraph_index']))
        loaded={first:[],second:[]}
        # Seed both sides before ranking remaining paragraphs.
        candidates=([next(row for row in candidates if row[1]==sid) for sid in (first,second) if any(row[1]==sid for row in candidates)]+
                    candidates)
        seen=set()
        for _,sid,paragraph in candidates:
            key=(sid,paragraph['paragraph_index'])
            if key in seen:continue
            seen.add(key);at=0 if sid==first else 1
            base['sections'][at]['paragraphs'].append(paragraph)
            if token_upper_bound(SYSTEM,db.dumps(base))+reserve+2048>limit:base['sections'][at]['paragraphs'].pop()
            else:loaded[sid].append(paragraph['paragraph_index'])
        for row in base['sections']:row['paragraphs'].sort(key=lambda p:p['paragraph_index'])
        base['coverage']=[{'section_id':sid,'provided_paragraph_indices':sorted(loaded[sid]),
            'omitted_paragraph_indices':[p['paragraph_index'] for p in sections[sid]['paragraphs'] if p['paragraph_index'] not in loaded[sid]]} for sid in (first,second)]
        base['can_review']=all(row['paragraphs'] for row in base['sections'])
        windows.append(base)
    return windows


def run(task):
    from .writing_flow import _cached_call
    from .tasks import _validate_model_source_inputs,_validate_model_presend
    cfg=provider.config();chunks,manifest=prepare(task['project_id'],int(cfg.get('context_limit',100000)),int(cfg.get('max_output_tokens',8192)))
    coverage=[];added=0
    for index,chunk in enumerate(chunks):
        meta={**manifest,'section_id':chunk['section']['id'],'paper_chunk_index':index}
        reply=_cached_call(task,SYSTEM,chunk,meta,stage='whole_paper_review_chunk',index=index)
        parsed=provider.parse_json(reply['text'])
        if not isinstance(parsed,dict) or not isinstance(parsed.get('findings'),list):raise ValueError('全文审阅返回结构无效，原响应保留')
        _validate_model_source_inputs(task['project_id'],manifest)
        # Exact revision and permission checks are repeated after each call.
        for sid,rid in manifest['related_revisions'].items():
            sec=security.require_section(sid)
            if sec['project_id']!=task['project_id'] or sec['current_revision_id']!=rid:raise HTTPException(409,'全文审阅期间正文已变化')
            from .writing_agent.provenance import revision_permissions
            revision_permissions(task['project_id'],rid)
        for item in parsed['findings']:
            if not isinstance(item,dict) or item.get('section_id')!=chunk['section']['id']:continue
            quote=item.get('quote','')
            if not isinstance(quote,str) or not quote or not any(quote in p['text'] for p in chunk['paragraphs']):continue
            if not isinstance(item.get('message'),str) or not item['message'].strip():continue
            key=[task['id'],index,item['message'],quote]
            from .ai_request_cache import digest
            fid=digest(key)
            db.execute('INSERT OR IGNORE INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (fid,task['project_id'],chunk['section']['id'],chunk['section']['revision_id'],None,'warning','unknown','model_assisted',item['message'][:2000],quote[:4000],'open',db.now()))
            added+=1
        coverage.append({'section_id':chunk['section']['id'],'revision_id':chunk['section']['revision_id'],
                         'paragraph_indices':[p['paragraph_index'] for p in chunk['paragraphs']],
                         'model_reported_complete':parsed.get('coverage_complete') is True,'run_id':reply.get('run_id')})
    cross_coverage=[]
    for index,window in enumerate(cross_section_windows(chunks,int(cfg.get('context_limit',100000)),int(cfg.get('max_output_tokens',8192)))):
        status={'coverage':window['coverage'],'model_reported_complete':False,'state':'not_checked'}
        if window['can_review']:
            reply=_cached_call(task,SYSTEM,window,{**manifest,'paper_chunk_index':index},stage='whole_paper_cross_section',index=index)
            parsed=provider.parse_json(reply['text'])
            if not isinstance(parsed,dict) or not isinstance(parsed.get('findings'),list):raise ValueError('跨章审阅返回结构无效')
            for row in window['sections']:
                sec=security.require_section(row['section']['id'])
                if sec['current_revision_id']!=row['section']['revision_id']:raise HTTPException(409,'跨章审阅期间正文已变化')
                from .writing_agent.provenance import revision_permissions
                revision_permissions(task['project_id'],sec['current_revision_id'])
            for item in parsed['findings']:
                if not isinstance(item,dict):continue
                section=next((row for row in window['sections'] if row['section']['id']==item.get('section_id')),None)
                quote=item.get('quote','');message=item.get('message','')
                if not section or not isinstance(quote,str) or not quote or not any(quote in p['text'] for p in section['paragraphs']) or not isinstance(message,str) or not message.strip():continue
                from .ai_request_cache import digest
                fid=digest([task['id'],'cross',index,message,quote])
                db.execute('INSERT OR IGNORE INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (fid,task['project_id'],section['section']['id'],section['section']['revision_id'],None,'warning','unknown','model_assisted',message[:2000],quote[:4000],'open',db.now()))
                added+=1
            status.update(model_reported_complete=parsed.get('coverage_complete') is True,state='reviewed',run_id=reply.get('run_id'))
        cross_coverage.append(status)
    _validate_model_source_inputs(task['project_id'],manifest)
    return {'added_findings':added,'requires_human_review':True,'coverage':coverage,
            'paragraph_coverage_complete':all(c['model_reported_complete'] for c in coverage),
            'cross_section_semantic_coverage':cross_coverage,
            'cross_section_scope':'相邻章、明确依赖、同名数值指标及摘要/结论与正文的配对窗口；省略段落逐项记录，不代表所有可能跨章关系均通过',
            'note':'各段落分批覆盖，跨章关系另按实际原文窗口核对；仍需人工通读'}
