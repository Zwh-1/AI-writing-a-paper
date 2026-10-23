"""Local advanced capability API. Remote collaboration uses its own isolated app."""
import importlib.util,os,hashlib,io,json,subprocess,tempfile,urllib.parse
from pathlib import Path
from fastapi import APIRouter,HTTPException
from fastapi.responses import FileResponse
from . import db,security,tasks,parsers,retrieval,controlled_execution as controlled,slide_layout,ooxml_preserve,synchronization
from .config import ROOT
router=APIRouter()

def project(pid):return security.require_project(pid)
def record(pid,kind,parameters,result):
    rid=db.uid();db.execute('INSERT INTO advanced_runs VALUES(?,?,?,?,?,?)',(rid,pid,kind,db.dumps(parameters),db.dumps(result),db.now()));return {'run_id':rid,**result}

def execute_task(task):
    pid=task['project_id'];body=db.loads(task['payload'],{});kind=task['kind'];project(pid)
    def progress(done,total):
        current=db.one('SELECT cancel_requested FROM tasks WHERE id=?',(task['id'],))
        if tasks.STOP.is_set() or current and current['cancel_requested']:raise InterruptedError('已停止任务，已完成的索引批次仍保留')
        tasks.event(task['id'],f'已处理 {done}/{total}',10+int(75*done/max(total,1)))
    tasks.event(task['id'],'准备高级任务与项目来源',10)
    if kind=='advanced_index':result=retrieval.build_index(pid,progress)
    elif kind=='advanced_benchmark':result=retrieval.evaluate(pid,body.get('cases',[]),progress=progress)
    elif kind=='advanced_statistics':
        from .advanced_statistics import analyze
        result=analyze(pid,body)
    elif kind=='advanced_screenshot':result=controlled.capture(pid,body)
    elif kind=='advanced_execute':result=controlled.execute(pid,body)
    elif kind=='advanced_model_download':
        from .model_install import download
        result=download(body['model'])
    elif kind=='advanced_office':
        from .office_renderer import render_file
        result=render_file(file_record(pid,body['file_id']))
    elif kind=='advanced_http_test':result=controlled.http_test(pid,body)
    elif kind=='advanced_sandbox_install':result=controlled.install_sandbox()
    elif kind=='advanced_objects':
        from .ppt_objects import extract
        result=extract(file_record(pid,body['file_id']))
    elif kind=='advanced_tables':result={'candidates':slide_layout.continuation_candidates(file_record(pid,body['file_id']))}
    else:raise ValueError('高级任务类型无效')
    parameters={k:v for k,v in body.items() if k not in ('code','password','api_key','sync_password','cloud_password')};return record(pid,kind,parameters,result)

def file_record(pid,fid):
    f=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(fid,pid))
    if not f:raise HTTPException(404,'本项目未找到文件')
    return f

@router.get('/advanced/capabilities')
def capabilities():return {'neural_library':bool(importlib.util.find_spec('fastembed')),'statistics_library':bool(importlib.util.find_spec('scipy')),'execution':controlled.capabilities(),'office_renderer':bool(__import__('app.exports',fromlist=['renderer']).renderer()),'collaboration_server':'separate_opt_in_gateway','cloud_transport':'encrypted_resource_complete_WebDAV_ETag','streaming':'upstream_SSE_only','docx_preservation':'untouched_parts_byte_identical'}
@router.get('/projects/{pid}/advanced')
def configuration(pid):project(pid);return {'retrieval':retrieval.config(pid),'capture_origins':db.setting('capture_origins:'+pid,[]),'sharing':db.setting('sharing:'+pid,False),'remote_ai_global_budget':db.setting('remote_ai_global_budget',0),'streaming':__import__('app.provider',fromlist=['config']).config().get('streaming',False)}
@router.put('/projects/{pid}/advanced')
def save_configuration(pid,body:dict):
    with db.transaction():return _save_configuration(pid,body)

def _save_configuration(pid,body):
    project(pid)
    if 'retrieval' in body:
        cfg={**retrieval.config(pid),**body['retrieval']}
        if cfg.get('rerank') not in ('fusion','cosine','cross_encoder') or not 10<=int(cfg['candidate_k'])<=100:raise HTTPException(422,'重排策略或召回池无效')
        from .model_install import SPECS
        if not isinstance(cfg['enabled'],bool) or cfg['model'] not in SPECS or cfg['cross_encoder'] not in SPECS:raise HTTPException(422,'模型或启用标记无效')
        cfg={k:cfg[k] for k in ('enabled','model','candidate_k','rerank','cross_encoder')};db.set_setting('retrieval:'+pid,cfg)
    if 'capture_origins' in body:
        origins=body['capture_origins']
        if not isinstance(origins,list) or len(origins)>10:raise HTTPException(422,'最多登记10个本机系统地址')
        for origin in origins:
            u=urllib.parse.urlparse(origin)
            if u.scheme not in ('http','https') or not u.hostname or (u.scheme=='http' and u.hostname not in ('localhost','127.0.0.1')) or u.username or u.password or u.path not in ('','/') or (u.hostname in ('localhost','127.0.0.1') and u.port==int(os.environ.get('PAPER_PORT','8765'))):raise HTTPException(422,'请填写本机系统的协议、主机和端口，不包含路径或凭据')
        db.set_setting('capture_origins:'+pid,[controlled.canonical_origin(x) for x in origins])
    if 'sharing' in body:
        if not isinstance(body['sharing'],bool):raise HTTPException(422,'共享开关须为布尔值')
        db.set_setting('sharing:'+pid,body['sharing'])
    if 'remote_ai_global_budget' in body:
        import math
        value=float(body['remote_ai_global_budget'])
        if not math.isfinite(value) or not 0<=value<=1000000:raise HTTPException(422,'协作预算无效')
        db.set_setting('remote_ai_global_budget',value)
    if 'streaming' in body:
        from . import provider
        if not isinstance(body['streaming'],bool):raise HTTPException(422,'流式开关须为布尔值')
        db.set_setting('provider',{**provider.config(),'streaming':body['streaming']})
    db.audit(pid,'advanced_configuration',{'keys':list(body)});return configuration(pid)
@router.post('/projects/{pid}/retrieval/index')
def index(pid):project(pid);return tasks.enqueue(pid,'advanced_index',{})
@router.post('/projects/{pid}/retrieval/hybrid')
def hybrid(pid,body:dict):
    project(pid);query=str(body.get('query','')).strip()
    if not query or len(query)>5000:raise HTTPException(422,'请输入1至5000字的检索问题')
    return retrieval.hybrid(pid,query,body.get('kind','all'),min(30,max(1,int(body.get('limit',10)))),force=True)
@router.post('/projects/{pid}/retrieval/benchmark')
def benchmark(pid,body:dict):
    project(pid)
    if not isinstance(body.get('cases'),list) or not 1<=len(body['cases'])<=200:raise HTTPException(422,'需要1至200条评测样本')
    return tasks.enqueue(pid,'advanced_benchmark',{'cases':body['cases']})
@router.get('/projects/{pid}/advanced/runs')
def runs(pid):project(pid);return db.rows('SELECT * FROM advanced_runs WHERE project_id=? ORDER BY created_at DESC LIMIT 50',(pid,))
@router.post('/projects/{pid}/advanced/{kind}')
def advanced_task(pid,kind:str,body:dict):
    project(pid)
    if kind not in ('statistics','screenshot','execute','tables','model_download','office','http_test','sandbox_install','objects'):raise HTTPException(404,'高级操作不存在')
    if kind=='execute' and not controlled.capabilities()['docker_engine']:raise HTTPException(409,'Docker不可用，拒绝在宿主机直接执行用户代码')
    if kind in ('screenshot','http_test'):controlled.allowed_target(pid,str(body.get('url','')))
    return tasks.enqueue(pid,'advanced_'+kind,body)
@router.get('/pages/{page_id}/layout')
def layout(page_id):
    p=db.one('SELECT p.*,f.project_id FROM pages p JOIN files f ON f.id=p.file_id WHERE p.id=?',(page_id,))
    if not p:raise HTTPException(404,'页面不存在')
    project(p['project_id']);f=file_record(p['project_id'],p['file_id'])
    if f['extension']!='.pptx':raise HTTPException(422,'整页布局接口需要PPTX')
    return slide_layout.native_svg(f,p['page_number'])
@router.get('/projects/{pid}/docx/{fid}/inspect')
def inspect(pid,fid):
    project(pid);f=file_record(pid,fid)
    if f['extension']!='.docx':raise HTTPException(422,'需要DOCX文件')
    return ooxml_preserve.inspect_docx(security.safe_object(f['sha256']).read_bytes())
@router.post('/projects/{pid}/docx/{fid}/patch')
def patch(pid,fid,body:dict):
    project(pid);f=file_record(pid,fid)
    if f['extension']!='.docx':raise HTTPException(422,'需要DOCX文件')
    output,receipt=ooxml_preserve.patch_docx(security.safe_object(f['sha256']).read_bytes(),body.get('package_sha256'),body.get('patches'),body.get('formula_patches'));new,_=parsers.register_file(pid,Path(f['name']).stem+'-保真修订.docx',output,'draft',f['egress']);security.link_derivation(new['id'],fid,'docx_preserving_patch');tasks.enqueue(pid,'parse',{'file_id':new['id']});return record(pid,'docx_preserve',{'file_id':fid},{'file_id':new['id'],**receipt})
@router.get('/projects/{pid}/advanced/files/{fid}')
def file_download(pid,fid):project(pid);f=file_record(pid,fid);return FileResponse(security.safe_object(f['sha256']),filename=f['name'])
@router.get('/tasks/{tid}/stream')
def stream(tid,after:int=0):
    task=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    if not task:raise HTTPException(404,'任务不存在')
    project(task['project_id']);row=db.one('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1',(tid,))
    if not row:return {'chunks':[],'status':task['status'],'run_id':None}
    return {'run_id':row['id'],'status':row['status'],'chunks':db.rows('SELECT seq,text,kind,created_at FROM stream_chunks WHERE run_id=? AND seq>? ORDER BY seq LIMIT 500',(row['id'],after)),'requires_manual_recovery':row['status']!='completed'}
@router.post('/projects/{pid}/sync/operation')
def operation(pid,body:dict):project(pid);result=synchronization.apply_operation(pid,'local',body);return __import__('fastapi.responses',fromlist=['JSONResponse']).JSONResponse(result,status_code=result['status_code'])
@router.get('/projects/{pid}/sync/conflicts')
def conflicts(pid):project(pid);return db.rows("SELECT * FROM sync_conflicts WHERE project_id=? AND state='open'",(pid,))
@router.post('/projects/{pid}/sync/conflicts/{cid}')
def resolve(pid,cid,body:dict):project(pid);return synchronization.resolve_conflict(pid,'local',cid,body)
@router.post('/projects/{pid}/sync/{direction}')
def cloud(pid,direction,body:dict):
    project(pid)
    if direction=='export':return sync_export(pid,body)
    if direction=='apply':
        from .sync_bundle import apply
        packet=synchronization.decrypt(security.safe_object(body['encrypted_blob']).read_bytes(),body['sync_password'])
        if packet['project_id']!=pid:raise HTTPException(422,'同步包不属于当前项目')
        return apply(packet,'local')
    if direction not in ('push','pull'):raise HTTPException(404,'同步方向无效')
    result=synchronization.cloud(pid,body,pull=direction=='pull');db.audit(pid,'cloud_'+direction,{'etag':result.get('etag')});return result


@router.get('/advanced/models')
def models_status():
    from .model_install import SPECS,status
    return [status(name) for name in SPECS]

@router.get('/pages/{page_id}/office-preview')
def office_preview(page_id):
    from .office_renderer import page_image
    row=db.one('SELECT p.*,f.project_id FROM pages p JOIN files f ON f.id=p.file_id WHERE p.id=?',(page_id,))
    if not row:raise HTTPException(404,'页面不存在')
    project(row['project_id'])
    try:path=page_image(file_record(row['project_id'],row['file_id']),row['page_number'])
    except FileNotFoundError as e:raise HTTPException(404,str(e))
    return FileResponse(path,media_type='image/png')

@router.post('/projects/{pid}/sections/{sid}/formula')
def insert_formula(pid,sid,body:dict):
    from . import documents
    from .math_office import convert
    project(pid);section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(422,'章节不属于此项目')
    expression=body.get('latex','');math=convert(expression);old=documents.current(section);doc=db.loads(old['document']) if old else {'type':'doc','content':[]}
    doc['content'].append({'type':'formula','attrs':{'latex':expression,'expression':expression,'omml':math}})
    rev=documents.save(section,doc,section['current_revision_id']);return {'revision_id':rev['id'],'native_omml':True,'latex':expression}

@router.post('/projects/{pid}/sections/{sid}/footnote')
def insert_footnote(pid,sid,body:dict):
    from . import documents
    project(pid);section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(422,'章节不属于此项目')
    text=str(body.get('text','')).strip()
    if not 1<=len(text)<=10000:raise HTTPException(422,'脚注长度无效')
    old=documents.current(section);doc=db.loads(old['document']) if old else {'type':'doc','content':[]}
    paragraph=next((n for n in reversed(doc.get('content',[])) if n.get('type') in ('paragraph','heading')),None)
    if paragraph is None:paragraph={'type':'paragraph','content':[]};doc.setdefault('content',[]).append(paragraph)
    paragraph.setdefault('content',[]).append({'type':'footnote','attrs':{'noteText':text}});rev=documents.save(section,doc,section['current_revision_id']);return {'revision_id':rev['id'],'footnote_inserted':True}

@router.get('/streams/{rid}/text')
def stream_download(rid):
    from fastapi.responses import Response
    row=db.one('SELECT * FROM generation_runs WHERE id=?',(rid,))
    if not row:raise HTTPException(404,'生成记录不存在')
    project(row['project_id']);text=''.join(r['text'] for r in db.rows('SELECT text FROM stream_chunks WHERE run_id=? ORDER BY seq',(rid,)))
    return Response(text,media_type='text/plain',headers={'Content-Disposition':'attachment; filename="recovered-stream.txt"'})

@router.post('/tasks/{tid}/recover-stream')
def recover_stream(tid):
    task=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    if not task:raise HTTPException(404,'任务不存在')
    project(task['project_id']);run=db.one('SELECT * FROM generation_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1',(tid,))
    if not run:raise HTTPException(422,'尚无生成记录')
    rows=db.rows('SELECT text FROM stream_chunks WHERE run_id=? ORDER BY seq',(run['id'],));text=''.join(r['text'] for r in rows)
    if not text:raise HTTPException(422,'没有已到达的文本片段')
    f,new=parsers.register_file(task['project_id'],'恢复片段-'+run['id']+'.txt',text.encode(),'draft','local_only')
    if new:tasks.enqueue(task['project_id'],'parse',{'file_id':f['id']})
    return {'file_id':f['id'],'chunks':len(rows),'network_requests':0,'automatically_accepted':False,'egress':'local_only'}

@router.post('/projects/{pid}/sync/export')
def sync_export(pid,body:dict):
    from .sync_bundle import make
    project(pid);raw=synchronization.encrypt(make(pid,db.setting('sync_base:'+pid,{})),body['sync_password']);sha=security.put_blob(raw);db.set_setting('sync_export:'+pid,list(dict.fromkeys(db.setting('sync_export:'+pid,[])+[sha]))[-30:]);return {'encrypted_blob':sha,'bytes':len(raw),'download':'/api/v1/projects/'+pid+'/sync/blob/'+sha}

@router.get('/projects/{pid}/sync/blob/{sha}')
def sync_download(pid,sha):
    project(pid);path=security.safe_object(sha)
    if not path.exists():raise HTTPException(404,'同步包不存在')
    # Only a successful export receipt for this project can authorize a download.
    allowed=db.setting('sync_export:'+pid,[])
    if sha not in allowed:raise HTTPException(404,'同步包不属于此项目')
    return FileResponse(path,media_type='application/octet-stream',filename='project-sync.papersync')

@router.post('/advanced/sync/import')
def sync_import(body:dict):
    from .sync_bundle import apply
    import base64
    raw=base64.b64decode(body['data'],validate=True);packet=synchronization.decrypt(raw,body['sync_password']);return apply(packet,'local',allow_new=True)

@router.post('/advanced/collaboration/bootstrap')
def collaboration_bootstrap(body:dict):
    from .collaboration import create_user
    with db.transaction():
        if db.one('SELECT id FROM collaboration_users LIMIT 1'):raise HTTPException(409,'协作账号已存在，请在协作服务使用现有账号管理')
        return create_user(str(body.get('username','')).strip(),body.get('password',''),True)

@router.post('/advanced/collaboration/start')
def collaboration_start(body:dict):
    from .service_control import start_collaboration
    return start_collaboration(body.get("port",8774))


@router.post('/projects/{pid}/artifacts/{aid}/egress')
def artifact_egress(pid,aid,body:dict):
    project(pid);row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
    if not row:raise HTTPException(404,'产物不属于当前项目')
    value=body.get('egress');note=str(body.get('note','')).strip()
    if value not in ('local_only','selected_content','allowed') or not note:raise HTTPException(422,'请选择外发范围并填写核对说明')
    meta=db.loads(row['metadata'],{});meta['egress']=value;db.execute('UPDATE artifacts SET metadata=? WHERE id=?',(db.dumps(meta),aid));db.audit(pid,'artifact_egress',{'artifact_id':aid,'egress':value,'note':note});return {'artifact_id':aid,'egress':value,'research_fact_verification':'unchanged'}


@router.get('/projects/{pid}/sync/record-conflicts')
def record_conflicts(pid):
    from .sync_bundle import record_hash
    project(pid);rows=db.rows("SELECT * FROM sync_record_conflicts WHERE project_id=? AND state='open'",(pid,))
    for row in rows:
        key=db.loads(row['key_json']);current=db.one('SELECT * FROM '+row['table_name']+' WHERE '+' AND '.join(k+'=?' for k in key),tuple(key.values()));row['current_hash']=record_hash(row['table_name'],current) if current else None
    return rows

@router.post('/projects/{pid}/sync/record-conflicts/{cid}')
def resolve_record_conflict(pid,cid,body:dict):
    from .sync_bundle import resolve_record
    project(pid);return resolve_record(pid,cid,body)


@router.post('/projects/{pid}/capture/profile')
def capture_profile(pid,body:dict):
    project(pid);return controlled.store_capture_profile(pid,body.get('storage_state'))

@router.delete('/projects/{pid}/capture/profile')
def remove_capture_profile(pid):
    project(pid);path=security.DATA/'private'/('capture-'+pid+'.dpapi');path.unlink(missing_ok=True);return {'deleted':True}


@router.post('/projects/{pid}/continued-tables/{run_id}/{number}/confirm')
def confirm_table(pid,run_id,number:int,body:dict):
    import csv,re
    project(pid);run=db.one("SELECT * FROM advanced_runs WHERE id=? AND project_id=? AND kind='advanced_tables'",(run_id,pid))
    if not run:raise HTTPException(404,'续表记录不存在')
    candidates=db.loads(run['result'],{}).get('candidates',[])
    if not 0<=number<len(candidates):raise HTTPException(404,'续表候选不存在')
    if not body.get('acknowledge') or not str(body.get('note','')).strip():raise HTTPException(422,'需要逐项核对后的明确确认与说明')
    rows=body.get('rows')
    if not isinstance(rows,list) or not 1<=len(rows)<=5000 or any(not isinstance(r,list) or len(r)>100 or any(not isinstance(v,str) or len(v)>10000 for v in r) for r in rows):raise HTTPException(422,'表格行列或单元格大小无效')
    candidate=candidates[number];f=file_record(pid,db.loads(run['parameters'])['file_id']);pages=[candidate['from_page'],candidate['to_page']];page=db.one('SELECT id FROM pages WHERE file_id=? AND page_number=?',(f['id'],pages[0]))
    if not page:raise HTTPException(422,'原始页已变化，请重新整理续表')
    plain=io.StringIO();writer=csv.writer(plain);writer.writerows(rows);text=plain.getvalue();sid=db.uid();aid=db.uid();safe=io.StringIO();writer=csv.writer(safe);escaped=0
    for row in rows:
        values=[]
        for value in row:
            if value.lstrip().startswith(('=','+','-','@')) and not re.fullmatch(r'[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?',value.strip()):value="'"+value;escaped+=1
            values.append(value)
        writer.writerow(values)
    sha=security.put_blob(safe.getvalue().encode('utf-8-sig'));kind='rule_candidate' if f['role']=='rules' else 'example' if f['role']=='example' else 'evidence'
    from .parsers import tokens
    with db.transaction() as c:
        c.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,f['id'],page['id'],db.dumps({'kind':'continued_table','pages':pages,'run_id':run_id}),text,text,kind,'manual_table',1));c.execute('INSERT INTO source_fts VALUES(?,?)',(sid,tokens(text)))
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'已确认续表.csv','continued_table',sha,db.dumps({'source_file_id':f['id'],'source_pages':pages,'source_id':sid,'egress':f['egress'],'review_note':body['note'],'review_method':'explicit_user_declaration','formula_cells_escaped':escaped}),db.now()))
        db.audit(pid,'continued_table_confirmed',{'artifact_id':aid,'source_id':sid,'pages':pages,'note':body['note']})
    return {'artifact_id':aid,'source_id':sid,'pages':pages,'page_review_states_unchanged':True,'csv_download':'/api/v1/artifacts/'+aid+'/content'}


@router.get('/artifacts/{aid}/content')
def artifact_content(aid):
    row=db.one('SELECT * FROM artifacts WHERE id=?',(aid,))
    if not row:raise HTTPException(404,'产物不存在')
    project(row['project_id']);path=security.safe_object(row['sha256'])
    if row['kind']=='screenshot':return FileResponse(path,media_type='image/png')
    return FileResponse(path,filename=row['name'])


@router.post('/projects/{pid}/sync/conflicts/{cid}/restore')
def restore_conflict(pid,cid,body:dict):project(pid);return synchronization.restore_branch(pid,'local',cid,body)


@router.post('/advanced/collaboration/stop')
def collaboration_stop():
    from .service_control import stop_collaboration
    return stop_collaboration()


@router.post('/advanced/math/convert')
def convert_formula(body:dict):
    from .math_office import convert
    return {'omml':convert(body.get('latex','')),'format':'Office Math'}
