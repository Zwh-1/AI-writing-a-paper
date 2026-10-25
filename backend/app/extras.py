import io,re,hashlib,base64
from fastapi import APIRouter,HTTPException,UploadFile,File
from . import db,security,tasks,documents,parsers,provider
router=APIRouter()

def parse_bibliography(text,extension):
    records=[]
    if extension=='.ris':
        for record in re.split(r'(?m)^ER\s*-',text):
            fields={}
            for line in record.splitlines():
                m=re.match(r'^([A-Z0-9]{2})\s*-\s*(.*)',line)
                if m: fields.setdefault(m[1],[]).append(m[2].strip())
            title=(fields.get('TI') or fields.get('T1') or [''])[0]
            if title: records.append({'title':title,'authors':fields.get('AU',fields.get('A1',[])),'year':re.sub(r'[^0-9]','',(fields.get('PY') or fields.get('Y1') or [''])[0])[:4],'journal':(fields.get('JO') or fields.get('T2') or [''])[0],'doi':(fields.get('DO') or [''])[0],'scope':'metadata_only'})
    else:
        for match in re.finditer(r'@\w+\s*\{[^,]+,',text):
            pos=match.end();depth=1;start=pos
            while pos<len(text) and depth:
                if text[pos]=='{' and (pos==0 or text[pos-1]!='\\'):depth+=1
                elif text[pos]=='}' and (pos==0 or text[pos-1]!='\\'):depth-=1
                pos+=1
            body=text[start:pos-1];fields={}
            for f in re.finditer(r'(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|"[^"\\]*(?:\\.[^"\\]*)*"|[^,\n]+)',body):
                val=f.group(2).strip();fields[f.group(1).lower()]=val[1:-1] if val.startswith(('{','"')) else val
            if fields.get('title'):records.append({'title':fields['title'],'authors':fields.get('author','').split(' and '),'year':fields.get('year',''),'journal':fields.get('journal',fields.get('booktitle','')),'doi':fields.get('doi',''),'volume':fields.get('volume',''),'pages':fields.get('pages',''),'scope':'metadata_only'})
    return records

@router.post('/projects/{pid}/references/import')
async def import_bibliography(pid:str,file:UploadFile=File(...)):
    security.require_project(pid);ext='.'+(file.filename or '').rsplit('.',1)[-1].lower()
    if ext not in ('.bib','.ris'):raise HTTPException(422,'请上传BibTeX或RIS')
    raw=await file.read(10_000_001)
    if len(raw)>10_000_000:raise HTTPException(422,'书目文件超过10MB')
    try:text=raw.decode('utf-8-sig')
    except UnicodeError:text=raw.decode('gb18030')
    records=parse_bibliography(text,ext)
    if not records:raise HTTPException(422,'未找到可识别书目，请检查文件格式')
    count=0
    for meta in records:
        existing=[db.loads(r['metadata'],{}) for r in db.rows('SELECT metadata FROM bibliography WHERE project_id=?',(pid,))]
        if any((meta.get('doi') and meta['doi']==x.get('doi')) or meta['title'].casefold()==str(x.get('title','')).casefold() for x in existing):continue
        db.execute('INSERT INTO bibliography(id,project_id,metadata,verification,created_at) VALUES(?,?,?,?,?)',(db.uid(),pid,db.dumps(meta),'unverified',db.now()));count+=1
    return {'imported':count,'recognized':len(records),'verification':'unverified'}

@router.post('/projects/{pid}/references/lookup')
def lookup_reference(pid,body:dict):
    security.require_project(pid);query=str(body.get('query','')).strip()
    if not query:raise HTTPException(422,'请输入DOI或题名')
    import httpx,urllib.parse
    try:
        with httpx.Client(timeout=20,trust_env=False,follow_redirects=False) as c:
            if re.match(r'^(?:https://doi.org/)?10\.',query):
                doi=query.removeprefix('https://doi.org/');r=c.get('https://api.crossref.org/works/'+urllib.parse.quote(doi,safe=''));r.raise_for_status();items=[r.json()['message']]
            else:r=c.get('https://api.crossref.org/works',params={'query.bibliographic':query,'rows':5});r.raise_for_status();items=r.json()['message']['items']
    except Exception as e:raise HTTPException(502,'元数据服务未能返回结果，请稍后重试或人工填写；这不代表文献不存在') from e
    result=[]
    for x in items:
        parts=x.get('published',{}).get('date-parts',[[None]])
        result.append({'title':(x.get('title') or [''])[0],'authors':[' '.join((a.get('given',''),a.get('family',''))).strip() for a in x.get('author',[])],'year':str(parts[0][0] or ''),'journal':(x.get('container-title') or [''])[0],'doi':x.get('DOI',''),'pages':x.get('page',''),'scope':'metadata_only','metadata_source':'Crossref','checked_at':db.now()})
    return {'candidates':result,'full_text_verified':False}

@router.post('/projects/{pid}/writing-batch')
@db.atomic
def batch_writing(pid,body:dict):
    p=security.require_project(pid)
    if not p['rule_package_id']:raise HTTPException(422,'请先发布规则包')
    sections=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,));selection=set(body.get('section_ids',[]));chosen=[s for s in sections if not selection or s['id'] in selection]
    chosen.sort(key=lambda s:(s['kind'] in ('abstract','abstract_en'),s['kind']=='abstract_en',s['position']))
    planned=[];skipped=[]
    for s in chosen:
        payload={'section_id':s['id'],'base_revision_id':s['current_revision_id'],'operation':body.get('operation','generate'),'instruction':body.get('instruction',''),'rule_package_id':p['rule_package_id']}
        if s['kind']=='abstract_en':
            from .abstract_translation import chinese_abstract
            try:
                source=chinese_abstract(pid,s)
                if any(item['id']==source['id'] for item in chosen):
                    skipped.append({'section_id':s['id'],'reason':'请先采用并保存本次中文摘要候选，再翻译英文摘要'})
                    continue
                if not source['current_revision_id'] or not documents.current(source)['text'].strip():
                    skipped.append({'section_id':s['id'],'reason':'请先完成并保存中文摘要，再翻译英文摘要'})
                    continue
            except HTTPException as error:
                if error.status_code!=422:raise
                skipped.append({'section_id':s['id'],'reason':str(error.detail)})
                continue
        tasks.writing_preflight(pid,payload);planned.append(payload)
    created=[tasks.enqueue(pid,'write',payload) for payload in planned]
    return {'tasks':created,'skipped_sections':skipped,'automatic_acceptance':False}

@router.get('/projects/{pid}/citations')
def citation_list(pid):
    security.require_project(pid);rows=db.rows('SELECT c.*,s.title AS section_title,r.text AS section_text FROM citations c JOIN revisions r ON r.id=c.revision_id JOIN sections s ON s.current_revision_id=r.id WHERE s.project_id=?',(pid,))
    for c in rows:c['sources']=[db.one('SELECT s.*,f.name FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=?',(i,)) for i in db.loads(c['source_ids'],[])]
    return rows
@router.patch('/citations/{cid}')
def confirm_citation(cid,body:dict):
    c=db.one('SELECT * FROM citations WHERE id=?',(cid,))
    if not c:raise HTTPException(404,'引用不存在')
    support=body.get('support')
    if support not in ('human_confirmed','partial','unsupported','unknown'):raise HTTPException(422,'核查状态无效')
    if support=='human_confirmed' and not db.loads(c['source_ids'],[]):raise HTTPException(422,'请先关联具体原文；没有原文不能标记为已确认支持')
    db.execute('UPDATE citations SET support=? WHERE id=?',(support,cid));db.audit(None,'citation_review',{'citation_id':cid,'support':support,'note':body.get('note','')});return {'support':support}
@router.post('/citations/{cid}/check')
def check_citation(cid):
    c=db.one('SELECT c.*,s.project_id FROM citations c JOIN revisions r ON r.id=c.revision_id JOIN sections s ON s.id=r.section_id WHERE c.id=?',(cid,))
    if not c:raise HTTPException(404,'引用不存在')
    return tasks.enqueue(c['project_id'],'support_check',{'citation_id':cid})
@router.post('/projects/{pid}/semantic-review')
def semantic_review(pid):security.require_project(pid);return tasks.enqueue(pid,'semantic_review',{})

@router.post('/projects/{pid}/charts')
def create_chart(pid,body:dict):
    security.require_project(pid);f=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(body['file_id'],pid))
    if not f or f['extension'] not in ('.csv','.xlsx'):raise HTTPException(422,'选择本项目CSV/XLSX')
    import pandas as pd,matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    path=security.safe_object(f['sha256']);frame=pd.read_excel(path,engine='openpyxl') if f['extension']=='.xlsx' else pd.read_csv(path)
    column=body.get('column')
    if column not in frame.columns:raise HTTPException(422,'列名不存在')
    values=pd.to_numeric(frame[column],errors='coerce').dropna()
    if values.empty:raise HTTPException(422,'本列没有可绘图数值')
    font=FontProperties(fname='C:/Windows/Fonts/msyh.ttc') if __import__('pathlib').Path('C:/Windows/Fonts/msyh.ttc').exists() else FontProperties()
    fig,ax=plt.subplots(figsize=(8,4.6),dpi=160);ax.hist(values,bins=min(20,max(3,len(values)//2)),color='#5d9a81',edgecolor='white');ax.set_title(body.get('title') or column+'分布',fontproperties=font);ax.set_xlabel(column,fontproperties=font);ax.set_ylabel('频数',fontproperties=font);ax.spines[['top','right']].set_visible(False);fig.tight_layout();buffer=io.BytesIO();fig.savefig(buffer,format='png');plt.close(fig)
    aid=db.uid();sha=security.put_blob(buffer.getvalue());meta={'source_file_id':f['id'],'source_hash':f['sha256'],'column':column,'operation':'histogram','n':len(values),'actual_execution':True,'egress':f['egress']};db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,body.get('title') or column+'分布图','figure',sha,db.dumps(meta),db.now()));return {'id':aid,'sha256':sha,'metadata':meta}
@router.get('/artifacts/{aid}/download')
def artifact_download(aid):
    from fastapi.responses import FileResponse
    a=db.one('SELECT * FROM artifacts WHERE id=?',(aid,))
    if not a:raise HTTPException(404,'研究产物不存在')
    return FileResponse(security.safe_object(a['sha256']),filename=a['name'])
@router.get('/projects/{pid}/review-history')
def review_history(pid):security.require_project(pid);return db.rows("SELECT * FROM project_revisions WHERE project_id=? AND kind='review_history' ORDER BY created_at DESC",(pid,))
@router.get('/projects/{pid}/rule-packages')
def rule_history(pid):security.require_project(pid);return db.rows('SELECT * FROM rule_packages WHERE project_id=? ORDER BY version DESC',(pid,))

@router.get('/projects/{pid}/research-map')
def research_map(pid):
    project=security.require_project(pid);brief=db.loads(project['brief'],{});result=[]
    from .writing_agent.common import state
    head=state(pid)
    if not brief.get('question') and head['core_version_id']:
        core=db.one('SELECT content_json FROM thesis_core_versions WHERE id=? AND project_id=?',(head['core_version_id'],pid))
        if core:
            core_content=db.loads(core['content_json'],{})
            brief['question']=core_content.get('research_question','')
    for section in db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,)):
        rev=documents.current(section);source_ids=set();fact_ids=set();artifact_ids=set();evidence_ids=set();claim_count=0
        if rev:
            for node in documents.nodes(db.loads(rev['document'],{})):
                attrs=node.get('attrs',{});source_ids.update(attrs.get('sourceIds',[]));fact_ids.update(attrs.get('factIds',[]));
                if attrs.get('artifactId'):artifact_ids.add(attrs['artifactId'])
                if attrs.get('sourceIds') or attrs.get('factIds'):claim_count+=1
            for cite in db.rows('SELECT source_ids FROM citations WHERE revision_id=?',(rev['id'],)):source_ids.update(db.loads(cite['source_ids'],[]))
        contract_id=head['contracts'].get(section['id']);contract=db.one('SELECT content_json FROM section_contract_versions WHERE id=? AND project_id=?',(contract_id,pid)) if contract_id else None
        contract_content=db.loads(contract['content_json'],{}) if contract else {}
        evidence_ids.update(contract_content.get('evidence_ids',[]));fact_ids.update(contract_content.get('fact_ids',[]));artifact_ids.update(contract_content.get('artifact_ids',[]))
        result.append({'section_id':section['id'],'title':section['title'],'goal':section['goal'],'revision_id':section['current_revision_id'],'claim_count':claim_count,'source_ids':sorted(source_ids),'fact_ids':sorted(fact_ids),'artifact_ids':sorted(artifact_ids),'evidence_ids':sorted(evidence_ids),'contract_id':contract_id,'stale':bool(section['stale'])})
    return {'research_question':brief.get('question',''),'sections':result}

@router.post('/projects/{pid}/outline/accept-candidate')
def accept_outline(pid,body:dict):
    from .schemas import OutlineCandidate
    security.require_project(pid)
    task=db.one("SELECT * FROM tasks WHERE id=? AND project_id=? AND kind='outline_ai' AND status='succeeded'",(body['task_id'],pid))
    if not task:raise HTTPException(404,'提纲候选不存在或未完成')
    existing=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived'",(pid,))
    if existing and body.get('mode')!='append':raise HTTPException(409,'已有提纲；请选择追加，避免覆盖人工内容')
    candidate=OutlineCandidate.model_validate({'sections':body.get('sections') or db.loads(task['result'],{}).get('sections',[])})
    position=max([s['position'] for s in existing],default=0)
    ids=[]
    with db.transaction() as c:
        if c.execute("SELECT id FROM project_revisions WHERE project_id=? AND kind='outline_accept' AND json_extract(content,'$.task_id')=?",(pid,task['id'])).fetchone():raise HTTPException(409,'此候选已采用，请直接编辑现有章节')
        for n,sec in enumerate(candidate.sections,1):
            sid=db.uid();ids.append(sid);c.execute('INSERT INTO sections(id,project_id,title,kind,position,target_words,goal) VALUES(?,?,?,?,?,?,?)',(sid,pid,sec.title,sec.kind,position+n,sec.target_words,sec.goal))
        c.execute('UPDATE projects SET outline_revision=outline_revision+1,updated_at=? WHERE id=?',(db.now(),pid))
        c.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'outline_accept',db.dumps({'task_id':task['id'],'sections':candidate.model_dump()}),db.now()))
    return {'section_ids':ids,'count':len(ids)}
@router.post('/provider/probe-generation')
def probe_generation(body:dict):
    pid=body.get('project_id');security.require_project(pid)
    if not security.key_status()['configured']:raise HTTPException(422,'请先填写API key')
    if not provider.config().get('model'):raise HTTPException(422,'请先选择实际模型ID')
    return tasks.enqueue(pid,'provider_probe',{})

@router.post('/files/{fid}/import-draft')
def import_draft_file(fid):
    from .drafts import import_draft
    file=db.one('SELECT * FROM files WHERE id=? AND active=1',(fid,))
    if not file:raise HTTPException(404,'当前资料不存在')
    return import_draft(file)
