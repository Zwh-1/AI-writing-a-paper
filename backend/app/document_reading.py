"""Question-bound document reading. Text coverage is never semantic proof."""
import re
from fastapi import APIRouter, HTTPException
from . import db,security,knowledge_units as units,material_policy as policy

router=APIRouter(prefix='/projects/{pid}/document-reading')
TOPICS={
 'research':{'research_question':'研究问题','methods':'研究方法','conditions':'样本、环境与适用条件','results':'结果及比较对象','limitations':'局限、例外与不能推出的结论'},
 'guidance':{'purpose':'资料用途与教学主题','scope':'适用章节、等级与条件','examples':'正例、反例和示范语境','authority':'要求的来源与适用性'}
}


def task_hash(pid,sid):
    project=security.require_project(pid)
    section=security.require_section(sid) if sid else None
    if section and section['project_id']!=pid:raise HTTPException(404,'章节不属于本项目')
    return units.digest([project['brief'],{k:section[k] for k in ('id','title','goal','kind')} if section else None])


def catalog(pid,intent='research',query='',limit=100):
    security.require_project(pid)
    if intent not in TOPICS or not isinstance(query,str) or len(query)>5000 or type(limit) is not int or not 1<=limit<=200:
        raise HTTPException(422,'文献目录参数无效')
    rows=db.rows('SELECT f.*,h.parse_id FROM files f LEFT JOIN knowledge_heads h ON h.file_id=f.id WHERE f.project_id=? AND f.active=1 ORDER BY f.name,f.id',(pid,))
    books={}
    for book in db.rows('SELECT * FROM bibliography WHERE project_id=? ORDER BY created_at,id',(pid,)):
        books.setdefault(book['file_id'],[]).append({**book,'metadata':db.loads(book['metadata'],{})})
    result=[]
    for row in rows:
        material=policy.source_context(row,intent)
        if not material['allowed_for_intent']:continue
        references=books.get(row['id'],[])
        if query and query.casefold() not in (row['name']+' '+db.dumps([b['metadata'] for b in references])).casefold():continue
        result.append({'file_id':row['id'],'name':row['name'],'extension':row['extension'],'primary_role':row['role'],
                       'usages':policy.usages(row),'pages':row['pages'],'parse_id':row['parse_id'],'status':row['status'],
                       'references':[{'id':b['id'],'metadata':b['metadata'],'verification':b['verification']} for b in references],
                       'material_use':material,'search_scope':'document_metadata_not_factual_evidence'})
    return {'documents':result[:limit],'total':len(result),'intent':intent,'semantic_context_status':'unassessed'}


def outline(pid,fid,intent='research',ensure=False):
    original=units.file_record(pid,fid,allow_material=True)
    profile=policy.source_context(original,intent)
    if not profile['allowed_for_intent']:raise HTTPException(403,'该原件没有本次阅读所需用途')
    head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE h.file_id=?',(fid,))
    # Old PPT extraction could separate an example label from its example; rebuild that format.
    if ensure and (not head or not units.parse_current(head,original) or original['extension']=='.pptx' and head['engine']!=units.structure.ENGINE):
        units.build(pid,fid,allow_material=True)
        head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE h.file_id=?',(fid,))
    if not head:return {'file_id':fid,'name':original['name'],'needs_structure':True,'units':[],'material_use':profile}
    rows=db.rows('SELECT id,ordinal,kind,text,locator,origin,review_state,heading_path FROM knowledge_units WHERE parse_id=? ORDER BY ordinal',(head['id'],))
    headings=re.compile(r'^\s*(?:\d+(?:\.\d+){0,4}[.、]?\s+.{2,120}|第.{1,12}[章节].{0,100}|(?:abstract|introduction|methods?|methodology|results?|discussion|limitations?|conclusions?|references|摘要|绪论|引言|结论|研究方法|实验结果|参考文献)\b.{0,90})$',re.I)
    entries=[]
    for row in rows:
        locator=db.loads(row['locator'],{})
        candidates=[line.strip() for line in row['text'].splitlines() if len(line)<160 and headings.match(line)][:5]
        entries.append({'unit_id':row['id'],'ordinal':row['ordinal'],'kind':row['kind'],'locator':locator,'origin':row['origin'],
                        'characters':len(row['text']),'heading_path':db.loads(row['heading_path'],[]),'heading_candidates':candidates,
                        'label':locator.get('slide_title') or (candidates[0] if candidates else '原页文本' if locator.get('physical_page') else '文档内容'),
                        'review_state':row['review_state'],'interpretation':'navigation_only_not_semantic_fact'})
    return {'file_id':fid,'name':original['name'],'extension':original['extension'],'parse_id':head['id'],
            'current':units.parse_current(head,original),'material_use':profile,'units':entries,'unit_count':len(entries),
            'required_topics':TOPICS[intent],'semantic_context_status':'unassessed','scope_note':'目录和标题仅供导航；原文须按本次问题阅读，不能据标题猜测结论'}


@db.atomic
def create(pid,body):
    fid=body.get('file_id');question=body.get('question','');intent=body.get('intent','research');sid=body.get('section_id') or None
    if not isinstance(fid,str) or not isinstance(question,str) or not question.strip() or len(question)>6000 or intent not in TOPICS:
        raise HTTPException(422,'请选择原件、阅读用途并填写具体问题')
    scope=task_hash(pid,sid);directory=outline(pid,fid,intent,ensure=True)
    identity=db.uid();stamp=db.now()
    db.execute('INSERT INTO document_readings(id,project_id,file_id,parse_id,section_id,question,intent,required_topics,task_scope_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (identity,pid,fid,directory['parse_id'],sid,question.strip(),intent,db.dumps(TOPICS[intent]),scope,stamp,stamp))
    return context(pid,identity)


def record(pid,identity,remote=False):
    security.require_project(pid)
    row=db.one('SELECT * FROM document_readings WHERE id=? AND project_id=?',(identity,pid))
    if not row:raise HTTPException(404,'阅读记录不属于本项目')
    original=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(row['file_id'],pid))
    if not original:raise HTTPException(404,'原件记录不存在')
    head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE h.file_id=?',(row['file_id'],))
    profile=policy.source_context(original,row['intent'])
    current=bool(head and head['id']==row['parse_id'] and units.parse_current(head,original) and row['task_scope_hash']==task_hash(pid,row['section_id']) and profile['allowed_for_intent'])
    passages=db.rows('SELECT * FROM document_reading_passages WHERE reading_id=? ORDER BY created_at,id',(identity,))
    for passage in passages:
        item=units.get(pid,passage['unit_id'],remote=False,intent=row['intent'])
        if not item['current'] or item['review_state']=='excluded' or item['content_hash']!=passage['unit_hash'] or any(d['state']=='open' for d in item['disputes']) or passage['source_use']!=profile['source_purpose']:current=False
    if remote and (not current or not security.file_allows_remote(row['file_id'],pid)):raise HTTPException(403,'阅读依据、用途、任务或外发权限已变化')
    return row,original,profile,current,passages


def context(pid,identity,remote=False):
    row,original,profile,current,passages=record(pid,identity,remote)
    findings=db.rows('SELECT * FROM document_reading_findings WHERE reading_id=? ORDER BY created_at,rowid',(identity,))
    required=db.loads(row['required_topics'],{})
    latest={f['topic']:f for f in findings}
    covered={topic for topic,f in latest.items() if f['status']=='present' or f['status']=='not_applicable' and f['method']=='human'}
    missing=[topic for topic in required if topic not in covered]
    for p in passages:p['locator']=db.loads(p['locator'],{})
    for f in findings:f['passage_ids']=db.loads(f['passage_ids'],[])
    corpus=db.rows('SELECT id,length(text) AS characters,review_state,locator FROM knowledge_units WHERE parse_id=?',(row['parse_id'],))
    full_ids=set()
    for unit in corpus:
        through=0
        for a,b in sorted((p['char_start'],p['char_end']) for p in passages if p['unit_id']==unit['id']):
            if a>through:break
            through=max(through,b)
        if through>=unit['characters']:full_ids.add(unit['id'])
    expected={u['id'] for u in corpus if u['review_state']!='excluded'}
    assessed=not missing and current
    status='human_reviewed_for_question' if assessed and row['state']=='reviewed' else 'candidate_assessed' if assessed else 'unassessed'
    snapshot={'reading_id':identity,'parse_id':row['parse_id'],'question':row['question'],'intent':row['intent'],'task_scope_hash':row['task_scope_hash'],
              'passages':[{k:p[k] for k in ('id','unit_id','unit_hash','char_start','char_end','text','locator','source_use')} for p in passages],
              'findings':findings,'material_use':profile,'current':current,'semantic_context_status':status,
              'required_topics':required,'missing_topics':missing,'parsed_text_coverage_complete':bool(expected) and full_ids>=expected,
              'claim_support':'not_checked','semantic_context_complete':None,'review':{'state':row['state'],'reviewer':row['reviewer'],'note':row['review_note']},'source_instruction_authority':'project_material_not_system_instruction'}
    digest=units.digest(snapshot)
    return {**row,**snapshot,'version':digest,'file_id':original['id'],'file_hash':original['sha256'],'name':original['name'],'state':row['state'] if current else 'stale',
            'read_unit_count':len(full_ids),'document_unit_count':len(expected),'scope_note':'阅读覆盖与问题核对记录不自动证明任何新论断；教学示例与评分条件不构成本项目成果'}


def touch(pid,identity):
    db.execute("UPDATE document_readings SET state='reading',reviewer='',review_note='',updated_at=? WHERE id=?",(db.now(),identity))
    from .writing_agent.impact import changed
    changed(pid,'document_reading',identity,'原文阅读范围或核对记录发生变化，需复查相关论断')


@db.atomic
def read_range(pid,identity,body,remote=False):
    row,original,profile,current,existing=record(pid,identity,remote)
    if not current:raise HTTPException(409,'原文或阅读任务已变化，请重新建立阅读记录')
    start=units.integer(body,'ordinal_start',1,1,1000000);end=units.integer(body,'ordinal_end',start,start,1000000)
    budget=units.integer(body,'max_chars',24000,200,80000)
    rows=db.rows('SELECT id,ordinal,text,content_hash,locator,review_state,origin FROM knowledge_units WHERE parse_id=? AND ordinal BETWEEN ? AND ? ORDER BY ordinal',(row['parse_id'],start,end))
    if not rows:raise HTTPException(422,'该范围没有原文单元')
    if original['extension']=='.pptx':
        # OCR is an alternative transcription of a slide, not an independent teaching statement.
        for item in rows:
            if item['origin']=='pptx_slide':continue
            page=db.loads(item['locator'],{}).get('page')
            anchor=db.one("SELECT id,length(text) AS characters FROM knowledge_units WHERE parse_id=? AND origin='pptx_slide' AND json_extract(locator,'$.page')=?",(row['parse_id'],page))
            if anchor and anchor['id'] not in {r['id'] for r in rows} and not any(p['unit_id']==anchor['id'] and p['char_start']==0 and p['char_end']==anchor['characters'] for p in existing):
                return {'status':'needs_original_slide','passages':[],'required_unit_id':anchor['id'],
                        'action':'请先读取同一幻灯片的完整原生文本，以保留教学主题与正反例；OCR文字只作另一个转录候选'}
    selected=[]
    for item in rows:
        checked=units.get(pid,item['id'],remote=remote,intent=row['intent'])
        if not checked['current'] or checked['review_state']=='excluded':raise HTTPException(409,'所选范围含排除或过期内容，未静默跳过')
        a=0;b=len(item['text'])
        if body.get('char_start') is not None or body.get('char_end') is not None:
            if len(rows)!=1:raise HTTPException(422,'字符区间只适用于单个单元')
            a=units.integer(body,'char_start',0,0,len(item['text']));b=units.integer(body,'char_end',len(item['text']),a+1,len(item['text']))
        selected.append({'unit_id':item['id'],'unit_hash':item['content_hash'],'char_start':a,'char_end':b,'text':item['text'][a:b],
            'locator':{**checked['locator'],'page_id':checked['page_id']},'content_roles':checked['content_roles'],'ordinal':item['ordinal']})
    total=sum(len(p['text']) for p in selected)
    if total>budget:return {'status':'needs_narrower_range','requested_characters':total,'max_chars':budget,'passages':[],
                            'semantic_context_status':'unassessed','action':'请按目录缩小章节或明确字符范围后继续阅读；当前未返回截断正文'}
    if remote and len(db.dumps(selected).encode('utf-8'))>30000:
        return {'status':'needs_narrower_range','requested_characters':total,'max_utf8_bytes':30000,'passages':[],
                'semantic_context_status':'unassessed','action':'原文及定位信息超出单次工具返回预算，请缩小明确范围后继续；未登记为已读'}
    result=[];added=False
    for passage in selected:
        old=db.one('SELECT id FROM document_reading_passages WHERE reading_id=? AND unit_id=? AND char_start=? AND char_end=?',(identity,passage['unit_id'],passage['char_start'],passage['char_end']))
        pid_=old['id'] if old else db.uid()
        if not old:
            db.execute('INSERT INTO document_reading_passages VALUES(?,?,?,?,?,?,?,?,?,?)',(pid_,identity,passage['unit_id'],passage['unit_hash'],passage['char_start'],passage['char_end'],passage['text'],db.dumps(passage['locator']),profile['source_purpose'],db.now()));added=True
        result.append({**passage,'passage_id':pid_})
    if added:touch(pid,identity)
    return {'status':'read','reading_id':identity,'passages':result,'requested_scope_complete':True,'semantic_context_status':context(pid,identity)['semantic_context_status'],
            'material_use':profile,'scope_note':'这些是所请求的原文，不代表已读完整篇或语义充分'}


@db.atomic
def add_finding(pid,identity,body,method='candidate'):
    reading=context(pid,identity)
    if not reading['current']:raise HTTPException(409,'阅读版本已过期')
    topic=body.get('topic');status=body.get('status','present');quote=body.get('quote','');note=body.get('note','');ids=body.get('passage_ids',[])
    if topic not in reading['required_topics'] or status not in ('present','missing','contradicted','not_applicable') or not isinstance(note,str) or not note.strip() or len(note)>6000 or not isinstance(quote,str) or len(quote)>12000:
        raise HTTPException(422,'阅读核对主题或内容无效')
    if not isinstance(ids,list) or len(ids)>30 or any(not isinstance(i,str) for i in ids):raise HTTPException(422,'原文位置无效')
    passages={p['id']:p for p in reading['passages']}
    if any(i not in passages for i in ids):raise HTTPException(422,'只能引用本次已实际读取的原文')
    if status in ('present','contradicted') and (not ids or not quote.strip() or not any(quote in passages[i]['text'] for i in ids)):
        raise HTTPException(422,'该核对项需要逐字可定位的已读原文引句')
    reviewer=''
    if method=='human':_,reviewer=units.identity_note(body)
    if status=='not_applicable' and method!='human':raise HTTPException(422,'模型不能自行免除必读主题；请保留为待补范围')
    identity_=db.uid()
    db.execute('INSERT INTO document_reading_findings VALUES(?,?,?,?,?,?,?,?,?,?)',(identity_,identity,topic,status,quote,db.dumps(ids),note,method,reviewer,db.now()))
    touch(pid,identity)
    return context(pid,identity)


@db.atomic
def review(pid,identity,body):
    reading=context(pid,identity);note,reviewer=units.identity_note(body)
    if not reading['current'] or body.get('expected_version')!=reading['version']:raise HTTPException(409,'阅读记录已变化，请重新核对')
    if reading['missing_topics'] or body.get('compared_original') is not True:raise HTTPException(422,'请先核对必读主题及原件，缺失信息不能自动视为充分')
    db.execute("UPDATE document_readings SET state='reviewed',reviewer=?,review_note=?,updated_at=? WHERE id=?",(reviewer,note,db.now(),identity))
    from .writing_agent.impact import changed
    changed(pid,'document_reading',identity,'当前阅读问题的人工核对状态已变化，需重新核对关联论断')
    return context(pid,identity)


@router.get('/documents')
def documents_api(pid: str,intent: str='research',query: str=''):
    return catalog(pid,intent,query)


@router.get('/documents/{fid}/outline')
def outline_api(pid: str,fid: str,intent: str='research'):
    return outline(pid,fid,intent,ensure=False)


@router.put('/documents/{fid}/usages')
def usage_api(pid: str,fid: str,body: dict):
    return policy.set_usages(pid,fid,body)


@router.get('/materials')
def materials_api(pid: str):
    security.require_project(pid)
    return [{'file_id':f['id'],'name':f['name'],'primary_role':f['role'],'usages':policy.usages(f),'source_kind':policy.source_kind(f),'status':f['status'],'extension':f['extension']} for f in db.rows('SELECT * FROM files WHERE project_id=? AND active=1 ORDER BY name,id',(pid,))]


@router.post('/sessions')
def create_api(pid: str,body: dict):return create(pid,body)


@router.get('/sessions')
def sessions_api(pid: str,section_id: str=''):
    security.require_project(pid)
    rows=db.rows('SELECT id FROM document_readings WHERE project_id=?'+(' AND section_id=?' if section_id else '')+' ORDER BY updated_at DESC LIMIT 100',(pid,section_id) if section_id else (pid,))
    return [{k:v for k,v in context(pid,r['id']).items() if k not in ('passages','findings')} for r in rows]


@router.get('/sessions/{identity}')
def context_api(pid: str,identity: str):return context(pid,identity)


@router.post('/sessions/{identity}/read')
def read_api(pid: str,identity: str,body: dict):return read_range(pid,identity,body)


@router.post('/sessions/{identity}/findings')
def finding_api(pid: str,identity: str,body: dict):return add_finding(pid,identity,body,'human')


@router.post('/sessions/{identity}/review')
def review_api(pid: str,identity: str,body: dict):return review(pid,identity,body)
