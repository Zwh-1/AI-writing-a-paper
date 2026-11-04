"""Task-oriented retrieval; derived knowledge selects original reading scopes."""
from fastapi import HTTPException
from collections import deque
import time
from . import db, security, knowledge_lineage as lineage, knowledge_graph as graph, knowledge_wiki as wiki

def route(question):
    from .knowledge_planning import intent
    return intent(question)

def observations_for(pid,refs):
    queue=deque(refs);seen=set();result=[]
    while queue:
        r=queue.popleft();key=(r['type'],r['id'])
        if key in seen:continue
        seen.add(key)
        if r['type']=='observation':result.append(lineage.owned(pid,'observation',r['id']))
        queue.extend(lineage.references(pid,r['type'],r['id']))
    return result

def balanced_plan(items,limit=16):
    """Apply the reading limit only after every document can compete for a slot."""
    grouped={};seen=set()
    for item in items:
        key=(item.get('unit_id'),item.get('evidence_id'),item.get('intent'),item.get('char_start'),item.get('char_end'))
        if key in seen:continue
        seen.add(key)
        document=item.get('file_id') or ('evidence',item.get('evidence_id'))
        grouped.setdefault(document,deque()).append(item)
    selected=[]
    while len(selected)<limit:
        available=False
        for group in grouped.values():
            if group and len(selected)<limit:selected.append(group.popleft());available=True
        if not available:break
    return selected

def balanced_claims(pid,items,limit=20):
    if not items:return []
    observations={r['id']:r['file_id'] or ('evidence',r['evidence_id']) for r in db.rows('SELECT id,file_id,evidence_id FROM knowledge_observations WHERE project_id=?',(pid,))}
    groups={}
    for item in items:
        files=tuple(sorted({str(observations[r['id']]) for r in item['sources'] if r['type']=='observation' and r['id'] in observations}))
        groups.setdefault(files or ('unscoped',),deque()).append(item)
    result=[]
    while len(result)<limit:
        ready=False
        for group in groups.values():
            if group and len(result)<limit:result.append(group.popleft());ready=True
        if not ready:break
    return result

def baseline_scopes(pid,question,ns,remote=False):
    """Give every ablation the same existing raw guidance/project evidence."""
    from . import material_policy as policy,parsers
    plans=[]
    with lineage.validation_scope():
        if ns in ('guidance','all'):
            words=list(dict.fromkeys(parsers.tokens(question).split()))[:35]
            match=' OR '.join('"'+w.replace('"','')+'"' for w in words)
            rows=db.rows("SELECT u.*,p.file_id,f.role,f.extension FROM knowledge_unit_fts ft JOIN knowledge_units u ON u.id=ft.unit_id JOIN knowledge_heads h ON h.parse_id=u.parse_id JOIN knowledge_parses p ON p.id=u.parse_id JOIN files f ON f.id=p.file_id WHERE knowledge_unit_fts MATCH ? AND p.project_id=? AND f.active=1 AND u.review_state!='excluded' ORDER BY bm25(knowledge_unit_fts) LIMIT 200",(match,pid)) if match else []
            for row in rows:
                if not policy.source_context({'id':row['file_id'],'role':row['role'],'extension':row['extension']},'guidance')['allowed_for_intent']:continue
                try:lineage.version_of(pid,'unit',row['id'],remote)
                except HTTPException:continue
                if len(row['text'])<=12000:a,b=0,len(row['text'])
                else:
                    from .knowledge_compiler import ranges
                    a,b=max(ranges(row['text']),key=lambda span:graph.relevance(row['text'][span[0]:span[1]],question))
                if b>a:plans.append({'file_id':row['file_id'],'unit_id':row['id'],'intent':'guidance','char_start':a,'char_end':b,'reason':'既有全文检索定位的指导资料，仍需读取并核对语境','semantic_support':'not_checked'})
                if len(plans)>=8:break
        if ns in ('project','all'):
            from .writing_agent import evidence
            records=[]
            for e in db.rows("SELECT id FROM evidence_items WHERE project_id=? AND validity='current' AND kind IN ('code_observation','test_definition','test_run','deployment','data_result','author_statement')",(pid,)):
                try:
                    lineage.version_of(pid,'evidence',e['id'],remote);item=evidence.get(pid,e['id'],remote)
                except HTTPException:continue
                text=item['payload'].get('text') or db.dumps(item['payload']);records.append((graph.relevance(text,question),item,len(text)))
            for _,item,size in sorted(records,key=lambda r:-r[0])[:8]:plans.append({'file_id':None,'evidence_id':item['id'],'intent':'project','char_start':0,'char_end':size,'reason':'既有项目证据记录，保留实际证据等级','semantic_support':'not_checked'})
    return plans

def query(pid,body,remote=False):
    started=time.perf_counter()
    with lineage.validation_scope():result=_query(pid,body,remote)
    if remote:
        # Local embedding/reranking may take long enough for source permissions
        # or versions to change. Recheck all returned content after inference.
        with lineage.validation_scope(fresh=True):
            lineage.validate_refs(pid,result['knowledge_refs'],True)
            for item in result['graph']['assertions']:
                if lineage.version_of(pid,'assertion',item['claim_version_id'],True)!=item['version_hash']:
                    lineage.fail('KNOWLEDGE_STALE','检索期间图谱依据变化，请重新检索')
            for item in result['graph']['nodes']:
                if lineage.version_of(pid,'entity_version',item['current_version_id'],True)!=item['version']['content_hash']:
                    lineage.fail('KNOWLEDGE_STALE','检索期间实体依据变化，请重新检索')
            for item in result['reading_plan']:
                if item.get('unit_id'):lineage.version_of(pid,'unit',item['unit_id'],True)
                elif item.get('evidence_id'):lineage.version_of(pid,'evidence',item['evidence_id'],True)
            for item in result['originals']:
                if not security.file_allows_remote(item['file_id'],pid):lineage.fail('SOURCE_ACCESS_REVOKED','检索期间原文外发权限变化，未返回派生内容',403)
                if item.get('knowledge_unit_id') and item.get('unit_hash') and lineage.version_of(pid,'unit',item['knowledge_unit_id'],True)!=item['unit_hash']:
                    lineage.fail('SOURCE_STALE','检索期间原文版本变化，请重新检索')
    result['timings_ms']['total']=round((time.perf_counter()-started)*1000,3)
    return result


def _query(pid,body,remote=False):
    started=time.perf_counter();timings={}
    security.require_project(pid);question=body.get('query','');ns=body.get('namespace','literature');variant=body.get('variant','D')
    source_scope=body.get('file_ids',[])
    lineage.descendants_of_files(pid,source_scope)
    if not isinstance(question,str) or not question.strip() or len(question)>5000 or variant not in ('A','B','C','D'):lineage.fail('INVALID_QUERY','请填写1至5000字的问题并选择有效检索组合',422)
    if ns!='all':graph.namespace(ns)
    pages=wiki.search(pid,question,ns,4,remote,**({'file_ids':source_scope} if source_scope else {}))['items'] if variant in ('C','D') else []
    timings['wiki']=round((time.perf_counter()-started)*1000,3);stage=time.perf_counter()
    found=graph.search(pid,question,ns,body.get('depth',1),remote=remote,**({'file_ids':source_scope} if source_scope else {})) if variant in ('B','D') else {'nodes':[],'assertions':[],'edges':[]}
    timings['graph']=round((time.perf_counter()-stage)*1000,3)
    refs=[];context_pages=[];gaps=[];remaining=16000
    for page in pages:
        selected=[]
        for block in sorted(page['blocks'],key=lambda b:-graph.relevance(b['text'],question)):
            if len(block['text'])>remaining:
                gaps.append({'page_id':page['id'],'block_key':block['block_key'],'reason':'完整Wiki块超过剩余上下文预算，未截断内容'});continue
            selected.append(block);remaining-=len(block['text'])
            if len(selected)>=6:break
        if selected:
            context_pages.append({'id':page['id'],'title':page['title'],'version_id':page['version']['id'],'blocks':selected,'kind':page['kind']})
            refs.extend(lineage.ref(pid,'wiki_block',block['id'],remote) for block in selected)
    for a in balanced_claims(pid,found['assertions']):refs.append(lineage.ref(pid,'assertion',a['claim_version_id'],remote))
    refs=list({(r['type'],r['id'],r['version']):r for r in refs}.values())
    observations=observations_for(pid,refs);plan=[];seen=set()
    for obs in sorted(observations,key=lambda o:-graph.relevance(o['text'],question)):
        if not obs['unit_id']:
            if obs['evidence_id']:plan.append({'file_id':None,'evidence_id':obs['evidence_id'],'observation_id':obs['id'],'intent':'project','char_start':0,'char_end':len(obs['text']),'reason':'项目原始记录，证据类型和执行标记另行核对','semantic_support':'not_checked'})
            continue
        meta=db.loads(obs['metadata_json'],{});key=(obs['unit_id'],meta.get('char_start'),meta.get('char_end'))
        if key in seen:continue
        seen.add(key);plan.append({'file_id':obs['file_id'],'unit_id':obs['unit_id'],'observation_id':obs['id'],'intent':obs['intent'],'char_start':meta['char_start'],'char_end':meta['char_end'],'reason':'知识论断的实际原文范围','semantic_support':'not_checked'})
    originals=[]
    stage=time.perf_counter()
    if body.get('include_originals',True) and ns in ('literature','all'):
        from .knowledge_service import search
        originals=search(pid,question,limit=8,remote=remote,**({'file_ids':source_scope} if source_scope else {}))['items']
    timings['originals']=round((time.perf_counter()-stage)*1000,3)
    for hit in originals:
        uid=hit.get('knowledge_unit_id')
        if uid and not any(p.get('unit_id')==uid for p in plan):
            row=db.one('SELECT text FROM knowledge_units WHERE id=?',(uid,))
            if row and len(row['text'])<=12000:plan.append({'file_id':hit['file_id'],'unit_id':uid,'intent':'research','char_start':0,'char_end':len(row['text']),'reason':'原文检索候选；尚未读取','semantic_support':'not_checked'})
    existing={(p.get('unit_id'),p.get('evidence_id'),p.get('intent')) for p in plan}
    plan.extend(p for p in baseline_scopes(pid,question,ns,remote) if (p.get('unit_id'),p.get('evidence_id'),p.get('intent')) not in existing)
    if source_scope:plan=[p for p in plan if p.get('file_id') in source_scope]
    balanced=balanced_plan(plan)
    timings['total']=round((time.perf_counter()-started)*1000,3)
    from .knowledge_planning import plan
    argument_plan=plan(pid,question,found['assertions'],balanced,refs)
    return {'query':question,'namespace':ns,'variant':variant,'route':route(question),'wiki':context_pages,'graph':found,'argument_plan':argument_plan,
            'originals':originals,'reading_plan':balanced,'knowledge_refs':refs,'file_ids':sorted(set(lineage.source_files(pid,refs))|{p['file_id'] for p in balanced if p.get('file_id')}),
            'timings_ms':timings,
            'gaps':gaps,'scope_note':'图谱与Wiki用于组织论点；阅读计划中的原文尚需实际读取，检索及综合不自动证明支持性'}

def prepare_readings(pid,question,sid,plan,max_chars=30000):
    from . import document_reading as reader
    security.require_project(pid);section=security.require_section(sid)
    if section['project_id']!=pid:lineage.fail('INVALID_SECTION','章节不属于当前项目',404)
    remaining=max_chars;sessions={};gaps=[];completed=[]
    for item in plan:
        size=item['char_end']-item['char_start']
        if size>remaining:
            gaps.append({'file_id':item['file_id'],'reason':'原文范围超过剩余阅读预算，未截断；可分步读取'});continue
        if item.get('evidence_id'):
            try:lineage.version_of(pid,'evidence',item['evidence_id'],True);completed.append({'evidence_id':item['evidence_id'],'scope':'project_record_not_automatic_execution_proof'})
            except HTTPException as error:gaps.append({'evidence_id':item['evidence_id'],'reason':str(error.detail)})
            continue
        intent=item.get('intent','research');key=(item['file_id'],intent)
        ordinal=db.one('SELECT ordinal,parse_id FROM knowledge_units WHERE id=?',(item['unit_id'],))
        if not ordinal:continue
        try:
            if key not in sessions:
                old=db.one("SELECT id FROM document_readings WHERE project_id=? AND section_id=? AND file_id=? AND question=? AND intent=? ORDER BY created_at DESC LIMIT 1",(pid,sid,item['file_id'],question,intent))
                if old:
                    try:context=reader.context(pid,old['id'],remote=True)
                    except HTTPException:context=None
                else:context=None
                if not context:context=reader.create(pid,{'file_id':item['file_id'],'question':question,'section_id':sid,'intent':intent})
                sessions[key]=context['id']
            result=reader.read_range(pid,sessions[key],{'ordinal_start':ordinal['ordinal'],'ordinal_end':ordinal['ordinal'],'char_start':item['char_start'],'char_end':item['char_end'],'max_chars':min(80000,max(200,remaining))},remote=True)
            if result['status']=='needs_original_slide':
                anchor=db.one('SELECT id,ordinal,text FROM knowledge_units WHERE id=?',(result['required_unit_id'],))
                from .knowledge_compiler import ranges
                for start,end in ranges(anchor['text'],3000):
                    if end-start>remaining:break
                    native=reader.read_range(pid,sessions[key],{'ordinal_start':anchor['ordinal'],'ordinal_end':anchor['ordinal'],'char_start':start,'char_end':end,'max_chars':min(80000,max(200,remaining))},remote=True)
                    if native['status']!='read':break
                    remaining-=end-start;completed.extend(native['passages'])
                if size<=remaining:result=reader.read_range(pid,sessions[key],{'ordinal_start':ordinal['ordinal'],'ordinal_end':ordinal['ordinal'],'char_start':item['char_start'],'char_end':item['char_end'],'max_chars':min(80000,max(200,remaining))},remote=True)
            if result['status']=='read':remaining-=size;completed.extend(result['passages'])
            else:gaps.append({'file_id':item['file_id'],'reason':result.get('action','需继续读取必要上下文')})
        except HTTPException as error:gaps.append({'file_id':item['file_id'],'reason':str(error.detail)})
    return {'reading_ids':list(sessions.values()),'passages':completed,'gaps':gaps,'semantic_support':'not_checked'}

@db.read_session()
def prepare_writing(pid,question,sid,limit=8,budget_bytes=None):
    # Reading updates change reading receipts, not the underlying source input.
    # Hash each file once for this preparation while unit and permission checks
    # still resolve current records individually.
    from .knowledge_units import cache_inputs
    with cache_inputs():
        return _prepare_writing(pid,question,sid,limit,budget_bytes)


def _prepare_writing(pid,question,sid,limit=8,budget_bytes=None):
    from .document_preparation import prepare
    if not db.setting('graph_wiki_enabled:'+pid,False):return prepare(pid,question,sid,limit,max_bytes=budget_bytes)
    knowledge=query(pid,{'query':question,'namespace':'literature','include_originals':True},remote=True)
    automatic=db.setting('automatic_writing_readings:'+pid+':'+sid,{})
    from .document_reading import context as reading_context
    previous={}
    for row in db.rows("SELECT id FROM document_readings WHERE project_id=? AND section_id=? AND intent='research'",(pid,sid)):
        try:
            current=reading_context(pid,row['id'],remote=True)
            if current.get('current'):previous[row['id']]=current['version']
        except HTTPException:continue
    reads=prepare_readings(pid,question,sid,knowledge['reading_plan'],max_chars=min(30000,max(200,budget_bytes//4)) if budget_bytes is not None else 30000)
    for rid in reads['reading_ids']:
        current=reading_context(pid,rid,remote=True)
        if rid not in previous or automatic.get(rid)==previous[rid]:automatic[rid]=current['version']
    db.set_setting('automatic_writing_readings:'+pid+':'+sid,automatic)
    result=prepare(pid,question,sid,limit,findings={'items':knowledge['originals']},max_bytes=budget_bytes)
    # Only pass assertions covered by the manifest references to a writing
    # model. The larger graph remains available for local exploration.
    allowed={r['id'] for r in knowledge['knowledge_refs'] if r['type']=='assertion'}
    assertions=[a for a in knowledge['graph']['assertions'] if a['claim_version_id'] in allowed]
    endpoints={identity for a in assertions for identity in (a['subject_id'],a['object_id']) if identity}
    model_graph={**knowledge['graph'],'assertions':assertions,'edges':[e for e in knowledge['graph']['edges'] if e['claim_id'] in allowed],
                 'nodes':[n for n in knowledge['graph']['nodes'] if n['id'] in endpoints]}
    result['knowledge_context']={**{k:knowledge[k] for k in ('route','wiki','scope_note')},'graph':model_graph}
    result['argument_plan']=knowledge.get('argument_plan',{})
    result['knowledge_refs']=knowledge['knowledge_refs'];result['gaps'].extend(reads['gaps']+knowledge['gaps'])
    result['automatic_reading_ids']=[r['reading_id'] for r in result.get('document_readings',[]) if automatic.get(r['reading_id'])==r['version']]
    return result

def answer(task,body,context=None):
    from .knowledge_model_bridge import json_call
    from .knowledge_compiler import observe_unit,observe_evidence
    from .config import DATA
    pid=task['project_id'];context=context or query(pid,body,True);observed=[];remaining=18000
    for item in context['reading_plan']:
        size=item['char_end']-item['char_start']
        if size>remaining:continue
        observation=observe_evidence(pid,item['evidence_id']) if item.get('evidence_id') else observe_unit(pid,item['unit_id'],item['char_start'],item['char_end'],True,intent=item.get('intent','research'));observed.append(observation);remaining-=size
    if not observed:lineage.fail('NO_READABLE_EVIDENCE','没有读到足以准备回答的原文，请先构建或调整资料范围',422)
    refs=[lineage.ref(pid,'observation',o['id'],True) for o in observed]
    data,run=json_call(task,'knowledge_answer',{'question':body['query'],'originals':observed,'derived_context':context['wiki'],'official_navigation':context.get('official_navigation'),
        'allowed_sources':refs,'output_schema':{'blocks':[{'kind':'synthesis/inference/limitation/question','text':'回答内容','sources':refs}],'gaps':['缺少的依据']},
        'requirements':['事实逐块绑定实际提供的原文','保留条件、否定和结论强度','覆盖不足明确列出，不猜测结果']},refs+context['knowledge_refs'])
    allowed={(r['type'],r['id'],r['version']) for r in refs};blocks=data.get('blocks',[])
    if not isinstance(blocks,list) or not 1<=len(blocks)<=250:lineage.fail('KNOWLEDGE_OUTPUT_INVALID','回答需要1至250个完整内容块',422)
    for b in blocks:
        if not isinstance(b,dict) or b.get('kind') not in wiki.BLOCKS or not isinstance(b.get('text'),str) or not b['text'].strip() or len(b['text'])>30000:
            lineage.fail('KNOWLEDGE_OUTPUT_INVALID','回答内容块缺少有效文字或类型，原模型响应已留存，未发布为知识',422)
        if not isinstance(b.get('sources'),list) or not b['sources'] or any(not isinstance(r,dict) or (r.get('type'),r.get('id'),r.get('version')) not in allowed for r in b['sources']):lineage.fail('INVALID_PROVENANCE','回答引用未实际读取的来源',422)
    identity=db.uid();folder=DATA/'knowledge'/'answers';folder.mkdir(parents=True,exist_ok=True);path=folder/(identity+'.json');path.write_text(db.dumps(data),encoding='utf-8');content=lineage.digest(data)
    manifest={'question':body['query'],'run_id':run,'refs':refs,'gaps':data.get('gaps',[]),'semantic_support':'model_candidate_not_human_verified'}
    db.execute('INSERT INTO knowledge_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,task['id'],'knowledge_answer',content,str(path),db.dumps(manifest),'ready',db.now()));lineage.add_dependencies(pid,'artifact',identity,content,refs)
    return {'id':identity,'answer':data,'manifest':manifest,'run_id':run,'human_approval_added':False}
