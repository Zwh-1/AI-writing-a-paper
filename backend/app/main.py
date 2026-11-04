import os,json,asyncio,hashlib,re,zipfile,io,mimetypes,sqlite3,secrets
from datetime import datetime,timedelta,timezone
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request,HTTPException,UploadFile,File,Form
from fastapi.responses import FileResponse,JSONResponse,StreamingResponse
from fastapi.staticfiles import StaticFiles
from . import db,security,parsers,rules,documents,provider,tasks,reviews,exports,material_quality
from .config import ROOT,DATA,PORT,APP_VERSION,MAX_FILE
from . import material_summary
from . import model_routing
from . import deepseek_pool
from . import deepseek_profiles
from .api.v2 import install as install_api_v2

@asynccontextmanager
async def lifespan(app):
    db.init()
    from .runtime_identity import loaded
    loaded('server')
    session_directory=Path(os.environ.get('PAPER_SESSION_DIR',str(ROOT/'.runtime')))
    session_directory.mkdir(parents=True,exist_ok=True)
    (session_directory/f'session-{PORT}.json').write_text(db.dumps({'port':PORT,'bootstrap_token':security.BOOTSTRAP_TOKEN,'pid':os.getpid(),'data_directory':str(DATA)}),encoding='utf-8')
    yield

app=FastAPI(title='砚台 · 论文工作台',version=APP_VERSION,lifespan=lifespan)
from .api.routers import mount as mount_api_routers
mount_api_routers(app)
from pydantic import ValidationError
@app.exception_handler(ValidationError)
async def writing_contract_error(request,error):
    return JSONResponse({'detail':{'code':'INVALID_CONTRACT','message':'输入或阶段产物不符合约定结构','fields':[{'location':list(x['loc']),'message':x['msg'],'type':x['type']} for x in error.errors(include_input=False)]}},422)
@app.middleware('http')
async def boundary(request,call_next):
    host=request.headers.get('host','').split(':')[0]
    configured_hosts={value.strip() for value in os.environ.get('PAPER_ALLOWED_HOSTS','').split(',') if value.strip()} if os.environ.get('PAPER_CONTAINER_DEPLOYMENT')=='1' else set()
    if host not in {'127.0.0.1','localhost','testserver',*configured_hosts}: return JSONResponse({'detail':'本地服务不接受此Host'},403)
    origin=request.headers.get('origin')
    configured_origins={value.strip() for value in os.environ.get('PAPER_ALLOWED_ORIGINS','').split(',') if value.strip()} if os.environ.get('PAPER_CONTAINER_DEPLOYMENT')=='1' else set()
    if origin and origin not in {f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}','http://127.0.0.1:5173','http://localhost:5173','http://testserver',*configured_origins}: return JSONResponse({'detail':'不允许的请求来源'},403)
    public=request.url.path in ('/api/v1/health','/api/v1/session','/api/v1/studio/zotero/callback','/api/v2/studio/zotero/callback')
    if request.url.path.startswith('/api/') and not public:
        if not secrets.compare_digest(request.cookies.get('paper_session',''),security.SESSION_TOKEN): return JSONResponse({'detail':'请通过启动器打开本机工作台'},401)
        if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token',''),security.SESSION_TOKEN): return JSONResponse({'detail':'会话校验失败'},403)
    response=await call_next(request)
    if response.headers.get('content-type','').startswith('text/html'):
        response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin';response.headers['X-Frame-Options']='SAMEORIGIN'
    return response
@app.exception_handler(ValueError)
async def value_error(req,e): return JSONResponse({'detail':security.redact(e)},422)
@app.exception_handler(provider.ProviderError)
async def upstream_error(req,e): return JSONResponse({'detail':security.redact(e),'outcome_unknown':e.unknown},502)
@app.get('/api/v1/health')
def health():
    from .runtime_identity import loaded
    identity=loaded('server')
    return {'status':'ok','version':APP_VERSION,'key_configured':security.key_status()['configured'],
            'build_id':identity['build_id'],'loaded_at':identity['loaded_at'],
            'capabilities':identity['capabilities'],'health_scope':'http_liveness'}
@app.post('/api/v1/session')
def session(body:dict,request:Request):
    token=body.get('token','')
    if token and secrets.compare_digest(token,security.BOOTSTRAP_TOKEN):
        response=JSONResponse({'csrf':security.SESSION_TOKEN});response.set_cookie('paper_session',security.SESSION_TOKEN,httponly=True,samesite='strict',max_age=86400);return response
    if secrets.compare_digest(request.cookies.get('paper_session',''),security.SESSION_TOKEN): return {'csrf':security.SESSION_TOKEN}
    raise HTTPException(401,'请运行启动脚本，通过带会话的本机链接进入')
def body_project(pid): return security.require_project(pid)
def file_record(fid):
    f=db.one('SELECT * FROM files WHERE id=?',(fid,))
    if not f: raise HTTPException(404,'文件不存在')
    return f
@app.get('/api/v1/projects')
def projects(include_archived:bool=False): return db.rows('SELECT * FROM projects'+('' if include_archived else ' WHERE archived=0')+' ORDER BY archived,updated_at DESC')
@app.post('/api/v1/projects')
def create_project(body:dict):
    title=str(body.get('title','')).strip()
    if not title or len(title)>300: raise HTTPException(422,'请输入1–300字的论文题目或项目名称')
    pid=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,paper_type,brief,target_words,budget,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(pid,title,body.get('paper_type','software_system'),db.dumps(body.get('brief',{})),max(500,min(int(body.get('target_words',15000)),300000)),max(0,float(body.get('budget',20))),stamp,stamp));return body_project(pid)
@app.get('/api/v1/projects/{pid}')
def project(pid):
    p=body_project(pid);p['brief']=db.loads(p['brief'],{});p['stats']={name:db.one(f'SELECT COUNT(*) AS n FROM {table} WHERE project_id=?',(pid,))['n'] for name,table in [('files','files'),('rules','rules'),('sections','sections'),('facts','facts'),('references','bibliography')]};p['word_count']=sum(documents.counts(r['text'])['total'] for r in db.rows('SELECT r.text FROM sections s JOIN revisions r ON r.id=s.current_revision_id WHERE s.project_id=?',(pid,)));p['findings']=db.rows('SELECT * FROM findings WHERE project_id=? ORDER BY created_at DESC LIMIT 12',(pid,));p['cost']=db.one('SELECT COALESCE(SUM(cost),0) AS known_cost,SUM(CASE WHEN cost IS NULL AND status=\'completed\' THEN 1 ELSE 0 END) AS unknown_calls FROM generation_runs WHERE project_id=?',(pid,));return p
@app.patch('/api/v1/projects/{pid}')
def patch_project(pid,body:dict):
    old=body_project(pid)
    db.execute('INSERT INTO project_revisions VALUES(?,?,?,?,?)',(db.uid(),pid,'project',db.dumps(old),db.now()))
    for key in ('title','paper_type','target_words','budget','stage','archived','brief'):
        if key in body:
            val=db.dumps(body[key]) if key=='brief' else body[key]
            if key in ('budget','target_words') and float(val)<0: raise HTTPException(422,'预算和篇幅不能为负数')
            db.execute(f'UPDATE projects SET {key}=?,updated_at=? WHERE id=?',(val,db.now(),pid))
    if 'brief' in body: documents.fact_changed(pid,'brief')
    return project(pid)
@app.get('/api/v1/projects/{pid}/budget')
def project_budget(pid):
    body_project(pid)
    from .summary_billing import budget
    return budget(pid)
@app.get('/api/v1/projects/{pid}/files')
def files(pid):
    body_project(pid)
    summary_states={r['file_id']:r for r in material_summary.batch_status(pid)['files']}
    return [{**f,'parse_quality':material_quality.summary(f),'summary_state':summary_states.get(f['id'],{}).get('state','not_started')} for f in db.rows('SELECT * FROM files WHERE project_id=? ORDER BY created_at DESC',(pid,))]
@app.get('/api/v1/material-summary/settings')
def summary_settings():return material_summary.settings()
@app.put('/api/v1/material-summary/settings')
def save_summary_settings(body:dict):return material_summary.save_settings(body)
@app.post('/api/v1/material-summary/models')
def summary_models():return material_summary.models()
@app.get('/api/v1/files/{fid}/summary')
def material_summary_status(fid):return material_summary.status(fid)
@app.post('/api/v1/files/{fid}/summary')
def summarize_material(fid):return material_summary.enqueue(fid)
@app.get('/api/v1/projects/{pid}/materials/summary-status')
def material_summary_batch_status(pid):return material_summary.batch_status(pid)
@app.post('/api/v1/projects/{pid}/materials/summary-billing')
def reconcile_summary_billing(pid):
    from .summary_billing import reconcile
    body_project(pid);return reconcile(pid)
@app.post('/api/v1/projects/{pid}/tasks/billing-reconcile')
def reconcile_task_billing(pid,body:dict):
    from .summary_billing import reconcile,budget
    body_project(pid);profile=body.get('profile')
    if profile is not None:
        if profile not in ('material-summary','gpt-fallback','primary','image-generation'):raise HTTPException(422,'账单核对接口类型无效')
        return reconcile(pid,profile=profile)
    # Query configured routes. A failed query must not
    # turn into a success toast or release any unresolved request reservation.
    results=[]
    for route in ('primary','material-summary','gpt-fallback','image-generation'):
        if route=='image-generation':
            from .image_generation import ready as image_ready
            if not image_ready():continue
        if route=='gpt-fallback' and not model_routing.ready():continue
        if route=='material-summary' and not security.key_status(route)['configured']:continue
        if route=='primary' and not security.key_status()['configured']:continue
        try:results.append({'profile':route,**reconcile(pid,profile=route)})
        except (ValueError,provider.ProviderError,HTTPException) as error:
            results.append({'profile':route,'reconciled':False,'reason':security.redact(error)})
    return {'profiles':results,'budget':budget(pid),
            'message':None if results else '未找到已配置的账单查询接口，费用预留保持不变。'}
@app.post('/api/v1/projects/{pid}/materials/resume-summaries')
def resume_summary_tasks(pid):return material_summary.resume_budget(pid)
@app.get('/api/v1/files/{fid}/summary/markdown')
def material_summary_markdown(fid):
    from fastapi.responses import Response
    from urllib.parse import quote
    state=material_summary.status(fid);summary=state['summary'];f=file_record(fid)
    if not summary:raise HTTPException(404,'尚无已保存的总结')
    lines=['# '+f['name'],'','## 整体概括','',summary['overview'],'','## 核心要点','']
    def point(p):return p['text']+'（第 '+ '、'.join(str(n) for n in p['pages'])+' 页）'
    lines.extend('- '+point(p) for p in summary['key_points']);lines.extend(['','## 主题归纳',''])
    for topic in summary['topics']:
        lines.extend(['### '+topic['title'],'']);lines.extend('- '+point(p) for p in topic['points']);lines.append('')
    lines.extend(['模型：'+summary['model'],'AI 整理结果需对照原文核对。'])
    if summary.get('source_review'):lines.extend(['','来源：Codex 结合原文与已保存分段复核整理；未创建新的远端生成请求。'])
    if summary.get('excluded_pages'):
        lines.extend(['','未纳入正文归纳：'])
        lines.extend('- 第 '+str(p['page'])+' 页：'+p['reason'] for p in summary['excluded_pages'])
    return Response('\n'.join(lines),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(f['name']+'-总结.md')})
@app.post('/api/v1/projects/{pid}/materials/summarize')
def summarize_materials(pid):
    body_project(pid);queued=[];skipped=[];cached=0
    for f in db.rows('SELECT id,name FROM files WHERE project_id=? AND active=1 ORDER BY created_at',(pid,)):
        try:
            result=material_summary.enqueue(f['id'])
            if not result.get('cached'):queued.append(result['task']['id'])
            else:cached+=1
        except HTTPException as error:skipped.append({'file_id':f['id'],'name':f['name'],'reason':error.detail})
    return {'task_ids':queued,'count':len(queued),'cached':cached,'skipped':skipped}
@app.post('/api/v1/projects/{pid}/materials/repair')
def repair_materials(pid):
    body_project(pid);queued=[]
    for f in db.rows('SELECT * FROM files WHERE project_id=? AND active=1 ORDER BY created_at',(pid,)):
        if material_quality.summary(f)['repair_needed']:
            queued.append(tasks.enqueue(pid,'parse',{'file_id':f['id'],'repair':True})['id'])
    return {'task_ids':queued,'count':len(queued),'external_requests':False,'message':'本地补解析已排队；保留来源编号，结果仍需人工核对'}
@app.post('/api/v1/files/{fid}/reparse')
def reparse_file(fid):
    f=file_record(fid)
    return tasks.enqueue(f['project_id'],'parse',{'file_id':fid,'repair':True})
@app.get('/api/v1/files/{fid}/parse-report')
def parse_report(fid):
    f=file_record(fid);return material_quality.summary(f,include_pages=True)
@app.post('/api/v1/projects/{pid}/files')
async def upload(pid:str,file:UploadFile=File(...),role:str=Form('research'),egress:str=Form('selected_content')):
    body_project(pid);data=await file.read(MAX_FILE+1);f,new=parsers.register_file(pid,file.filename or 'file.txt',data,role,egress)
    task=tasks.enqueue(pid,'parse',{'file_id':f['id']}) if new or f['status']!='ready' or material_quality.summary(f)['repair_needed'] else None
    return {'file':f,'task':task}
@app.post('/api/v1/projects/{pid}/imports/local')
def import_local(pid,body:dict):
    body_project(pid);source=(ROOT.parent/'格式').resolve()
    if not source.is_dir(): raise HTTPException(404,'未找到工作目录旁的“格式”文件夹，请上传资料')
    result=[]
    for path in sorted(source.iterdir()):
        if path.is_file() and path.suffix.lower() in parsers.SUPPORTED:
            role='rules' if path.suffix.lower()=='.pptx' or '说明' in path.name else 'example'
            f,new=parsers.register_file(pid,path.name,path.read_bytes(),role,'selected_content')
            task=tasks.enqueue(pid,'parse',{'file_id':f['id']}) if new or f['status']!='ready' or material_quality.summary(f)['repair_needed'] else None;result.append({'file_id':f['id'],'name':path.name,'new':new,'task_id':task['id'] if task else None})
    return {'files':result}
@app.patch('/api/v1/files/{fid}')
def patch_file(fid,body:dict):
    f=file_record(fid)
    for key in ('role','egress','active'):
        if key in body:
            if key=='role' and body[key] not in ('rules','example','research','draft','data','supervisor'): raise HTTPException(422,'用途无效')
            if key=='egress' and body[key] not in ('local_only','selected_content','allowed'): raise HTTPException(422,'策略无效')
            db.execute(f'UPDATE files SET {key}=? WHERE id=?',(body[key],fid))
    if 'role' in body and body['role']!=f['role']:
        old_kind='example' if f['role']=='example' else 'rule_candidate' if f['role']=='rules' else 'commentary' if f['role'] in ('draft','supervisor') else 'evidence'
        new_kind='example' if body['role']=='example' else 'rule_candidate' if body['role']=='rules' else 'commentary' if body['role'] in ('draft','supervisor') else 'evidence'
        db.execute('UPDATE sources SET kind=? WHERE file_id=? AND kind=?',(new_kind,fid,old_kind))
    from .reference_knowledge import sync_document
    sync_document(fid)
    from .knowledge_lineage import changed as knowledge_changed,access_changed
    if any(key in body and body[key]!=f[key] for key in ('role','active')):knowledge_changed(f['project_id'],'file',fid,'原件用途或有效状态变化')
    if 'egress' in body and body['egress']!=f['egress']:access_changed(f['project_id'],fid)
    documents.fact_changed(f['project_id'],fid);return file_record(fid)
@app.get('/api/v1/files/{fid}/download')
def download_file(fid):
    f=file_record(fid);return FileResponse(security.safe_object(f['sha256']),filename=f['name'])
@app.get('/api/v1/files/{fid}/pages')
def pages(fid):
    f=file_record(fid);quality=material_quality.summary(f,include_pages=True)
    states={p['id']:p for p in quality['page_states']}
    return [{**p,'extraction':states.get(p['id'],{})} for p in db.rows('SELECT * FROM pages WHERE file_id=? ORDER BY page_number',(fid,))]
@app.post('/api/v1/files/{fid}/review')
def review_document(fid,body:dict):
    from .material_review import review_file
    result=review_file(fid,body)
    return {**result,'pages':pages(fid),'parse_report':material_quality.summary(file_record(fid),include_pages=True)}
@app.get('/api/v1/projects/{pid}/materials/review')
def project_material_review_state(pid):
    from .material_review import project_state
    return project_state(pid)
@app.post('/api/v1/projects/{pid}/materials/review')
def review_all_materials(pid,body:dict):
    from .material_review import review_project as confirm_originals
    return confirm_originals(pid,body)
@app.get('/api/v1/pages/{page_id}')
def page_detail(page_id):
    p=db.one('SELECT p.*,f.project_id,f.name,f.extension FROM pages p JOIN files f ON f.id=p.file_id WHERE p.id=?',(page_id,))
    if not p: raise HTTPException(404,'页面不存在')
    p['images']=db.loads(p['images'],[]);p['sources']=db.rows('SELECT * FROM sources WHERE page_id=?',(page_id,));p['extraction']=next((x for x in material_quality.summary(file_record(p['file_id']),True)['page_states'] if x['id']==page_id),{});return p
@app.patch('/api/v1/pages/{page_id}')
def review_page(page_id,body:dict):
    p=page_detail(page_id);state=body.get('review_state',p['review_state'])
    if state not in ('unreviewed','reviewed','not_applicable'): raise HTTPException(422,'核对状态无效')
    if state=='not_applicable' and not body.get('review_note'): raise HTTPException(422,'排除页面需要填写理由')
    if 'transcript' in body and body['transcript'].strip():
        text=str(body['transcript']);sid=db.uid()
        db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,p['file_id'],page_id,db.dumps({'kind':'manual_transcript','page':p['page_number']}),text,text,'rule_candidate' if file_record(p['file_id'])['role']=='rules' else 'example' if file_record(p['file_id'])['role']=='example' else 'evidence','manual',1))
        db.execute('INSERT INTO source_fts VALUES(?,?)',(sid,parsers.tokens(text)))
        db.execute('UPDATE pages SET text=text||? WHERE id=?',('\n[人工核对转录]\n'+text,page_id))
    db.execute('UPDATE pages SET review_state=?,review_note=? WHERE id=?',(state,body.get('review_note',''),page_id));db.execute('UPDATE sources SET verified=? WHERE page_id=?',(int(state=='reviewed'),page_id))
    from .reference_knowledge import sync_document
    sync_document(p['file_id'])
    if body.get('transcript') or state=='not_applicable' or p['review_state']=='not_applicable':
        from .knowledge_lineage import changed as knowledge_changed
        knowledge_changed(p['project_id'],'file',p['file_id'],'原页转录或适用范围变化')
    return page_detail(page_id)
@app.post('/api/v1/pages/{page_id}/ocr')
def ocr_page(page_id):
    p=page_detail(page_id);return tasks.enqueue(p['project_id'],'ocr',{'page_id':page_id})
@app.get('/api/v1/pages/{page_id}/preview')
def page_preview(page_id):
    p=page_detail(page_id)
    if p['extension']!='.pdf': raise HTTPException(422,'PPT页面请查看原文件及提取的图片')
    import pypdfium2 as pdfium
    f=file_record(p['file_id']);pdf=pdfium.PdfDocument(str(security.safe_object(f['sha256'])));page=pdf[p['page_number']-1];bitmap=page.render(scale=1.25);image=bitmap.to_pil();path=DATA/'previews'/f'{page_id}.png';image.save(path);bitmap.close();page.close();pdf.close();return FileResponse(path)
@app.get('/api/v1/images/{sha}')
def image(sha):
    # Require the asset to be reachable from an imported page or project artifact.
    if not db.one('SELECT id FROM pages WHERE images LIKE ? LIMIT 1',('%'+sha+'%',)) and not db.one('SELECT id FROM artifacts WHERE sha256=? LIMIT 1',(sha,)): raise HTTPException(404,'图片不存在')
    path=security.safe_object(sha)
    from PIL import Image
    try:
        with Image.open(path) as im: mime=Image.MIME.get(im.format,'image/png')
    except Exception: raise HTTPException(422,'不是可预览的图片')
    return FileResponse(path,media_type=mime)
@app.get('/api/v1/source-units/{sid}')
def source(sid):
    r=db.one('SELECT s.*,f.project_id,f.name,f.extension,p.page_number FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=?',(sid,))
    if not r: raise HTTPException(404,'来源不存在')
    return r
@app.post('/api/v1/projects/{pid}/search')
def search(pid,body:dict): body_project(pid);return parsers.search(pid,body.get('query',''),body.get('kind','research'),body.get('limit',12))
@app.get('/api/v1/projects/{pid}/rules')
def get_rules(pid):
    body_project(pid)
    from .rule_workspace import workspace
    return workspace(pid)
@app.get('/api/v1/rules/{rid}/source')
def rule_source(rid):
    r=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    if not r:raise HTTPException(404,'规则不存在')
    body_project(r['project_id'])
    if not r['source_id']:return {'source':None,'file':None,'page':None}
    source=db.one('SELECT * FROM sources WHERE id=?',(r['source_id'],))
    if not source:raise HTTPException(404,'规则原文已不存在')
    from .rule_source_context import read
    return {'source':source,'file':file_record(source['file_id']),'page':page_detail(source['page_id']) if source['page_id'] else None,
            'reading_context':read(r,source)}
@app.post('/api/v1/projects/{pid}/rules/extract')
def extract_rules(pid): body_project(pid);return tasks.enqueue(pid,'extract_rules',{})
@app.post('/api/v1/projects/{pid}/rules')
def add_rule(pid,body:dict):
    body_project(pid);statement=str(body.get('statement','')).strip()
    if not statement: raise HTTPException(422,'规则内容不能为空')
    from .rule_engine import validate_execution,execution_key
    from .rule_workspace import validate_rule
    validate_rule(body)
    if body.get('state')=='machine_verified':raise HTTPException(422,'机器审核状态由自动校验生成')
    _,_,operations=validate_execution(body)
    if not body.get('rule_key'):body['rule_key']=execution_key(body.get('checker','manual'),body.get('value'))
    if body.get('source_id') and not db.one('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(body['source_id'],pid)):raise HTTPException(422,'规则原文不属于本项目')
    rid=db.uid();db.execute('INSERT INTO rules(id,project_id,category,scope,statement,modality,state,checker,rule_key,value,decision_note,operations,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(rid,pid,body.get('category','content'),body.get('scope','global'),statement,body.get('modality','mandatory'),body.get('state','confirmed'),body.get('checker','manual'),body.get('rule_key',''),db.dumps(body.get('value')),body.get('decision_note','用户新增要求'),db.dumps(operations),db.now()))
    if body.get('source_id'):
        if not db.one('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(body['source_id'],pid)):raise HTTPException(422,'规则原文不属于本项目')
        db.execute('UPDATE rules SET source_id=? WHERE id=?',(body['source_id'],rid))
    if 'operations' in body:db.execute('UPDATE rules SET operations=? WHERE id=?',(db.dumps(body['operations']),rid))
    if 'conditions' in body:db.execute('INSERT INTO rule_conditions VALUES(?,?)',(rid,db.dumps(body['conditions'])))
    return db.one('SELECT * FROM rules WHERE id=?',(rid,))
@app.patch('/api/v1/rules/{rid}')
def patch_rule(rid,body:dict):
    r=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    if not r:raise HTTPException(404,'规则不存在')
    if body.get('state')=='machine_verified' and r['state']!='machine_verified':raise HTTPException(422,'请运行自动审核生成机器审核状态')
    if r['state']=='machine_verified' and any(k in body and (db.dumps(body[k]) if k in ('value','operations') else body[k])!=r.get(k) for k in ('statement','scope','modality','checker','rule_key','value','operations','source_id','category')):
        if body.get('state')!='confirmed':body['state']='candidate'
    from .rule_engine import validate_execution,execution_key
    from .rule_workspace import validate_rule
    validate_rule(body)
    condition=db.one('SELECT expression FROM rule_conditions WHERE rule_id=?',(rid,))
    merged={**r,'value':db.loads(r['value']),'operations':db.loads(r['operations']),'conditions':db.loads(condition['expression'],{}) if condition else {},**body}
    validate_execution(merged)
    if ('checker' in body or 'value' in body) and 'rule_key' not in body:body['rule_key']=execution_key(merged['checker'],merged['value'])
    if 'source_id' in body and body['source_id'] and not db.one('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(body['source_id'],r['project_id'])):raise HTTPException(422,'规则原文不属于本项目')
    with db.transaction() as c:
        for key in ('statement','category','scope','modality','state','checker','rule_key','value','decision_note','operations','source_id'):
            if key in body:c.execute(f'UPDATE rules SET {key}=? WHERE id=?',(db.dumps(body[key]) if key in ('value','operations') else body[key],rid))
        if 'conditions' in body:c.execute('INSERT INTO rule_conditions VALUES(?,?) ON CONFLICT(rule_id) DO UPDATE SET expression=excluded.expression',(rid,db.dumps(body['conditions'])))
    from .knowledge_lineage import changed
    changed(r['project_id'],'rule',rid,'论文规则已修改，复用内容需复查')
    db.audit(r['project_id'],'edit_rule',{'id':rid,'previous':r,'changes':body});return db.one('SELECT * FROM rules WHERE id=?',(rid,))
@app.post('/api/v1/projects/{pid}/rules/batch-review')
def batch_review_rules(pid,body:dict):
    body_project(pid);ids=body.get('rule_ids');state=body.get('state')
    if not isinstance(ids,list) or not ids or len(ids)>2000 or any(not isinstance(rid,str) for rid in ids):raise HTTPException(422,'请选择1至2000条规则')
    if state not in ('candidate','rejected'):raise HTTPException(422,'批量操作仅支持排除或恢复待确认；确认条款请逐条核对')
    selected=set(ids)
    valid={r['id'] for r in db.rows('SELECT id FROM rules WHERE project_id=?',(pid,))}
    if not selected.issubset(valid):raise HTTPException(422,'所选规则含其他项目或已不存在的条款')
    with db.transaction() as c:
        for rid in selected:c.execute('UPDATE rules SET state=? WHERE id=?',(state,rid))
    db.audit(pid,'batch_review_rules',{'rule_ids':sorted(selected),'state':state})
    from .knowledge_lineage import changed
    for rid in selected:changed(pid,'rule',rid,'规则批量状态已修改，复用内容需复查')
    return {'updated':len(selected),'state':state}
@app.post('/api/v1/projects/{pid}/rules/duplicates/merge')
def merge_duplicate_rules(pid,body:dict):
    from .rule_duplicates import merge
    body_project(pid)
    return merge(pid,body.get('canonical_id'),body.get('duplicate_ids'),review_id=body.get('review_id'),expected_fingerprint=body.get('expected_fingerprint'))

@app.post('/api/v1/projects/{pid}/rules/duplicates/review')
def review_duplicate_rules(pid,body:dict):
    from .duplicate_workflow import prepare
    body_project(pid)
    return prepare(pid,body.get('canonical_id'),body.get('duplicate_ids'))

@app.post('/api/v1/projects/{pid}/rules/duplicates/adopt')
def adopt_duplicate_review(pid,body:dict):
    from .duplicate_workflow import adopt
    body_project(pid)
    if not isinstance(body.get('review_id'),str) or not isinstance(body.get('expected_fingerprint'),str):raise HTTPException(422,'请选择当前复核记录')
    return adopt(pid,body['review_id'],body['expected_fingerprint'])

@app.get('/api/v1/projects/{pid}/rules/duplicates/reviews/{review_id}')
def get_duplicate_review(pid:str,review_id:str):
    from .duplicate_workflow import review_key
    body_project(pid)
    value=db.setting(review_key(pid,review_id),{})
    if not value:raise HTTPException(404,'重复资料复核记录不存在')
    return value
@app.post('/api/v1/projects/{pid}/rules/duplicates/{merge_id}/restore')
def restore_duplicate_rules(pid,merge_id:int):
    from .rule_duplicates import restore
    body_project(pid)
    return restore(pid,merge_id)
@app.post('/api/v1/projects/{pid}/rule-packages')
def publish_rules(pid): body_project(pid);return rules.publish(pid)
@app.get('/api/v1/projects/{pid}/sections')
def sections(pid):
    body_project(pid);return db.rows("SELECT s.*,r.text AS current_text,r.document AS current_document FROM sections s LEFT JOIN revisions r ON r.id=s.current_revision_id WHERE s.project_id=? AND s.state!='archived' ORDER BY s.position",(pid,))
@app.post('/api/v1/projects/{pid}/sections')
def add_section(pid,body:dict):
    body_project(pid)
    from .completion import active_sections,validate_outline
    sid=db.uid();position=db.one('SELECT COALESCE(MAX(position),0)+1 AS n FROM sections WHERE project_id=?',(pid,))['n']
    new={'id':sid,'parent_id':body.get('parent_id'),'dependencies':body.get('dependencies',[])}
    validate_outline(active_sections(pid)+[new])
    title=str(body.get('title','新章节')).strip();words=int(body.get('target_words',1500))
    if not title or len(title)>300 or not 0<=words<=300000:raise HTTPException(422,'章节标题或目标篇幅无效')
    with db.transaction() as c:
        c.execute('INSERT INTO sections(id,project_id,parent_id,title,kind,position,target_words,goal,dependencies) VALUES(?,?,?,?,?,?,?,?,?)',(sid,pid,new['parent_id'],title,body.get('kind','body'),body.get('position',position),words,body.get('goal',''),db.dumps(new['dependencies'])))
        c.execute('UPDATE projects SET outline_revision=outline_revision+1 WHERE id=?',(pid,))
    return security.require_section(sid)
@app.post('/api/v1/projects/{pid}/outline/template')
def outline_template(pid):
    p=body_project(pid)
    if not p['rule_package_id']: raise HTTPException(422,'请先发布论文规则包')
    if db.one('SELECT id FROM sections WHERE project_id=?',(pid,)): raise HTTPException(409,'已有提纲，请修改或追加章节，不会覆盖已有正文')
    templates={'software_system':[('摘要','abstract',350),('Abstract','abstract_en',350),('第一章 绪论','introduction',2000),('第二章 相关技术与研究现状','literature',2200),('第三章 需求分析','requirements',1800),('第四章 系统设计','system_design',3000),('第五章 系统实现','implementation',2800),('第六章 系统测试与结果分析','results',2000),('第七章 总结与展望','conclusion',1000)],'experimental':[('摘要','abstract',350),('Abstract','abstract_en',350),('绪论与相关研究','introduction',3000),('研究方法与实验设计','methods',3000),('实验结果','results',3000),('讨论','discussion',2200),('结论与展望','conclusion',1000)],'review':[('摘要','abstract',350),('引言','introduction',2000),('检索方法与筛选范围','methods',1800),('主题分类与比较','literature',5000),('问题与研究趋势','discussion',2500),('结论','conclusion',1000)]}
    chosen=templates.get(p['paper_type'],templates['experimental']);items=[]
    for title,kind,words in chosen: items.append(add_section(pid,{'title':title,'kind':kind,'target_words':words,'goal':'按已确认规则组织本节内容，材料不足时列出缺口。'}))
    dependencies=[s['id'] for s in items if s['kind'] not in ('abstract','abstract_en')]
    for s in items:
        if s['kind']=='abstract': db.execute('UPDATE sections SET dependencies=? WHERE id=?',(db.dumps(dependencies),s['id']))
        if s['kind']=='abstract_en': db.execute('UPDATE sections SET dependencies=? WHERE id=?',(db.dumps([x['id'] for x in items if x['kind']=='abstract']),s['id']))
    return sections(pid)
@app.patch('/api/v1/sections/{sid}')
def patch_section(sid,body:dict):
    s=security.require_section(sid)
    from .completion import active_sections,validate_outline
    validate_outline([{**n,**body} if n['id']==sid else n for n in active_sections(s['project_id'])])
    if body.get('state') not in (None,'empty','draft','user_reviewed'):raise HTTPException(422,'归档请使用专用接口')
    for key in ('title','kind','position','target_words','goal','dependencies','state','parent_id'):
        if key in body:
            if key=='dependencies':
                for dep in body[key]:
                    if dep==sid or security.require_section(dep)['project_id']!=s['project_id']: raise HTTPException(422,'章节依赖无效')
            db.execute(f'UPDATE sections SET {key}=? WHERE id=?',(db.dumps(body[key]) if key=='dependencies' else body[key],sid))
    db.execute('UPDATE projects SET outline_revision=outline_revision+1,updated_at=? WHERE id=?',(db.now(),s['project_id']))
    from .knowledge_lineage import changed
    changed(s['project_id'],'outline',sid,'章节计划已修改，复用内容需复查')
    if any(k in body for k in ('title','kind','goal','parent_id','dependencies')):documents.mark_downstream(s['project_id'],sid,'章节计划或依赖已修改')
    return security.require_section(sid)
@app.get('/api/v1/sections/{sid}/content')
def content(sid):
    s=security.require_section(sid);r=documents.current(s);return {'section':s,'revision':r,'document':db.loads(r['document']) if r else documents.text_document(''),'locks':[r['node_id'] for r in db.rows('SELECT node_id FROM locks WHERE section_id=?',(sid,))]}
@app.put('/api/v1/sections/{sid}/content')
def save_content(sid,body:dict): return documents.save(security.require_section(sid),body['document'],body.get('base_revision_id'))
@app.get('/api/v1/sections/{sid}/revisions')
def revisions(sid): security.require_section(sid);return db.rows('SELECT * FROM revisions WHERE section_id=? ORDER BY created_at DESC LIMIT 100',(sid,))
@app.post('/api/v1/sections/{sid}/restore')
def restore_revision(sid,body:dict):
    s=security.require_section(sid);r=db.one('SELECT * FROM revisions WHERE id=? AND section_id=?',(body['revision_id'],sid))
    if not r: raise HTTPException(404,'历史版本不存在')
    return documents.save(s,db.loads(r['document'],{}),body.get('base_revision_id'),kind='restore')
@app.put('/api/v1/sections/{sid}/locks')
def set_locks(sid,body:dict):
    s=security.require_section(sid);r=documents.current(s);valid={n.get('attrs',{}).get('nodeId') for n in documents.nodes(db.loads(r['document'],{}))} if r else set()
    if any(x not in valid for x in body.get('node_ids',[])): raise HTTPException(422,'锁定节点不存在')
    with db.transaction() as c:
        c.execute('DELETE FROM locks WHERE section_id=?',(sid,))
        for node in body.get('node_ids',[]): c.execute('INSERT INTO locks VALUES(?,?)',(sid,node))
    return {'node_ids':body.get('node_ids',[])}
@app.post('/api/v1/sections/{sid}/writing-tasks')
def create_writing(sid,body:dict,request:Request):
    s=security.require_section(sid)
    body['section_id']=sid;p,_=tasks.writing_preflight(s['project_id'],body);body['rule_package_id']=p['rule_package_id']
    if s['kind']!='abstract_en':
        from .writing_continuity import status
        preview=status(s['project_id'],sid)
        if not preview['can_load']:raise HTTPException(422,{'code':preview['code'],'message':preview['message']})
    return tasks.enqueue(s['project_id'],'write',body,request.headers.get('idempotency-key'))
@app.get('/api/v1/sections/{sid}/writing-context')
def writing_context(sid):
    from .writing_continuity import status
    s=security.require_section(sid);return status(s['project_id'],sid)
@app.post('/api/v1/sections/{sid}/accept')
def accept(sid,body:dict,request:Request=None):
    from .writing_agent.adoption import adopt_legacy
    return adopt_legacy(sid,body,request.headers.get('idempotency-key','') if request else '')
@app.get('/api/v1/projects/{pid}/facts')
def facts(pid): body_project(pid);return db.rows('SELECT * FROM facts WHERE project_id=? ORDER BY created_at',(pid,))
@app.post('/api/v1/projects/{pid}/facts')
def add_fact(pid,body:dict):
    body_project(pid);fid=db.uid();stamp=db.now()
    if body.get('artifact_id') and not db.one('SELECT id FROM artifacts WHERE id=? AND project_id=?',(body['artifact_id'],pid)):raise HTTPException(422,'研究产物不属于当前项目')
    for source_id in body.get('source_ids',[]):
        if source(source_id)['project_id']!=pid: raise HTTPException(422,'来源不属于项目')
    db.execute('INSERT INTO facts(id,project_id,label,statement,status,verification,source_ids,artifact_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(fid,pid,body.get('label','研究事实'),body.get('statement',''),body.get('status','planned'),body.get('verification','unverified'),db.dumps(body.get('source_ids',[])),body.get('artifact_id'),stamp,stamp))
    from .writing_agent.evidence import sync_fact
    sync_fact(pid,fid);documents.fact_changed(pid,fid);return db.one('SELECT * FROM facts WHERE id=?',(fid,))
@app.patch('/api/v1/facts/{fid}')
def patch_fact(fid,body:dict):
    f=db.one('SELECT * FROM facts WHERE id=?',(fid,))
    if not f: raise HTTPException(404,'事实不存在')
    if body.get('artifact_id') and not db.one('SELECT id FROM artifacts WHERE id=? AND project_id=?',(body['artifact_id'],f['project_id'])):raise HTTPException(422,'研究产物不属于当前项目')
    for source_id in body.get('source_ids',[]):
        if source(source_id)['project_id']!=f['project_id']: raise HTTPException(422,'来源不属于项目')
    db.execute('INSERT INTO fact_revisions VALUES(?,?,?,?)',(db.uid(),fid,db.dumps(f),db.now()))
    for key in ('label','statement','status','verification','source_ids','artifact_id'):
        if key in body: db.execute(f'UPDATE facts SET {key}=?,revision=revision+1,updated_at=? WHERE id=?',(db.dumps(body[key]) if key=='source_ids' else body[key],db.now(),fid))
    from .writing_agent.evidence import sync_fact
    sync_fact(f['project_id'],fid);documents.fact_changed(f['project_id'],fid)
    from .knowledge_lineage import changed
    changed(f['project_id'],'fact',fid,'研究事实已修改，复用内容需复查')
    return db.one('SELECT * FROM facts WHERE id=?',(fid,))
@app.get('/api/v1/projects/{pid}/references')
def references(pid): body_project(pid);return db.rows("SELECT * FROM bibliography WHERE project_id=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0",(pid,))
@app.post('/api/v1/projects/{pid}/references')
def add_reference(pid,body:dict):
    from .reference_service import save,get
    row=save(pid,body.get('metadata',body),body.get('file_id'),verification=body.get('verification','unverified'))
    return get(pid,row['id'],False)
@app.patch('/api/v1/references/{rid}')
@db.atomic
def patch_reference(rid,body:dict):
    from .reference_service import update,get
    row=update(rid,body)
    return get(row['project_id'],rid,False)
@app.get('/api/v1/projects/{pid}/mentor-tasks')
def mentor_tasks(pid): body_project(pid);return db.rows('SELECT * FROM mentor_tasks WHERE project_id=?',(pid,))
@app.post('/api/v1/projects/{pid}/mentor-tasks')
def add_mentor(pid,body:dict):
    body_project(pid);sid=body.get('section_id') or None
    if sid and security.require_section(sid)['project_id']!=pid: raise HTTPException(422,'章节不属于项目')
    rid=db.uid();db.execute('INSERT INTO mentor_tasks VALUES(?,?,?,?,?,?,?)',(rid,pid,sid,body['comment'],'open','',db.now()));return {'id':rid}
@app.patch('/api/v1/mentor-tasks/{tid}')
def patch_mentor(tid,body:dict):
    task=db.one('SELECT * FROM mentor_tasks WHERE id=?',(tid,))
    if not task: raise HTTPException(404,'意见不存在')
    db.execute('UPDATE mentor_tasks SET status=?,resolution=? WHERE id=?',(body['status'],body.get('resolution',''),tid))
    from .knowledge_lineage import changed
    changed(task['project_id'],'mentor_task',tid,'修改意见已更新，复用内容需复查')
    return {'ok':True}
@app.get('/api/v1/projects/{pid}/terms')
def terms(pid): body_project(pid);return db.rows('SELECT * FROM terms WHERE project_id=?',(pid,))
@app.post('/api/v1/projects/{pid}/terms')
def add_term(pid,body:dict):
    body_project(pid);zh,en=term_values(body)
    db.execute('INSERT INTO terms VALUES(?,?,?,?) ON CONFLICT(project_id,zh) DO UPDATE SET en=excluded.en',(db.uid(),pid,zh,en));return terms(pid)

def term_values(body):
    zh=str(body.get('zh') or '').strip();en=str(body.get('en') or '').strip()
    if not zh or not en or len(zh)>300 or len(en)>600:raise HTTPException(422,'请填写有效的中英文术语')
    return zh,en

@app.patch('/api/v1/projects/{pid}/terms/{tid}')
def edit_term(pid,tid,body:dict):
    body_project(pid);old=db.one('SELECT * FROM terms WHERE id=? AND project_id=?',(tid,pid))
    if not old:raise HTTPException(404,'术语不存在于当前项目')
    zh,en=term_values(body)
    if db.one('SELECT id FROM terms WHERE project_id=? AND zh=? AND id!=?',(pid,zh,tid)):raise HTTPException(409,'该中文术语已存在，请编辑已有记录')
    db.execute('UPDATE terms SET zh=?,en=? WHERE id=?',(zh,en,tid));db.audit(pid,'term_edited',{'previous':old,'zh':zh,'en':en});return terms(pid)

@app.delete('/api/v1/projects/{pid}/terms/{tid}')
def delete_term(pid,tid):
    body_project(pid);old=db.one('SELECT * FROM terms WHERE id=? AND project_id=?',(tid,pid))
    if not old:raise HTTPException(404,'术语不存在于当前项目')
    db.execute('DELETE FROM terms WHERE id=?',(tid,));db.audit(pid,'term_deleted',old);return {'deleted':True}
@app.get('/api/v1/projects/{pid}/artifacts')
def artifacts(pid): body_project(pid);return db.rows('SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC',(pid,))
@app.post('/api/v1/projects/{pid}/artifacts')
async def add_artifact(pid:str,file:UploadFile=File(...),kind:str=Form('figure'),metadata:str=Form('{}'),egress:str=Form('selected_content')):
    body_project(pid);data=await file.read(MAX_FILE+1)
    if len(data)>MAX_FILE: raise HTTPException(422,'资源过大')
    if egress not in ('local_only','selected_content','allowed'):raise HTTPException(422,'研究产物外发策略无效')
    sha=security.put_blob(data);aid=db.uid();db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,file.filename or '研究资源',kind,sha,db.dumps({**db.loads(metadata,{}),'egress':egress}),db.now()));return {'id':aid,'sha256':sha}
@app.post('/api/v1/projects/{pid}/analysis')
def analysis(pid,body:dict):
    body_project(pid);f=file_record(body['file_id'])
    if f['project_id']!=pid or f['extension'] not in ('.csv','.xlsx'): raise HTTPException(422,'请选择本项目CSV或XLSX数据')
    import pandas as pd
    path=security.safe_object(f['sha256']);frame=pd.read_excel(path,engine='openpyxl') if f['extension']=='.xlsx' else pd.read_csv(path)
    numeric=frame.select_dtypes(include='number');stats=db.loads(numeric.describe().to_json(force_ascii=False),{}) if len(numeric.columns) else {}
    result={'source_file_id':f['id'],'source_hash':f['sha256'],'operation':'descriptive_statistics','rows':len(frame),'columns':list(frame.columns),'missing':frame.isna().sum().to_dict(),'statistics':stats,'actual_execution':True,'timestamp':db.now()};aid=db.uid();sha=security.put_blob(db.dumps(result).encode());db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'描述性统计结果','analysis',sha,db.dumps(result),db.now()));return {'artifact_id':aid,**result}
@app.post('/api/v1/projects/{pid}/reviews')
def review_project(pid): body_project(pid);return tasks.enqueue(pid,'review',{})
@app.get('/api/v1/projects/{pid}/findings')
def get_findings(pid): body_project(pid);return db.rows("SELECT * FROM findings WHERE project_id=? AND status!='superseded' ORDER BY severity,created_at",(pid,))
@app.patch('/api/v1/findings/{fid}')
def resolve_finding(fid,body:dict): db.execute('UPDATE findings SET status=? WHERE id=?',(body['status'],fid));return {'ok':True}
@app.get('/api/v1/projects/{pid}/format')
def format_profile(pid): body_project(pid);return {'config':exports.format_config(pid),'template_sha256':db.setting('template:'+pid),'renderer_available':bool(exports.renderer())}
@app.put('/api/v1/projects/{pid}/format')
def save_format(pid,body:dict):
    body_project(pid);cfg={k:v for k,v in body.items() if k in exports.DEFAULT_FORMAT}
    if cfg.get('line_spacing_points') is None:cfg.pop('line_spacing_points',None)
    for k in ('font_size','line_spacing','line_spacing_points','first_line_chars','margin_top','margin_bottom','margin_left','margin_right'):
        if k in cfg and not 0<=float(cfg[k])<=100: raise HTTPException(422,'排版参数范围无效')
        if k in cfg:cfg[k]=float(cfg[k])
    if 'alignment' in cfg and cfg['alignment'] not in ('left','center','right','justify'):raise HTTPException(422,'段落对齐方式无效')
    for key in ('font','latin_font','heading_font'):
        if key in cfg and (not isinstance(cfg[key],str) or not cfg[key].strip() or len(cfg[key])>100):raise HTTPException(422,'请输入有效的字体名称')
    if 'font_size' in cfg and not 4<=float(cfg['font_size'])<=72:raise HTTPException(422,'正文字号须为4至72磅')
    if 'line_spacing' in cfg and not .5<=float(cfg['line_spacing'])<=4:raise HTTPException(422,'倍数行距须为0.5至4；固定行距请填写磅值')
    from .paper_format import resolve
    effective=resolve(pid,settings=cfg)['config']
    if effective.get('line_spacing_points') and effective['line_spacing_points']<effective['font_size']:raise HTTPException(422,'固定行距不能小于正文字号，以免裁切文字')
    if effective['margin_left']+effective['margin_right']>=195 or effective['margin_top']+effective['margin_bottom']>=280:raise HTTPException(422,'页边距没有为正文保留足够空间')
    db.set_setting('format:'+pid,cfg);return format_profile(pid)
@app.post('/api/v1/projects/{pid}/template')
async def upload_template(pid:str,file:UploadFile=File(...)):
    body_project(pid);data=await file.read(MAX_FILE+1)
    if len(data)>MAX_FILE or not (file.filename or '').lower().endswith('.docx'): raise HTTPException(422,'请上传DOCX模板')
    from docx import Document
    Document(io.BytesIO(data));sha=security.put_blob(data);db.set_setting('template:'+pid,sha);return {'sha256':sha,'verified':False,'message':'已保存；需导出样本并在目标软件验收'}
@app.post('/api/v1/projects/{pid}/exports')
def export(pid,body:dict): body_project(pid);return tasks.enqueue(pid,'export',body)
@app.get('/api/v1/projects/{pid}/exports')
def export_list(pid):
    from .export_service import history
    return history(pid)
@app.get('/api/v1/exports/{eid}/files/{name}')
def export_download(eid,name):
    if not db.one('SELECT id FROM exports WHERE id=?',(eid,)): raise HTTPException(404,'导出不存在')
    folder=(DATA/'exports'/eid).resolve();path=(folder/name).resolve()
    if not path.is_relative_to(folder) or not path.is_file(): raise HTTPException(404,'导出文件不存在')
    return FileResponse(path,filename=path.name)
@app.post('/api/v1/backups')
def backup(body:dict): return tasks.enqueue(body.get('project_id'),'backup',{})
@app.get('/api/v1/backups')
def backups(): return db.rows('SELECT * FROM backups ORDER BY created_at DESC')
@app.get('/api/v1/backups/{bid}/download')
def download_backup(bid):
    b=db.one('SELECT * FROM backups WHERE id=?',(bid,))
    if not b: raise HTTPException(404,'备份不存在')
    return FileResponse(DATA/'backups'/b['filename'],filename='论文工作台备份.zip')
@app.post('/api/v1/backups/restore')
async def restore_backup(file:UploadFile=File(...)): return exports.restore_backup(await file.read(2_000_000_001))
@app.get('/api/v1/tasks')
def task_list(project_id:str|None=None,summary:bool=False,active_only:bool=False):
    fields='id,project_id,kind,status,phase,progress,error,cancel_requested,attempt,created_at,updated_at,json_extract(payload,\'$.acknowledged_at\') AS acknowledged_at,json_extract(payload,\'$.acknowledged_note\') AS acknowledged_note' if summary else '*'
    clauses=[];args=[]
    if project_id:clauses.append('project_id=?');args.append(project_id)
    if summary and active_only:
        unknown_since=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
        recent_since=(datetime.now(timezone.utc)-timedelta(hours=6)).isoformat()
        clauses.append("(status IN ('queued','running') OR (status='outcome_unknown' AND updated_at>=?) OR (status IN ('failed','budget_blocked','incomplete') AND updated_at>=?))")
        args.extend([unknown_since,recent_since])
    where=(' WHERE '+' AND '.join(clauses)) if clauses else ''
    rows=db.rows('SELECT '+fields+' FROM tasks'+where+' ORDER BY created_at DESC LIMIT 100',tuple(args))
    if summary:return [{**t,'error':provider.readable_error(t['error'])} for t in rows]
    from .rule_review_receipts import receipt
    return [{**t,'error':provider.readable_error(t['error']),**({'review_receipt':receipt(t)} if t['kind']=='rules_ai' else {})} for t in rows]
@app.get('/api/v1/tasks/{tid}')
def task(tid):
    t=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    if not t: raise HTTPException(404,'任务不存在')
    if t['kind']=='write_paper':
        from .paper_writing import uncheckpointed_runs
        t['resume_requires_ack']=bool(uncheckpointed_runs(t)) if t['status'] not in ('queued','running','succeeded') else False
    t['payload']=db.loads(t['payload'],{});t['result']=db.loads(t['result']);t['error']=provider.readable_error(t['error'])
    if t['kind']=='rules_ai':
        from .rule_review_receipts import receipt
        t['review_receipt']=receipt(t)
    return t
@app.post('/api/v1/tasks/{tid}/cancel')
def cancel(tid):
    t=task(tid)
    with db.transaction():
        db.execute('UPDATE tasks SET cancel_requested=1,status=CASE WHEN status=\'queued\' THEN \'cancelled\' ELSE status END WHERE id=?',(tid,))
        db.execute("UPDATE tasks SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE project_id=? AND json_extract(payload,'$.parent_task_id')=? AND status IN ('queued','running')",(t['project_id'],tid))
    return task(tid)
@app.post('/api/v1/tasks/{tid}/retry')
def retry(tid,body:dict):
    t=task(tid)
    if t['kind']=='write_paper':
        from .paper_writing import resume
        original=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
        return resume(original,body)
    if t['status'] not in ('failed','budget_blocked','cancelled','outcome_unknown','incomplete'): raise HTTPException(409,'任务当前不可重试')
    paid=db.one("SELECT id FROM generation_runs WHERE task_id=? AND (status IN ('completed','sent','pending','outcome_unknown') OR (?='paper_image' AND status='completed_invalid')) LIMIT 1",(tid,t['kind']))
    if (t['status'] in ('outcome_unknown','incomplete') or paid) and not body.get('acknowledge_possible_duplicate_charge'): raise HTTPException(422,'远端可能已执行；重试须明确知悉可能再次计费')
    payload=dict(t['payload'])
    if (t['kind'].startswith('knowledge_') or t['kind']=='graph_wiki_build') and body.get('acknowledge_possible_duplicate_charge'):
        payload['acknowledge_possible_duplicate_charge']=True
    if t['kind']=='rules_ai':
        payload['resume_task_id']=t['id']
        if body.get('retry_unknown'):payload['retry_unknown']=bool(body.get('acknowledge_possible_duplicate_charge'))
        if body.get('retry_invalid'):payload['retry_invalid']=bool(body.get('acknowledge_possible_duplicate_charge'))
    if 'provider_profile' in body:
        if body['provider_profile'] not in ('primary','gpt'):raise HTTPException(422,'重试接口选择无效')
        if t['kind'] not in ('write','rules_ai','outline_ai','semantic_review','support_check','ocr','material_summary','provider_probe','provider_image_probe'):raise HTTPException(422,'此任务不使用模型接口')
        if body['provider_profile']=='gpt':model_routing.snapshot()
        payload['provider_profile']=body['provider_profile']
    return tasks.enqueue(t['project_id'],t['kind'],payload)
ACKNOWLEDGEABLE=('outcome_unknown','failed','incomplete','budget_blocked','cancelled')
@app.post('/api/v1/tasks/{tid}/acknowledge')
def acknowledge_task(tid,body:dict|None=None):
    body=body or {}
    t=task(tid)
    if t['status'] not in ACKNOWLEDGEABLE: raise HTTPException(409,'只有已结束或结果待核实的任务可以标记核实')
    payload=dict(t['payload'])
    note=str(body.get('note') or '').strip()
    # 核实只记录人工确认痕迹，不改写任务结果，也不改变失败原因，
    # 便于之后仍然按原状态重试；只有远端状态未知的任务沿用旧的关闭语义。
    payload['acknowledged_at']=db.now()
    payload['acknowledged_status']=t['status']
    if note: payload['acknowledged_note']=note
    closed='acknowledged' if t['status']=='outcome_unknown' else t['status']
    db.execute('UPDATE tasks SET status=?,payload=?,updated_at=? WHERE id=?',(closed,db.dumps(payload),db.now(),tid))
    db.audit(t['project_id'],'task_acknowledged',{'task_id':tid,'previous_status':t['status'],'note':note,'generation_runs':db.rows('SELECT id,status FROM generation_runs WHERE task_id=?',(tid,))})
    return task(tid)
@app.get('/api/v1/tasks/{tid}/events')
async def task_events(tid:str,after:int=0):
    task(tid)
    async def stream():
        seq=after
        for _ in range(300):
            events=db.rows('SELECT * FROM task_events WHERE task_id=? AND id>? ORDER BY id',(tid,seq))
            for e in events:
                seq=e['id'];yield f"id: {seq}\nevent: progress\ndata: {e['payload']}\n\n"
            state=db.one('SELECT status FROM tasks WHERE id=?',(tid,))['status']
            if state not in ('queued','running'): yield 'event: done\ndata: '+db.dumps({'status':state})+'\n\n';break
            if not events: yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})
@app.get('/api/v1/settings')
@provider.settings_atomic
def settings():
    cfg=provider.config();caps=db.setting('capabilities',{})
    if caps.get('base_url')!=cfg['base_url'] or caps.get('protocol')!=cfg['protocol']:caps={}
    elif caps.get('model') and caps['model']!=cfg['model']:caps={**caps,'generation':'not_tested'}
    return {'provider':cfg,'credential':security.key_status(),'capabilities':caps,'version':APP_VERSION,'data_directory':str(DATA),'renderer_available':bool(exports.renderer()),'account_usage':db.setting('account_usage')}
@app.put('/api/v1/settings')
@provider.settings_atomic
def save_settings(body:dict):
    if deepseek_profiles.enabled():
        deepseek_profiles.save_active(body)
        return settings()
    cfg={**provider.config(),**body.get('provider',{})};url=cfg['base_url'].rstrip('/');parsed=__import__('urllib.parse',fromlist=['urlparse']).urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password: raise HTTPException(422,'中转站需使用不含凭据的HTTPS地址')
    if cfg['protocol'] not in ('openai','responses'): raise HTTPException(422,'协议无效，请使用 Chat Completions 或 Responses')
    if not isinstance(cfg['model'],str) or 'gemini' in cfg['model'].lower():raise HTTPException(422,'主模型请使用 DeepSeek，GPT 请配置到备选接口')
    if not isinstance(cfg.get('supports_images',False),bool):raise HTTPException(422,'图片能力开关格式无效')
    if not 128<=int(cfg['max_output_tokens'])<=1000000: raise HTTPException(422,'输出上限超出范围')
    for key in ('input_price','output_price'):
        if cfg.get(key) is not None and (not isinstance(cfg[key],(int,float)) or not __import__('math').isfinite(float(cfg[key])) or float(cfg[key])<0):raise HTTPException(422,'Token单价须为非负有限数值')
    if not 15<=float(cfg['timeout_seconds'])<=3600:raise HTTPException(422,'请求超时应在15至3600秒之间')
    if not 0<float(cfg['max_request_cost'])<=1000000:raise HTTPException(422,'单次预留费用必须为正数')
    if not 512<=int(cfg['context_limit'])<=2000000:raise HTTPException(422,'应用上下文预算范围无效')
    if type(cfg.get('max_attempts',5)) is not int or not 1<=cfg.get('max_attempts',5)<=5:raise HTTPException(422,'最多尝试次数（含首次）应为1至5的整数')
    cfg['base_url']=url;cfg={k:v for k,v in cfg.items() if k in provider.DEFAULT}
    if 'api_key' in body:
        if not isinstance(body['api_key'],str):raise HTTPException(422,'API key必须是单行文字')
        security.save_key(body['api_key'].strip())
    previous_cfg=provider.config();previous_url=previous_cfg['base_url']
    db.set_setting('provider',cfg)
    if 'api_key' in body or previous_url!=cfg['base_url']:db.set_setting('account_usage',None)
    if 'api_key' in body or previous_url!=cfg['base_url'] or previous_cfg['protocol']!=cfg['protocol']:db.set_setting('capabilities',{})
    elif previous_cfg['model']!=cfg['model']:
        caps=db.setting('capabilities',{});caps.update({'generation':'not_tested','streaming_generation':'not_tested','model':cfg['model']});db.set_setting('capabilities',caps)
    return settings()
@app.post('/api/v1/provider/models')
def provider_models(): return {'models':provider.models()}
@app.get('/api/v1/gpt-fallback/settings')
def gpt_settings():return model_routing.settings()
@app.put('/api/v1/gpt-fallback/settings')
def save_gpt_settings(body:dict):return model_routing.save_settings(body)
@app.post('/api/v1/gpt-fallback/models')
def gpt_models():return model_routing.models()
@app.post('/api/v1/gpt-fallback/probe')
def gpt_probe(body:dict):
    pid=body.get('project_id');security.require_project(pid);model_routing.snapshot()
    return tasks.enqueue(pid,'provider_probe',{'provider_profile':'gpt'})
@app.post('/api/v1/provider/probe-image')
def provider_image_probe(body:dict):
    pid=body.get('project_id');security.require_project(pid)
    profile=body.get('provider_profile','primary')
    if profile not in ('primary','gpt'):raise HTTPException(422,'图片测试接口选择无效')
    if profile=='gpt':model_routing.snapshot()
    return tasks.enqueue(pid,'provider_image_probe',{'provider_profile':profile})
@app.post('/api/v1/provider/account-usage')
def get_account_usage():return provider.account_usage()
@app.get('/api/v1/provider/account-usage/history')
def account_usage_history():return db.setting('account_usage_history',[])
@app.get('/api/v1/deepseek-pool/settings')
def pool_settings():return deepseek_pool.settings()
@app.put('/api/v1/deepseek-pool/settings')
def save_pool_settings(body:dict):return deepseek_pool.save_settings(body)
@app.post('/api/v1/deepseek-pool/endpoints/{endpoint_id}/key')
def save_pool_key(endpoint_id:str,body:dict):
    if not isinstance(body,dict) or 'api_key' not in body:raise HTTPException(422,'请求须包含 api_key')
    return deepseek_pool.save_endpoint_key(endpoint_id,body['api_key'])
@app.get('/api/v1/projects/{pid}/usage')
def usage(pid):
    body_project(pid)
    from .expense_evidence import ledger
    evidence={r['id']:r for r in ledger(pid)['runs']}
    return [{**r,'expense_basis':evidence[r['id']]} for r in db.rows('SELECT * FROM generation_runs WHERE project_id=? ORDER BY created_at DESC',(pid,))]
@app.post('/api/v1/usage/{rid}/settle')
def settle_usage(rid,body:dict):
    raise HTTPException(422,'不能仅凭手填金额结算；请通过费用依据账本导入可匹配该请求的服务商JSON凭证')
@app.get('/api/v1/projects/{pid}/audit')
def audit(pid): body_project(pid);return db.rows('SELECT * FROM audit WHERE project_id=? ORDER BY id DESC LIMIT 100',(pid,))
@app.post('/api/v1/projects/{pid}/outline/ai')
def ai_outline(pid): body_project(pid);return tasks.enqueue(pid,'outline_ai',{})

mount_api_routers(app,supplements=True)

frontend=Path(os.environ.get('PAPER_FRONTEND_DIR',str(ROOT/'frontend'/'dist'))).resolve()
if frontend.exists():
    app.mount('/assets',StaticFiles(directory=frontend/'assets',check_dir=False),name='assets')
    app.mount('/mathlive-fonts',StaticFiles(directory=frontend/'mathlive-fonts',check_dir=False),name='mathlive-fonts')
    @app.get('/{path:path}')
    def spa(path:str):
        if path=='api' or path.startswith('api/'):raise HTTPException(404,'接口不存在')
        return FileResponse(frontend/'index.html')

install_api_v2(app)
