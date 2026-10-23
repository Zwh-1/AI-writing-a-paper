"""Revision-aware project sync, conflict branches, and encrypted WebDAV transport."""
import base64,hashlib,io,json,os,zipfile,urllib.parse
import httpx
from . import db,security,documents
from fastapi import HTTPException

def sharing_gate(pid):
    security.require_project(pid)
    if not db.setting('sharing:'+pid,False):raise HTTPException(403,'本项目尚未明确开启协作/云同步')
    if db.one("SELECT id FROM files WHERE project_id=? AND egress='local_only' LIMIT 1",(pid,)):raise HTTPException(403,'项目含仅限本地的资料；请先审核同步范围')
    for artifact in db.rows('SELECT metadata FROM artifacts WHERE project_id=?',(pid,)):
        if db.loads(artifact['metadata'],{}).get('egress')=='local_only':raise HTTPException(403,'项目含仅限本地的产物或截图，拒绝发送派生内容')

def event(pid,actor,kind,payload):
    db.execute('INSERT INTO collaboration_events(project_id,actor_id,kind,payload,created_at) VALUES(?,?,?,?,?)',(pid,actor,kind,db.dumps(payload),db.now()))

def three_way(base,local,remote):
    if base.get('type')!='doc' or local.get('type')!='doc' or remote.get('type')!='doc':return None
    def keyed(doc):
        content=doc.get('content',[]);keys=[n.get('attrs',{}).get('nodeId') for n in content]
        if not all(keys) or len(set(keys))!=len(keys):return None
        return dict(zip(keys,content)),keys
    b=keyed(base);l=keyed(local);r=keyed(remote)
    if None in (b,l,r):return None
    bm,bo=b;lm,lo=l;rm,ro=r;left={k for k in set(bm)|set(lm) if bm.get(k)!=lm.get(k)};right={k for k in set(bm)|set(rm) if bm.get(k)!=rm.get(k)}
    if any(lm.get(k)!=rm.get(k) for k in left&right):return None
    # Concurrent order changes are never silently resolved.
    old_order=[k for k in bo if k in lm and k in rm]
    if [k for k in lo if k in old_order]!=old_order or [k for k in ro if k in old_order]!=old_order:return None
    merged={**bm,**lm}
    for k in right:
        if k in rm:merged[k]=rm[k]
        else:merged.pop(k,None)
    order=[k for k in lo if k in merged]
    for pos,k in enumerate(ro):
        if k in merged and k not in order:
            prior=next((v for v in reversed(ro[:pos]) if v in order),None);index=order.index(prior)+1 if prior else 0;order.insert(index,k)
    return {'type':'doc','content':[merged[k] for k in order]}

def apply_operation(pid,actor,body):
    operation=str(body.get('operation_id',''));sid=body.get('section_id')
    if not operation or len(operation)>128:raise HTTPException(422,'同步操作ID无效')
    fingerprint=hashlib.sha256(db.dumps(body).encode()).hexdigest()
    with db.transaction():
        s=security.require_section(sid)
        if s['project_id']!=pid:raise HTTPException(422,'同步章节不属于当前项目')
        old=db.one('SELECT * FROM sync_operations WHERE project_id=? AND operation_id=?',(pid,operation))
        if old:
            if old['payload_hash']!=fingerprint:raise HTTPException(409,'相同操作ID提交了不同内容')
            return db.loads(old['result'])
        doc=documents.normalize(body.get('document',{}));base=body.get('base_revision_id');merged=False
        if base!=s['current_revision_id'] and body.get('auto_merge') and base:
            b=db.one('SELECT * FROM revisions WHERE id=? AND section_id=?',(base,sid));current=documents.current(s)
            if b and current:
                combined=three_way(db.loads(b['document']),db.loads(current['document']),doc)
                if combined is not None:doc=combined;base=s['current_revision_id'];merged=True
        if base==s['current_revision_id']:
            revision=documents.save(s,doc,base,kind='sync');result={'status_code':200,'revision':revision,'operation_id':operation,'auto_merged':merged};event(pid,actor,'revision',{'section_id':sid,'revision_id':revision['id'],'auto_merged':merged})
        else:
            cid=db.uid();current=s['current_revision_id'];db.execute('INSERT INTO sync_conflicts(id,project_id,section_id,base_revision_id,local_revision_id,remote_document,created_at) VALUES(?,?,?,?,?,?,?)',(cid,pid,sid,base,current,db.dumps(doc),db.now()));result={'status_code':409,'conflict_id':cid,'current_revision_id':current,'remote_content_preserved':True};event(pid,actor,'conflict',{'conflict_id':cid,'section_id':sid})
        db.execute('INSERT INTO sync_operations VALUES(?,?,?,?,?)',(pid,operation,fingerprint,db.dumps(result),db.now()))
        return result

def resolve_conflict(pid,actor,cid,body):
    row=db.one("SELECT * FROM sync_conflicts WHERE id=? AND project_id=? AND state='open'",(cid,pid))
    if not row:raise HTTPException(404,'未找到待处理冲突')
    s=security.require_section(row['section_id']);base=body.get('base_revision_id')
    if s['current_revision_id']!=base:raise HTTPException(409,'正文再次变化，请重新比较')
    choice=body.get('choice')
    if choice not in ('keep_local','take_remote','manual'):raise HTTPException(422,'请选择保留本地、采用远端或手工合并')
    if choice!='keep_local':
        doc=db.loads(row['remote_document']) if choice=='take_remote' else body.get('document',{});revision=documents.save(s,doc,base,kind='sync_merge')
    else:revision=db.one('SELECT * FROM revisions WHERE id=?',(base,))
    db.execute("UPDATE sync_conflicts SET state='resolved' WHERE id=?",(cid,));event(pid,actor,'conflict_resolved',{'conflict_id':cid,'choice':choice});return {'revision':revision,'resolved':True}

def packet(pid):
    sharing_gate(pid);p=security.require_project(pid);sections=db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived'",(pid,));revisions=[]
    for section in sections:
        if section['current_revision_id']:revisions.append(db.one('SELECT * FROM revisions WHERE id=?',(section['current_revision_id'],)))
    return {'format':'paper-revision-sync-v1','project':{'id':pid,'title':p['title']},'sections':sections,'revisions':revisions,'created_at':db.now(),'contains_credentials':False,'scope':'正文修订快照；资料、书目和图表需要在接收设备保留相同资源ID'}

def encrypt(payload,password):
    import zlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    if not isinstance(password,str) or len(password)<12:raise ValueError('同步口令至少12字符，且应与API key分开设置')
    salt=os.urandom(16);nonce=os.urandom(12);key=Scrypt(salt=salt,length=32,n=2**14,r=8,p=1).derive(password.encode());raw=db.dumps(payload).encode()
    if len(raw)>300*1024*1024:raise ValueError('项目同步快照超过300MB')
    return b'PAPER-SYNC-2\0'+salt+nonce+AESGCM(key).encrypt(nonce,zlib.compress(raw,6),b'PAPER-SYNC-2')
def decrypt(data,password):
    import zlib
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    prefix=b'PAPER-SYNC-2\0'
    if not isinstance(password,str) or len(data)>220*1024*1024 or not data.startswith(prefix):raise ValueError('同步包格式或大小无效')
    data=data[len(prefix):];salt=data[:16];nonce=data[16:28];key=Scrypt(salt=salt,length=32,n=2**14,r=8,p=1).derive(password.encode())
    try:
        compressed=AESGCM(key).decrypt(nonce,data[28:],b'PAPER-SYNC-2');decoder=zlib.decompressobj();raw=decoder.decompress(compressed,300*1024*1024+1)
        if len(raw)>300*1024*1024 or not decoder.eof:raise ValueError('同步包展开超过限制')
        result=json.loads(raw)
    except Exception as error:raise ValueError('同步包认证或解压失败：口令不符、数据损坏或规模超限') from error
    if result.get('format')!='paper-project-sync-v2':raise ValueError('同步包版本无效')
    return result

def cloud(pid,body,pull=False):
    sharing_gate(pid);url=body['url'];parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:raise ValueError('WebDAV地址必须是不包含凭据的HTTPS文件地址')
    user=body.get('username','');password=body.get('cloud_password','');passphrase=body.get('sync_password','');headers={};etag=body.get('etag')
    if not pull:
        if etag:headers['If-Match']=etag
        else:headers['If-None-Match']='*'
    from .network import proxy_for
    ca_file=os.environ.get('PAPER_CLOUD_CA_CERT');verify=__import__('ssl').create_default_context(cafile=ca_file) if ca_file else True
    with httpx.Client(timeout=30,proxy=proxy_for(url),trust_env=False,follow_redirects=False,verify=verify,auth=(user,password)) as connection:
        if pull:
            with connection.stream('GET',url) as response:
                if response.status_code!=200:raise ValueError(f'云端读取HTTP {response.status_code}')
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>220*1024*1024:raise ValueError('云端同步包超出大小上限')
                    chunks.append(chunk)
                data=decrypt(b''.join(chunks),passphrase);remote_tag=response.headers.get('etag')
                if data['project_id']!=pid:raise ValueError('云端快照不属于当前项目')
                sha=security.put_blob(b''.join(chunks));return {'etag':remote_tag,'encrypted_blob':sha,'project_id':pid,'remote_heads':data.get('heads',{}),'resource_count':len(data.get('blobs',{})),'automatically_applied':False,'next_step':'点击导入/合并，重叠修改会保留为冲突分支'}
        from .sync_bundle import make
        snapshot=make(pid,db.setting('sync_base:'+pid,{}));data=encrypt(snapshot,passphrase);sharing_gate(pid);response=connection.put(url,headers={**headers,'Content-Type':'application/octet-stream'},content=data)
        if response.status_code==412:raise HTTPException(409,'云端已有其他设备更新，已拒绝覆盖；请先拉取并比较')
        if response.status_code not in (200,201,204):raise ValueError(f'云端写入HTTP {response.status_code}，请核对权限后再操作')
        db.set_setting('sync_base:'+pid,snapshot['heads']);db.set_setting('sync_records:'+pid,snapshot['record_heads']);db.set_setting('sync_etag:'+pid,response.headers.get('etag'))
        return {'etag':response.headers.get('etag'),'encrypted_bytes':len(data),'warning':None if response.headers.get('etag') else '服务未返回ETag，下一次推送将拒绝盲目覆盖；请先拉取','api_key_used':False}


def restore_branch(pid,actor,cid,body):
    with db.transaction():
        row=db.one('SELECT * FROM sync_conflicts WHERE id=? AND project_id=?',(cid,pid))
        if not row:raise HTTPException(404,'冲突历史不属于当前项目')
        section=security.require_section(row['section_id'])
        if section['current_revision_id']!=body.get('base_revision_id'):raise HTTPException(409,'正文已变化，请重新比较后恢复')
        revision=documents.save(section,db.loads(row['remote_document']),body.get('base_revision_id'),kind='sync_restore');event(pid,actor,'revision',{'section_id':section['id'],'revision_id':revision['id'],'restored_branch':cid});return {'revision':revision,'branch_history_retained':True}
