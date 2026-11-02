"""Opt-in standalone collaboration gateway. It never exposes local model keys or legacy APIs."""
import asyncio,hashlib,hmac,json,os,secrets,time,threading
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Request,WebSocket,WebSocketDisconnect
from fastapi.responses import JSONResponse,FileResponse
from . import db,security,documents,synchronization as sync
from .config import ROOT
_LOCK=threading.RLock();_FAILURES={}
def password_hash(password,salt=None):
    if not isinstance(password,str) or not 12<=len(password)<=1024:raise ValueError('协作密码须为12至1024字符')
    salt=salt or secrets.token_hex(16);digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex();return salt+':'+digest

def create_user(username,password,admin=False):
    if not username or len(username)>80:raise ValueError('用户名无效')
    uid=db.uid();db.execute('INSERT INTO collaboration_users VALUES(?,?,?,?,0)',(uid,username,password_hash(password),int(admin)));return {'id':uid,'username':username,'admin':bool(admin)}

def offline_key(uid):
    import base64
    with db.transaction():
        master=db.setting('collaboration_offline_master')
        if not master:master=secrets.token_hex(32);db.set_setting('collaboration_offline_master',master)
    return base64.b64encode(hmac.new(bytes.fromhex(master),uid.encode(),hashlib.sha256).digest()).decode()

def principal(connection):
    token=connection.cookies.get('paper_collaboration','');row=db.one('SELECT u.*,s.csrf,s.expires_at FROM collaboration_sessions s JOIN collaboration_users u ON u.id=s.user_id WHERE s.token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    if not row or row['disabled'] or row['expires_at']<time.time():raise HTTPException(401,'请登录协作服务')
    return row

def membership(pid,user,edit=False,owner=False):
    member=db.one('SELECT role FROM project_members WHERE project_id=? AND user_id=?',(pid,user['id']));role='owner' if user['admin'] else member['role'] if member else None
    if role is None:raise HTTPException(404,'项目不存在或没有访问权限')
    sync.sharing_gate(pid)
    if owner and role!='owner' or edit and role not in ('owner','editor'):raise HTTPException(403,'当前角色不能执行此操作')
    return role

@asynccontextmanager
async def lifespan(app):
    if os.environ.get('PAPER_COLLAB_INSECURE')=='1' and os.environ.get('PAPER_COLLAB_BIND','127.0.0.1') not in ('127.0.0.1','localhost','::1'):raise RuntimeError('HTTP开发会话只能绑定回环地址；远程部署须使用HTTPS安全Cookie')
    db.init()
    if db.setting('remote_ai_global_budget') is None:
        value=float(os.environ.get('PAPER_REMOTE_AI_BUDGET','0'))
        if not __import__('math').isfinite(value) or value<0:raise RuntimeError('协作AI预算无效')
        db.set_setting('remote_ai_global_budget',value)
    yield

class BodyLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        cap=16384 if scope.get('path','').endswith('/login') else 300*1024*1024 if scope.get('path')=='/collab/v1/import' else 6*1024*1024
        used=0
        async def limited():
            nonlocal used
            message=await receive();used+=len(message.get('body',b''))
            if used>cap:raise HTTPException(413,'请求体超出当前操作限制')
            return message
        await self.app(scope,limited,send)

app=FastAPI(title='论文协作网关',lifespan=lifespan)
app.add_middleware(BodyLimit)
@app.middleware('http')
async def boundaries(request,call_next):
    allowed_hosts=os.environ.get('PAPER_COLLAB_HOSTS','localhost,127.0.0.1,testserver').split(',');host=request.url.hostname
    if host not in allowed_hosts:return JSONResponse({'detail':'Host未登记'},403)
    length=request.headers.get('content-length')
    if length and (not length.isdigit() or int(length)>(16384 if request.url.path.endswith('/login') else 300*1024*1024)):return JSONResponse({'detail':'请求体超出限制'},413)
    origin=request.headers.get('origin');allowed_origins=os.environ.get('PAPER_COLLAB_ORIGINS','http://testserver,https://testserver,http://127.0.0.1:8774').split(',')
    if origin and origin not in allowed_origins:return JSONResponse({'detail':'Origin未登记'},403)
    if request.url.path.startswith('/collab/v1/') and request.url.path not in ('/collab/v1/login','/health'):
        try:user=principal(request)
        except HTTPException as error:return JSONResponse({'detail':error.detail},error.status_code)
        if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token',''),user['csrf']):return JSONResponse({'detail':'CSRF校验失败'},403)
    response=await call_next(request);response.headers['X-Content-Type-Options']='nosniff';response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'";response.headers['Cache-Control']='no-store';return response

@app.get('/')
def page():
    bundled=ROOT/'frontend/collaboration.html'
    return FileResponse(bundled if bundled.is_file() else ROOT/'frontend/src/collaboration.html')
@app.post('/collab/v1/login')
def login(request:Request,body:dict):
    username=body.get('username','');password=body.get('password','');key=(request.client.host,username);now=time.time()
    with _LOCK:
        attempts=[t for t in _FAILURES.get(key,[]) if now-t<300]
        if len(attempts)>=6:raise HTTPException(429,'登录失败次数过多，请稍后重试')
    ip_key=(request.client.host,'*')
    with _LOCK:
        ip_attempts=[t for t in _FAILURES.get(ip_key,[]) if now-t<300]
        if len(ip_attempts)>=60:raise HTTPException(429,'当前来源登录失败过多，请稍后重试')
    user=db.one('SELECT * FROM collaboration_users WHERE username=?',(username,))
    try:
        calculated=password_hash(password,user['password_hash'].split(':')[0] if user else '0'*32)
        valid=user and not user['disabled'] and hmac.compare_digest(user['password_hash'],calculated)
    except Exception:valid=False
    if not valid:
        with _LOCK:
            if len(_FAILURES)>10000:
                for old in sorted(_FAILURES,key=lambda k:_FAILURES[k][-1] if _FAILURES[k] else 0)[:1000]:_FAILURES.pop(old,None)
            _FAILURES[key]=attempts+[now];_FAILURES[ip_key]=ip_attempts+[now]
        raise HTTPException(401,'用户名或密码错误')
    with _LOCK:_FAILURES.pop(key,None)
    token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32);db.execute('INSERT INTO collaboration_sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],csrf,now+8*3600));response=JSONResponse({'csrf':csrf,'offline_key':offline_key(user['id']),'user':{'id':user['id'],'username':user['username'],'admin':bool(user['admin'])}});response.set_cookie('paper_collaboration',token,httponly=True,secure=os.environ.get('PAPER_COLLAB_INSECURE')!='1',samesite='strict',max_age=8*3600);return response
@app.get('/collab/v1/session')
def session(request:Request):u=principal(request);return {'csrf':u['csrf'],'offline_key':offline_key(u['id']),'id':u['id'],'username':u['username'],'admin':bool(u['admin'])}
@app.post('/collab/v1/logout')
def logout(request:Request):db.execute('DELETE FROM collaboration_sessions WHERE token_hash=?',(hashlib.sha256(request.cookies['paper_collaboration'].encode()).hexdigest(),));r=JSONResponse({'ok':True});r.delete_cookie('paper_collaboration');return r
@app.post('/collab/v1/users')
def users(request:Request,body:dict):
    if not principal(request)['admin']:raise HTTPException(403,'仅管理员可创建账号')
    try:return create_user(body['username'],body['password'],False)
    except (KeyError,ValueError) as e:raise HTTPException(422,str(e))
@app.get('/collab/v1/projects')
def projects(request:Request):
    u=principal(request);rows=db.rows('SELECT p.* FROM projects p JOIN project_members m ON p.id=m.project_id WHERE m.user_id=? AND p.archived=0',(u['id'],)) if not u['admin'] else db.rows('SELECT * FROM projects WHERE archived=0');return [p for p in rows if db.setting('sharing:'+p['id'],False)]
@app.post('/collab/v1/projects')
def create_project(request:Request,body:dict):
    user=principal(request);pid=db.uid();stamp=db.now();title=str(body.get('title','')).strip()
    if not 1<=len(title)<=300:raise HTTPException(422,'请输入项目名称')
    with db.transaction() as c:c.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,title,stamp,stamp));c.execute('INSERT INTO project_members VALUES(?,?,?)',(pid,user['id'],'owner'))
    db.set_setting('sharing:'+pid,True);return {'id':pid,'title':title}
@app.put('/collab/v1/projects/{pid}/members')
def member(pid,request:Request,body:dict):
    user=principal(request);membership(pid,user,owner=True);role=body.get('role')
    if role not in ('owner','editor','reviewer','viewer'):raise HTTPException(422,'角色无效')
    target=db.one('SELECT id FROM collaboration_users WHERE username=? AND disabled=0',(body.get('username'),))
    if not target:raise HTTPException(404,'用户不存在')
    previous=db.one('SELECT role FROM project_members WHERE project_id=? AND user_id=?',(pid,target['id']))
    if previous and previous['role']=='owner' and role!='owner' and db.one("SELECT COUNT(*) n FROM project_members WHERE project_id=? AND role='owner'",(pid,))['n']<=1:raise HTTPException(409,'请先指定另一位项目所有者')
    db.execute('INSERT INTO project_members VALUES(?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role',(pid,target['id'],role));sync.event(pid,user['id'],'membership',{'user_id':target['id'],'role':role});return {'ok':True}
@app.get('/collab/v1/projects/{pid}/snapshot')
def snapshot(pid,request:Request):
    user=principal(request);role=membership(pid,user);data=sync.packet(pid);data['role']=role;data['references']=db.rows('SELECT id,metadata,file_id,verification FROM bibliography WHERE project_id=?',(pid,));data['files']=db.rows('SELECT id,name,extension FROM files WHERE project_id=? AND active=1',(pid,));data['locks']=db.rows('SELECT l.* FROM locks l JOIN sections s ON s.id=l.section_id WHERE s.project_id=?',(pid,));data['remote_ai_allowed']=role in ('owner','editor') and float(db.setting('remote_ai_global_budget',0))>0 and float(db.setting('remote_ai_budget:'+user['id'],db.setting('remote_ai_global_budget',0) if user['admin'] else 0))>0;data['rule_package_ready']=bool(security.require_project(pid)['rule_package_id']);data['members']=db.rows('SELECT u.username,m.role FROM project_members m JOIN collaboration_users u ON u.id=m.user_id WHERE m.project_id=?',(pid,));return data
@app.post('/collab/v1/projects/{pid}/sections')
def section(pid,request:Request,body:dict):
    membership(pid,principal(request),edit=True);sid=db.uid();position=db.one('SELECT COUNT(*) n FROM sections WHERE project_id=?',(pid,))['n'];db.execute('INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)',(sid,pid,str(body.get('title','新章节'))[:300],position));sync.event(pid,principal(request)['id'],'section_created',{'section_id':sid});return security.require_section(sid)
@app.post('/collab/v1/projects/{pid}/operations')
def operation(pid,request:Request,body:dict):
    user=principal(request);membership(pid,user,edit=True)
    with _LOCK:result=sync.apply_operation(pid,user['id'],body)
    return JSONResponse(result,status_code=result['status_code'])
@app.get('/collab/v1/projects/{pid}/conflicts')
def conflicts(pid,request:Request,include_resolved:bool=False):
    membership(pid,principal(request));return db.rows('SELECT * FROM sync_conflicts WHERE project_id=?'+('' if include_resolved else " AND state='open'")+' ORDER BY created_at DESC LIMIT 100',(pid,))
@app.post('/collab/v1/projects/{pid}/conflicts/{cid}')
def resolve(pid,cid,request:Request,body:dict):user=principal(request);membership(pid,user,edit=True);return sync.resolve_conflict(pid,user['id'],cid,body)
@app.post('/collab/v1/projects/{pid}/comments')
def comment(pid,request:Request,body:dict):
    user=principal(request);role=membership(pid,user)
    if role=='viewer':raise HTTPException(403,'只读用户不能评论')
    text=str(body.get('text','')).strip()
    if not 1<=len(text)<=5000:raise HTTPException(422,'评论长度无效')
    sync.event(pid,user['id'],'comment',{'text':text});return {'ok':True}
@app.get('/collab/v1/projects/{pid}/events')
def events(pid,request:Request,after:int=0):membership(pid,principal(request));return db.rows('SELECT * FROM collaboration_events WHERE project_id=? AND seq>? ORDER BY seq LIMIT 200',(pid,after))
@app.websocket('/collab/v1/projects/{pid}/live')
async def live(websocket:WebSocket,pid:str):
    try:
        if websocket.url.hostname not in os.environ.get('PAPER_COLLAB_HOSTS','localhost,127.0.0.1,testserver').split(','):raise HTTPException(403,'Host未登记')
        user=principal(websocket);membership(pid,user);origin=websocket.headers.get('origin');allowed=os.environ.get('PAPER_COLLAB_ORIGINS','http://testserver,https://testserver,http://127.0.0.1:8774').split(',')
        if origin and origin not in allowed:raise HTTPException(403,'Origin未登记')
    except HTTPException:await websocket.close(code=1008);return
    await websocket.accept();connection_id=db.uid();raw_cursor=websocket.query_params.get('after','0')
    if not raw_cursor.isdigit() or int(raw_cursor)>2**63-1:await websocket.close(code=1008);return
    cursor=int(raw_cursor);heartbeat=0
    try:
        while True:
            user=principal(websocket);membership(pid,user);rows=db.rows('SELECT * FROM collaboration_events WHERE project_id=? AND seq>? ORDER BY seq LIMIT 200',(pid,cursor))
            for row in rows:await websocket.send_json(row);cursor=row['seq']
            heartbeat+=1
            if heartbeat==1 or heartbeat%25==0:
                db.execute('INSERT OR REPLACE INTO collaboration_presence VALUES(?,?,?,?)',(connection_id,pid,user['id'],time.time()))
                users=db.rows('SELECT DISTINCT u.username FROM collaboration_presence p JOIN collaboration_users u ON u.id=p.user_id WHERE p.project_id=? AND p.updated_at>?',(pid,time.time()-15))
                await websocket.send_json({'kind':'heartbeat','cursor':cursor,'users':[u['username'] for u in users]})
            try:await asyncio.wait_for(websocket.receive_text(),timeout=.2)
            except asyncio.TimeoutError:pass
    except (WebSocketDisconnect,RuntimeError,HTTPException):pass
    finally:db.execute('DELETE FROM collaboration_presence WHERE connection_id=?',(connection_id,))


@app.get('/health')
def health():return {'status':'ok','service':'paper-collaboration','version':'0.3.0','data_id':hashlib.sha256(str(security.DATA.resolve()).encode()).hexdigest()[:16]}

@app.exception_handler(ValueError)
async def value_error(request,error):return JSONResponse({'detail':str(error)[:800]},422)

@app.get('/collab/v1/users')
def list_users(request:Request):
    if not principal(request)['admin']:raise HTTPException(403,'仅管理员可查看账号目录')
    return db.rows('SELECT id,username,admin,disabled FROM collaboration_users ORDER BY username')

@app.patch('/collab/v1/users/{uid}')
def update_user(uid,request:Request,body:dict):
    user=principal(request)
    if not user['admin']:raise HTTPException(403,'仅管理员可管理账号')
    target=db.one('SELECT * FROM collaboration_users WHERE id=?',(uid,))
    if not target:raise HTTPException(404,'账号不存在')
    if body.get('disabled') and target['admin'] and db.one('SELECT count(*) n FROM collaboration_users WHERE admin=1 AND disabled=0')['n']<=1:raise HTTPException(409,'不能停用最后一个管理员')
    if 'ai_budget' in body:
        import math
        budget=float(body['ai_budget'])
        if not math.isfinite(budget) or not 0<=budget<=float(db.setting('remote_ai_global_budget',0)):raise HTTPException(422,'账号预算超出服务方配置的协作预算')
        db.set_setting('remote_ai_budget:'+uid,budget)
    if 'disabled' in body:db.execute('UPDATE collaboration_users SET disabled=? WHERE id=?',(int(bool(body['disabled'])),uid))
    if body.get('password'):db.execute('UPDATE collaboration_users SET password_hash=? WHERE id=?',(password_hash(body['password']),uid));db.execute('DELETE FROM collaboration_sessions WHERE user_id=?',(uid,))
    return {'ok':True}

@app.post('/collab/v1/projects/{pid}/sections/{sid}/review')
def review_section(pid,sid,request:Request,body:dict):
    user=principal(request);role=membership(pid,user)
    if role not in ('owner','editor','reviewer'):raise HTTPException(403,'只读用户不能登记审阅')
    section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于此项目')
    if not body.get('acknowledge') or not body.get('note'):raise HTTPException(422,'请填写审阅说明并明确确认')
    if section['current_revision_id']!=body.get('revision_id') or not section['current_revision_id']:raise HTTPException(409,'正文版本已变化，请重新核对')
    with db.transaction():
        section=security.require_section(sid)
        if section['current_revision_id']!=body['revision_id']:raise HTTPException(409,'审阅期间正文已变化，请重新核对')
        db.execute('INSERT INTO section_reviews VALUES(?,?,?,?,?)',(db.uid(),sid,section['current_revision_id'],'协作用户 '+user['username']+'：'+str(body['note'])[:3000],db.now()));db.execute("UPDATE sections SET state='user_reviewed' WHERE id=?",(sid,));sync.event(pid,user['id'],'human_review_declared',{'section_id':sid,'revision_id':section['current_revision_id']})
    return {'recorded':True,'reviewer':user['username'],'revision_id':section['current_revision_id']}

@app.get('/collab/v1/projects/{pid}/bundle')
def bundle(pid,request:Request):
    from .sync_bundle import make
    membership(pid,principal(request),owner=True);return make(pid,db.setting('sync_base:'+pid,{}))

@app.post('/collab/v1/import')
def import_bundle(request:Request,body:dict):
    from .sync_bundle import apply
    user=principal(request);packet=body.get('packet',{});pid=packet.get('project_id');old=db.one('SELECT id FROM projects WHERE id=?',(pid,))
    if old:membership(pid,user,edit=True)
    with db.transaction():
        result=apply(packet,user['id'],allow_new=not bool(old))
        if not old:db.execute('INSERT INTO project_members VALUES(?,?,?)',(pid,user['id'],'owner'));db.set_setting('sharing:'+pid,True)
    return result

@app.get('/collab/v1/projects/{pid}/artifacts/{aid}')
def artifact_preview(pid,aid,request:Request):
    membership(pid,principal(request));row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
    if not row or not row['sha256']:raise HTTPException(404,'图表不存在')
    from PIL import Image
    target=security.DATA/'previews'/('collab-'+row['sha256']+'.png')
    with Image.open(security.safe_object(row['sha256'])) as image:image.convert('RGB').save(target)
    return FileResponse(target,media_type='image/png')


@app.post('/collab/v1/projects/{pid}/sections/{sid}/writing')
def writing(pid,sid,request:Request,body:dict):
    from . import tasks
    user=principal(request);membership(pid,user,edit=True)
    if not body.get('acknowledge_charges'):raise HTTPException(422,'请明确知悉模型调用按服务方规则计费')
    if float(db.setting('remote_ai_global_budget',0))<=0 or float(db.setting('remote_ai_budget:'+user['id'],db.setting('remote_ai_global_budget',0) if user['admin'] else 0))<=0:raise HTTPException(403,'管理员尚未开放此账号的AI预算')
    section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于本项目')
    payload={'section_id':sid,'base_revision_id':body.get('base_revision_id'),'operation':'generate','instruction':str(body.get('instruction',''))[:5000],'remote_actor':user['id']}
    tasks.writing_preflight(pid,payload);task=tasks.enqueue(pid,'write',payload);sync.event(pid,user['id'],'writing_queued',{'task_id':task['id'],'section_id':sid});return {'task_id':task['id'],'status':task['status']}

@app.get('/collab/v1/projects/{pid}/tasks/{tid}')
def remote_task(pid,tid,request:Request):
    membership(pid,principal(request));row=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(tid,pid))
    if not row:raise HTTPException(404,'任务不属于当前项目')
    return {'id':row['id'],'status':row['status'],'phase':row['phase'],'progress':row['progress'],'error':row['error'],'result':db.loads(row['result'])}

@app.post('/collab/v1/projects/{pid}/sections/{sid}/accept')
def accept_candidate(pid,sid,request:Request,body:dict):
    membership(pid,principal(request),edit=True);section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于当前项目')
    from .main import accept
    return accept(sid,body)


@app.get('/collab/v1/projects/{pid}/tasks/{tid}/stream')
def remote_stream(pid,tid,request:Request,after:int=0):
    membership(pid,principal(request));task=db.one('SELECT id FROM tasks WHERE id=? AND project_id=?',(tid,pid))
    if not task:raise HTTPException(404,'任务不属于当前项目')
    from .advanced import stream
    return stream(tid,after)


@app.get('/collab/v1/projects/{pid}/files/{fid}')
def project_file(pid,fid,request:Request):
    membership(pid,principal(request));row=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(fid,pid))
    if not row or row['egress']=='local_only':raise HTTPException(404,'文件不属于本项目可共享范围')
    return FileResponse(security.safe_object(row['sha256']),filename=row['name'])


@app.post('/collab/v1/projects/{pid}/conflicts/{cid}/restore')
def restore_conflict(pid,cid,request:Request,body:dict):
    user=principal(request);membership(pid,user,edit=True);return sync.restore_branch(pid,user['id'],cid,body)


from .api.v2 import install as install_api_v2
install_api_v2(app,service='collaboration',legacy_prefix='/collab/v1')
