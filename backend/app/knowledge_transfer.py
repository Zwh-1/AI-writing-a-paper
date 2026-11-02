"""Complete project knowledge archives and encrypted WebDAV snapshots.

Archives contain original bytes and every project-owned history. Credentials and
device identities are excluded. Same-project conflicts restore an isolated copy;
explicit forks use a staging process to remap identities without touching originals.
"""
import base64,hashlib,io,json,os,re,sqlite3,subprocess,sys,zipfile
from pathlib import Path,PurePosixPath
from fastapi import HTTPException
from . import db,security

FORMAT='paper-knowledge-bundle-v3'
EXCLUDE={'settings','schema_migrations','backups','format_profiles','collaboration_users','collaboration_sessions','project_members','collaboration_presence','collaboration_events','sync_operations','sync_conflicts','sync_record_conflicts','knowledge_transfer_receipts'}
SETTING_PREFIXES=('format:','template:','graph_wiki_enabled:','knowledge_auto_refresh:','knowledge_refresh_mode:','knowledge_refresh_budget:','knowledge_release:','knowledge_release_locked:','reference_knowledge:','retrieval:','expense_policy:','expense_currency:')
MAX_RAW=2_000_000_000
MAX_ZIP=1_200_000_000

def encode(value):
    if isinstance(value,bytes):return {'$binary':base64.b64encode(value).decode()}
    if isinstance(value,dict):return {k:encode(v) for k,v in value.items()}
    if isinstance(value,list):return [encode(v) for v in value]
    return value

def decode(value):
    if isinstance(value,dict) and set(value)=={'$binary'}:return base64.b64decode(value['$binary'],validate=True)
    if isinstance(value,dict):return {k:decode(v) for k,v in value.items()}
    if isinstance(value,list):return [decode(v) for v in value]
    return value

def schema(connection):
    tables={}
    for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%' AND sql NOT LIKE 'CREATE VIRTUAL%'"):
        name=row[0]
        if name in EXCLUDE:continue
        info=[dict(r) for r in connection.execute('PRAGMA table_info("'+name+'")')]
        tables[name]={'columns':[r['name'] for r in info],'keys':[r['name'] for r in sorted(info,key=lambda r:r['pk']) if r['pk']],
                      'foreign':[dict(r) for r in connection.execute('PRAGMA foreign_key_list("'+name+'")')]}
    return tables

def collect(connection,pid):
    specs=schema(connection);tables={name:{} for name in specs}
    def add(name,rows):
        changed=False
        for row in rows:
            r=dict(row)
            if 'project_id' in r and r['project_id']!=pid:continue
            key=tuple(r[k] for k in specs[name]['keys'])
            if key not in tables[name]:tables[name][key]=r;changed=True
        return changed
    add('projects',connection.execute('SELECT * FROM projects WHERE id=?',(pid,)))
    for name,spec in specs.items():
        if name!='projects' and 'project_id' in spec['columns']:add(name,connection.execute('SELECT * FROM "'+name+'" WHERE project_id=?',(pid,)))
    for _ in range(len(specs)):
        changed=False
        for name,spec in specs.items():
            if 'project_id' in spec['columns'] or name=='projects':continue
            for fk in spec['foreign']:
                parent=fk['table'];target=fk['to']
                if parent not in tables or not tables[parent] or not target:continue
                ids=list({r[target] for r in tables[parent].values()})
                for start in range(0,len(ids),400):
                    batch=ids[start:start+400]
                    changed=add(name,connection.execute('SELECT * FROM "'+name+'" WHERE "'+fk['from']+'" IN ('+','.join('?' for _ in batch)+')',batch)) or changed
        if not changed:break
    return {name:list(rows.values()) for name,rows in tables.items() if rows},specs

def hashes_in(value):
    if isinstance(value,str):
        if re.fullmatch('[a-f0-9]{64}',value):yield value
        elif value[:1] in ('{','['):
            try:yield from hashes_in(json.loads(value))
            except (ValueError,TypeError):pass
    elif isinstance(value,dict):
        for v in value.values():yield from hashes_in(v)
    elif isinstance(value,list):
        for v in value:yield from hashes_in(v)

def secret_check(tables):
    keys=[]
    for profile in ('','material-summary','gpt-fallback','image-generation'):
        try:key=security.get_key(profile)
        except Exception:key=''
        if key and len(key)>=8:keys.append(key)
    for table,rows in tables.items():
        for row in rows:
            for value in row.values():
                if isinstance(value,str) and any(key in value for key in keys):raise ValueError('项目记录包含已保存密钥，已阻止打包：'+table)

def export(pid,task_id=None):
    from .config import DATA
    security.require_project(pid);identity=db.uid();folder=DATA/'exports'/'knowledge';folder.mkdir(parents=True,exist_ok=True)
    snapshot=DATA/'tmp'/(identity+'.sqlite3');path=folder/(identity+'-complete.zip')
    source=db.connect();target=sqlite3.connect(snapshot)
    try:source.backup(target)
    finally:source.close();target.close()
    connection=sqlite3.connect(snapshot);connection.row_factory=sqlite3.Row
    try:
        tables,specs=collect(connection,pid);secret_check(tables)
        settings={prefix+pid:db.loads(r[0]) for prefix in SETTING_PREFIXES if (r:=connection.execute('SELECT value FROM settings WHERE key=?',(prefix+pid,)).fetchone())}
        sourceids={r['id'] for r in tables.get('sources',[])}
        supersessions=connection.execute("SELECT value FROM settings WHERE key='document_observation_supersessions'").fetchone()
        if supersessions:settings['document_observation_supersessions']={k:v for k,v in db.loads(supersessions[0],{}).items() if k in sourceids}
        manifest={'format':FORMAT,'schema':connection.execute('PRAGMA user_version').fetchone()[0],'project_id':pid,'created_at':db.now(),
                  'tables':{},'files':{},'settings':settings,'artifacts':{},'credentials_included':False,'original_histories_preserved':True}
        total=0
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for name,rows in tables.items():
                raw=db.dumps(encode(rows)).encode();entry='tables/'+name+'.json';total+=len(raw)
                if total>MAX_RAW:raise ValueError('完整知识包超过解压体积限制')
                archive.writestr(entry,raw);manifest['files'][entry]=hashlib.sha256(raw).hexdigest();manifest['tables'][name]={'count':len(rows),'columns':specs[name]['columns']}
            needed={r['sha256'] for r in tables.get('files',[])}
            for table,rows in tables.items():
                for sha in set(hashes_in(rows)):
                    if security.safe_object(sha).is_file():needed.add(sha)
            for sha in sorted(needed):
                original=security.safe_object(sha)
                if not original.is_file():raise ValueError('完整知识包缺少原始资源：'+sha)
                raw=original.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('原件哈希不一致：'+sha)
                total+=len(raw)
                if total>MAX_RAW:raise ValueError('完整知识包超过解压体积限制')
                entry='objects/'+sha;archive.writestr(entry,raw);manifest['files'][entry]=sha
            for artifact in tables.get('knowledge_artifacts',[]):
                if artifact['kind']=='knowledge_bundle':
                    manifest['artifacts'][artifact['id']]={'available':False,'regenerable_archive':True,'state':artifact['state']};continue
                original=Path(artifact['path']).resolve()
                if not original.is_relative_to(DATA.resolve()) or not original.exists():
                    manifest['artifacts'][artifact['id']]={'available':False,'state':artifact['state']};continue
                files=list(original.rglob('*')) if original.is_dir() else [original];entries=[]
                for resource in files:
                    if not resource.is_file() or resource.is_symlink():continue
                    relative=resource.relative_to(original) if original.is_dir() else Path(resource.name)
                    entry='resources/'+artifact['id']+'/'+relative.as_posix();raw=resource.read_bytes();total+=len(raw)
                    if total>MAX_RAW:raise ValueError('完整知识包超过解压体积限制')
                    archive.writestr(entry,raw);manifest['files'][entry]=hashlib.sha256(raw).hexdigest();entries.append(entry)
                manifest['artifacts'][artifact['id']]={'available':True,'directory':original.is_dir(),'entries':entries}
            archive.writestr('manifest.json',db.dumps(manifest))
        if path.stat().st_size>MAX_ZIP:raise ValueError('完整知识压缩包超过1.2GB限制')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        result={'id':identity,'path':str(path),'sha256':digest,'bytes':path.stat().st_size,'tables':len(tables),'records':sum(len(r) for r in tables.values()),'project_id':pid,'format':FORMAT,'credentials_included':False,
                'download':f'/api/v1/projects/{pid}/knowledge/transfers/{identity}/download'}
        db.execute('INSERT INTO knowledge_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(identity,pid,task_id,'knowledge_bundle',digest,str(path),db.dumps(result),'ready',db.now()))
        return result
    except Exception:
        path.unlink(missing_ok=True);raise
    finally:connection.close();snapshot.unlink(missing_ok=True)

def validate(raw):
    if len(raw)>MAX_ZIP:raise ValueError('知识包超过1.2GB限制')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or sum(i.file_size for i in z.infolist())>MAX_RAW:raise ValueError('知识包重复条目或体积无效')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('format')!=FORMAT or not re.fullmatch('[a-f0-9]{32}',manifest.get('project_id','')):raise ValueError('完整知识包格式无效')
        from .writing_agent.migrations import CURRENT
        if manifest.get('schema')!=CURRENT:raise ValueError('请使用相同数据库版本导入知识包')
        if set(names)!=set(manifest.get('files',{}))|{'manifest.json'}:raise ValueError('知识包清单不完整')
        with db.connect() as c:specs=schema(c)
        if set(manifest.get('tables',{}))-set(specs):raise ValueError('知识包包含不允许的表')
        tables={}
        for name,digest in manifest['files'].items():
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:raise ValueError('知识包路径无效')
            content=z.read(name)
            if hashlib.sha256(content).hexdigest()!=digest:raise ValueError('知识包资源哈希不匹配')
            if name.startswith('tables/'):
                table=p.stem
                if table not in manifest['tables']:raise ValueError('未登记数据表')
                rows=decode(json.loads(content))
                if not isinstance(rows,list) or len(rows)!=manifest['tables'][table]['count']:raise ValueError('知识包记录数不匹配')
                if any(not isinstance(r,dict) or set(r)!=set(specs[table]['columns']) or 'project_id' in r and r['project_id']!=manifest['project_id'] for r in rows):raise ValueError('知识包字段或项目所属无效')
                tables[table]=rows
        if len(tables.get('projects',[]))!=1 or tables['projects'][0]['id']!=manifest['project_id']:raise ValueError('项目对象不完整')
        for key,value in manifest.get('settings',{}).items():
            if key!='document_observation_supersessions' and not any(key==prefix+manifest['project_id'] for prefix in SETTING_PREFIXES):raise ValueError('知识包不能携带全局配置或密钥')
        for identity,resource in manifest.get('artifacts',{}).items():
            if identity not in {r['id'] for r in tables.get('knowledge_artifacts',[])}:raise ValueError('资源清单指向外部产物')
            if any(not entry.startswith('resources/'+identity+'/') or entry not in manifest['files'] for entry in resource.get('entries',[])):raise ValueError('产物资源路径或所属无效')
        secret_check(tables)
        return manifest,tables

def preview(raw):
    manifest,tables=validate(raw);pid=manifest['project_id'];existing=db.one('SELECT id FROM projects WHERE id=?',(pid,))
    return {'format':FORMAT,'project_id':pid,'title':tables['projects'][0]['title'],'sha256':hashlib.sha256(raw).hexdigest(),'existing_project':bool(existing),
            'records':sum(len(rows) for rows in tables.values()),'tables':{name:len(rows) for name,rows in tables.items()},
            'strategies':['isolated','fork'] if existing else ['restore','isolated','fork'],'originals_and_histories_included':True,
            'note':'已有项目不被覆盖；隔离恢复保留原始ID，新项目分支显式映射ID，历史人工记录保持导入身份。'}

def restore(raw,body):
    from .config import DATA,ROOT
    manifest,tables=validate(raw);digest=hashlib.sha256(raw).hexdigest()
    if body.get('expected_sha256')!=digest:raise ValueError('请先预览并核对知识包哈希')
    mode=body.get('mode','isolated')
    if mode not in ('isolated','restore','fork'):raise ValueError('导入策略无效')
    pid=manifest['project_id']
    if mode=='restore' and db.one('SELECT id FROM projects WHERE id=?',(pid,)):raise HTTPException(409,'本机已有该项目，请使用隔离恢复或新项目分支')
    identity=db.uid();stage=DATA/'restored'/identity;stage.mkdir(parents=True,exist_ok=False)
    archive=stage/'incoming.zip';archive.write_bytes(raw)
    env={**os.environ,'PAPER_DATA_DIR':str(stage),'PYTHONUTF8':'1'}
    result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/knowledge_transfer_worker.py'),str(archive),mode,body.get('title','')],env=env,cwd=str(ROOT),capture_output=True,text=True,encoding='utf-8',timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('知识包隔离校验/恢复失败：'+security.redact(result.stderr[-1000:]))
    detail=json.loads((stage/'transfer-result.json').read_text(encoding='utf-8'))
    if mode=='isolated':return {**detail,'restored_data_directory':str(stage),'automatically_applied':False,'credentials_included':False}
    connection=sqlite3.connect(stage/'workbench.sqlite3');connection.row_factory=sqlite3.Row
    try:
        imported,_=collect(connection,detail['project_id'])
        import shutil
        for artifact in imported.get('knowledge_artifacts',[]):
            origin=Path(artifact['path']).resolve()
            if origin.is_relative_to(stage.resolve()) and origin.exists():
                relative=origin.relative_to(stage.resolve());destination=DATA/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                if origin.is_dir():shutil.copytree(origin,destination,dirs_exist_ok=True)
                else:shutil.copy2(origin,destination)
                artifact['path']=str(destination)
                artifact['manifest_json']=artifact['manifest_json'].replace(str(stage).replace('\\','\\\\'),str(DATA).replace('\\','\\\\'))
        with db.transaction() as target:
            target.execute('PRAGMA defer_foreign_keys=ON')
            if target.execute('SELECT id FROM projects WHERE id=?',(detail['project_id'],)).fetchone():raise HTTPException(409,'目标项目已存在，原项目保持完整')
            for table,rows in imported.items():
                for row in rows:
                    if table in ('audit','task_events') and isinstance(row.get('id'),int):row={k:v for k,v in row.items() if k!='id'}
                    columns=list(row);verb='INSERT OR REPLACE' if table=='project_change_clock' else 'INSERT'
                    target.execute(verb+' INTO "'+table+'"('+','.join('"'+c+'"' for c in columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row[c] for c in columns))
            for r in connection.execute('SELECT key,value FROM settings'):
                if r['key']=='document_observation_supersessions':
                    db.set_setting(r['key'],{**db.setting(r['key'],{}),**db.loads(r['value'],{})})
                elif any(r['key']==prefix+detail['project_id'] for prefix in SETTING_PREFIXES):db.set_setting(r['key'],db.loads(r['value']))
            db.set_setting('sharing:'+detail['project_id'],False)
            for sha in (stage/'objects').iterdir():
                if sha.is_file():security.put_blob(sha.read_bytes())
            # Resource copies retain verified bytes; the separate staging copy
            # remains recoverable if a database constraint rejects the import.
            from .parsers import tokens
            for r in imported.get('sources',[]):target.execute('INSERT INTO source_fts VALUES(?,?)',(r['id'],tokens(r['text'])))
            for r in imported.get('knowledge_units',[]):target.execute('INSERT INTO knowledge_unit_fts VALUES(?,?)',(r['id'],tokens(r['text'])))
            if target.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('导入后的来源外键检查失败')
            target.execute('INSERT INTO knowledge_transfer_receipts VALUES(?,?,?,?,?,?,?)',(db.uid(),detail['project_id'],pid,digest,db.dumps(detail.get('mapping',{})),db.dumps(detail),db.now()))
            db.audit(detail['project_id'],'complete_knowledge_imported',{'source_project_id':pid,'package_sha256':digest,'mode':mode})
        return {**detail,'mode':mode,'resources_verified':len(manifest['files']),'automatically_applied':True,'human_approval_added':False}
    finally:connection.close()

def cloud(pid,body,pull=False):
    from .synchronization import sharing_gate
    from .network import proxy_for
    import httpx
    from urllib.parse import urlsplit
    sharing_gate(pid);url=body.get('url','');parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:raise ValueError('云地址须为不含凭据的HTTPS文件地址')
    password=body.get('sync_password','')
    if len(password)<12:raise ValueError('知识同步加密口令至少12字符')
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    prefix=b'PAPER-KNOWLEDGE-3\0'
    def key(salt):return Scrypt(salt=salt,length=32,n=2**14,r=8,p=1).derive(password.encode())
    with httpx.Client(timeout=90,proxy=proxy_for(url),trust_env=False,follow_redirects=False,auth=(body.get('username',''),body.get('cloud_password',''))) as client:
        if pull:
            with client.stream('GET',url) as response:
                if response.status_code!=200:raise ValueError('云读取HTTP '+str(response.status_code))
                chunks=[];size=0
                for chunk in response.iter_bytes():
                    size+=len(chunk)
                    if size>MAX_ZIP+100:raise ValueError('云知识包超过上限')
                    chunks.append(chunk)
                encrypted=b''.join(chunks)
                if not encrypted.startswith(prefix):raise ValueError('云知识包版本无效')
                offset=len(prefix);salt=encrypted[offset:offset+16];nonce=encrypted[offset+16:offset+28]
                raw=AESGCM(key(salt)).decrypt(nonce,encrypted[offset+28:],prefix)
                information=preview(raw)
                if information['project_id']!=pid:raise ValueError('云包不属于当前项目')
                sha=security.put_blob(raw);db.set_setting('knowledge_cloud_download:'+pid,sha)
                return {'preview':information,'package_blob':sha,'etag':response.headers.get('etag'),'automatically_applied':False,'next_step':'核对预览后隔离恢复或导入新项目分支'}
        bundle=export(pid);raw=Path(bundle['path']).read_bytes();salt=os.urandom(16);nonce=os.urandom(12)
        encrypted=prefix+salt+nonce+AESGCM(key(salt)).encrypt(nonce,raw,prefix)
        etag=body.get('etag');headers={'If-Match':etag} if etag else {'If-None-Match':'*'}
        sharing_gate(pid);response=client.put(url,content=encrypted,headers={**headers,'Content-Type':'application/octet-stream'})
        if response.status_code==412:raise HTTPException(409,'云端已经更新，拒绝覆盖；请先拉取比较')
        if response.status_code not in (200,201,204):raise ValueError('云写入HTTP '+str(response.status_code))
        db.audit(pid,'complete_knowledge_cloud_push',{'sha256':bundle['sha256'],'etag':response.headers.get('etag')})
        return {'etag':response.headers.get('etag'),'bytes':len(encrypted),'package_sha256':bundle['sha256'],'credentials_included':False,'format':FORMAT}
