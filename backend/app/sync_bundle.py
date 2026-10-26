import re
"""Project-scoped, resource-complete sync packets. Never includes global settings/credentials."""
import base64,hashlib,json,sqlite3
from . import db,security,documents
from fastapi import HTTPException
DIRECT=('advanced_runs','files','artifacts','facts','bibliography','rules','rule_packages','sections','project_revisions','terms','mentor_tasks','project_formats','manual_rule_reviews','retrieval_evaluations')
INDIRECT={'pages':('file_id','files'),'sources':('file_id','files'),'fact_revisions':('fact_id','facts'),'bibliography_revisions':('reference_id','bibliography'),'revisions':('section_id','sections'),'citations':('revision_id','revisions'),'dependencies':('project_id','projects'),'locks':('section_id','sections'),'section_reviews':('section_id','sections'),'section_archives':('section_id','sections'),'rule_conditions':('rule_id','rules'),'file_derivations':('child_file_id','files')}
ORDER=('advanced_runs','files','file_derivations','pages','sources','artifacts','facts','fact_revisions','bibliography','bibliography_revisions','rules','rule_packages','sections','revisions','citations','dependencies','terms','mentor_tasks','project_formats','manual_rule_reviews','section_reviews','section_archives','rule_conditions','locks','project_revisions','retrieval_evaluations')
IMMUTABLE={'pages','sources','fact_revisions','bibliography_revisions','rule_packages','revisions','citations','dependencies','project_revisions','manual_rule_reviews','section_reviews','retrieval_evaluations'}

MUTABLE={'projects','sections','files','pages','sources','artifacts','facts','bibliography','rules','terms','mentor_tasks','project_formats','locks','rule_conditions'}
SKIP={'projects':{'updated_at','created_at','outline_revision'},'sections':{'current_revision_id','stale','stale_reason','state'},'facts':{'updated_at','created_at'}}
def preserve_version_history(table,old,remote,pid):
    remote=remote.copy()
    if table in ('facts','bibliography') and 'revision' in remote:
        remote['revision']=max(int(old.get('revision',0))+1,int(remote['revision']))
        history='fact_revisions' if table=='facts' else 'bibliography_revisions';foreign='fact_id' if table=='facts' else 'reference_id'
        db.execute('INSERT INTO '+history+' VALUES(?,?,?,?)',(db.uid(),old['id'],db.dumps(old),db.now()))
    if table in ('facts','bibliography','rules','sources','files','projects','rule_conditions'):
        documents.fact_changed(pid,old.get('id','sync'))
    return remote

def record_hash(table,row):return hashlib.sha256(db.dumps({k:row[k] for k in sorted(row) if k not in SKIP.get(table,set())}).encode()).hexdigest()
def record_key(table,row):
    keys=[x['name'] for x in sorted(db.rows('PRAGMA table_info('+table+')'),key=lambda x:x['pk']) if x['pk']]
    return table+':'+db.dumps([row[k] for k in keys])

def make(pid,base_heads=None):
    if db.one('SELECT id FROM knowledge_parses WHERE project_id=? LIMIT 1',(pid,)):
        raise HTTPException(409,'该项目含版本化证据知识库，旧版云同步无法完整承载，请使用完整本地备份与隔离恢复')
    if db.one('SELECT id FROM thesis_core_versions WHERE project_id=? LIMIT 1',(pid,)) or db.one('SELECT id FROM repository_snapshots WHERE project_id=? LIMIT 1',(pid,)):
        raise HTTPException(409,'该项目含新版代码证据与写作分析，旧版云同步无法完整承载。请使用包含全部资源的本地备份与隔离恢复；不会静默丢弃新数据')
    from .synchronization import sharing_gate
    tables={}
    with db.transaction():
        sharing_gate(pid)
        if db.one("SELECT id FROM sync_conflicts WHERE project_id=? AND state='open' LIMIT 1",(pid,)) or db.one("SELECT id FROM sync_record_conflicts WHERE project_id=? AND state='open' LIMIT 1",(pid,)):raise HTTPException(409,'请先处理同步冲突，再发布下一份快照')
        project=security.require_project(pid);tables['projects']=[project]
        for table in DIRECT:tables[table]=db.rows('SELECT * FROM '+table+' WHERE project_id=?',(pid,))
        for table in ORDER:
            if table in INDIRECT:
                foreign,parent=INDIRECT[table];parents=tables[parent];ids=[r.get('id',r.get('rule_id')) for r in parents]
                tables[table]=db.rows('SELECT * FROM '+table+' WHERE '+foreign+' IN ('+','.join('?' for _ in ids)+')',ids) if ids else []
        settings={key+pid:db.setting(key+pid) for key in ('format:','template:') if db.setting(key+pid) is not None}
        heads={s['id']:s['current_revision_id'] for s in tables['sections']}
    hashes={r['sha256'] for r in tables['files']}|{r['sha256'] for r in tables['artifacts'] if r['sha256']}
    for page in tables['pages']:
        for image in db.loads(page['images'],[]):
            if image.get('sha256'):hashes.add(image['sha256'])
    for profile in tables['project_formats']:
        if profile['template_sha256']:hashes.add(profile['template_sha256'])
    if settings.get('template:'+pid):hashes.add(settings['template:'+pid])
    blobs={};total=0
    for sha in sorted(hashes):
        path=security.safe_object(sha)
        if not path.is_file():raise ValueError('项目缺少必要的原始资源：'+sha)
        raw=path.read_bytes();total+=len(raw)
        if total>200*1024*1024:raise ValueError('当前同步包原件超过200MB，请分项目整理')
        blobs[sha]=base64.b64encode(raw).decode()
    return {'format':'paper-project-sync-v2','schema':3,'project_id':pid,'tables':tables,'blobs':blobs,'settings':settings,'heads':heads,'base_heads':base_heads or {},'record_heads':{record_key(t,r):record_hash(t,r) for t,rows in tables.items() if t in MUTABLE for r in rows},'base_records':db.setting('sync_records:'+pid,{}),'created_at':db.now(),'contains_credentials':False}

def validate(packet):
    if not isinstance(packet,dict):raise ValueError('同步包必须是对象')
    if packet.get('format')!='paper-project-sync-v2' or packet.get('schema')!=3:raise ValueError('同步包版本不受支持')
    pid=packet.get('project_id');tables=packet.get('tables',{});blobs=packet.get('blobs',{})
    if not isinstance(tables,dict):raise ValueError('同步表必须是对象')
    if not isinstance(pid,str) or not re.fullmatch('[0-9a-f]{32}',pid) or set(tables)-set(ORDER)-{'projects'}:raise ValueError('同步包项目或数据表无效')
    if not isinstance(tables,dict) or any(not isinstance(rows,list) for rows in tables.values()):raise ValueError('同步表必须是记录列表')
    if len(tables.get('projects',[]))!=1 or tables['projects'][0]['id']!=pid:raise ValueError('同步包项目标识不一致')
    if sum(len(rows) for rows in tables.values())>200000 or not isinstance(blobs,dict):raise ValueError('同步数据规模过大')
    primary={};ids={};rows_by_id={}
    for table,rows in tables.items():
        fields=db.rows('PRAGMA table_info('+table+')');names={f['name'] for f in fields};keys=[f['name'] for f in sorted(fields,key=lambda x:x['pk']) if f['pk']];primary[table]=keys;ids[table]={r['id'] for r in rows if 'id' in r};rows_by_id[table]={r['id']:r for r in rows if 'id' in r}
        for row in rows:
            if not isinstance(row,dict) or set(row)-names or any(not isinstance(v,(str,int,float,bytes,type(None))) for v in row.values()):raise ValueError('同步字段或类型无效')
            if 'id' in row and (not isinstance(row['id'],str) or not re.fullmatch('[0-9a-f]{32}',row['id'])):raise ValueError('同步对象ID必须是32位十六进制标识')
            if table=='files':
                from .parsers import SUPPORTED
                if row.get('extension') not in SUPPORTED:raise ValueError('同步文件类型不受支持')
            if 'project_id' in row and row['project_id']!=pid:raise ValueError('同步包含跨项目记录')
            if not all(k in row for k in keys):raise ValueError('同步记录缺少主键')
            old=db.one('SELECT * FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),tuple(row[k] for k in keys))
            if old and old.get('project_id',pid)!=pid:raise ValueError('同步主键已被其他项目使用')
            if old and any(old.get(k)!=row.get(k) for k in keys):raise ValueError('同步主键冲突')
            if old and table=='files' and any(old.get(k)!=row.get(k) for k in ('sha256','extension','size')):raise ValueError('文件同ID内容被替换，应导入新文件版本')
            if old and table=='sources' and any(old.get(k)!=row.get(k) for k in ('file_id','page_id','text','locator','method')):raise ValueError('来源同ID内容被替换，应建立新来源版本')
            if old and table not in MUTABLE and row!=old:
                raise ValueError('资源或历史版本与本机同ID记录不一致，请保留快照并人工处理：'+table)
    references={'child_file_id':'files','parent_file_id':'files','file_id':'files','page_id':'pages','artifact_id':'artifacts','fact_id':'facts','reference_id':'bibliography','section_id':'sections','revision_id':'revisions','source_id':'sources','rule_id':'rules','package_id':'rule_packages'}
    for table,rows in tables.items():
        for row in rows:
            for column,parent in references.items():
                value=row.get(column)
                if value and value not in ids.get(parent,set()):raise ValueError('同步资源引用不属于此项目：'+table+'.'+column)
            if table=='sections' and row.get('parent_id') and row['parent_id'] not in ids.get('sections',set()):raise ValueError('章节层级包含外部项目')
            if table=='sections':
                if any(sid not in ids.get('sections',set()) for sid in db.loads(row.get('dependencies','[]'),[])):raise ValueError('章节依赖包含外部ID')
                if row.get('current_revision_id')!=packet.get('heads',{}).get(row['id']):raise ValueError('章节头版本与快照不一致')
            if table=='facts':
                if any(sid not in ids.get('sources',set()) for sid in db.loads(row.get('source_ids','[]'),[])):raise ValueError('事实来源包含外部ID')
            if table=='citations':
                if any(sid not in ids.get('sources',set()) for sid in db.loads(row.get('source_ids','[]'),[])):raise ValueError('引文来源包含外部ID')
            if table=='fact_revisions':
                snapshot=db.loads(row.get('content'),{})
                if any(sid not in ids.get('sources',set()) for sid in db.loads(snapshot.get('source_ids','[]'),[])):raise ValueError('事实历史来源包含外部ID')
            if table=='dependencies':
                parent={'source':'sources','fact':'facts','section':'sections','reference':'bibliography','rule_package':'rule_packages','brief':'projects'}.get(row.get('upstream_type'))
                if not parent or row.get('upstream_id') not in ids.get(parent,set()):raise ValueError('依赖图包含跨项目或未登记资源')
            if table=='projects' and row.get('rule_package_id') and row['rule_package_id'] not in ids.get('rule_packages',set()):raise ValueError('项目引用了外部规则包')
            if table=='rule_packages':
                for rule in db.loads(row['rules'],[]):
                    if rule.get('source_id') and rule['source_id'] not in ids.get('sources',set()):raise ValueError('规则快照包含外部来源')
            if table=='artifacts':
                origin=db.loads(row.get('metadata'),{}).get('source_file_id')
                if origin and origin not in ids.get('files',set()):raise ValueError('产物依据包含外部原件')
            if table=='revisions':
                if row.get('parent_id'):
                    parent=rows_by_id.get('revisions',{}).get(row['parent_id'])
                    if not parent or parent['section_id']!=row['section_id']:raise ValueError('正文历史版本跨越章节')
                document=db.loads(row['document'],{});documents.normalize(document)
                for node in documents.nodes(document):
                    a=node.get('attrs',{})
                    for key,parent in [('sourceIds','sources'),('factIds','facts')]:
                        if any(v not in ids.get(parent,set()) for v in a.get(key,[])):raise ValueError('正文依据包含跨项目ID')
                    for key,parent in [('citationId','bibliography'),('artifactId','artifacts')]:
                        if a.get(key) and a[key] not in ids.get(parent,set()):raise ValueError('正文资源包含跨项目ID')
    raw={};total=0
    for sha,encoded in blobs.items():
        security.safe_object(sha)
        if not isinstance(encoded,str) or len(encoded)>180*1024*1024:raise ValueError('同步资源大小无效')
        try:value=base64.b64decode(encoded,validate=True)
        except Exception as e:raise ValueError('同步资源编码无效') from e
        total+=len(value)
        if total>200*1024*1024 or hashlib.sha256(value).hexdigest()!=sha:raise ValueError('同步资源哈希校验失败')
        raw[sha]=value
    needed={r['sha256'] for r in tables.get('files',[])}|{r['sha256'] for r in tables.get('artifacts',[]) if r['sha256']}
    for p in tables.get('pages',[]):needed|={x['sha256'] for x in db.loads(p['images'],[]) if x.get('sha256')}
    for k,v in packet.get('settings',{}).items():
        if k not in ('format:'+pid,'template:'+pid):raise ValueError('同步包不能携带全局设置或凭据')
        if k.startswith('template:') and v:needed.add(v)
    for profile in tables.get('project_formats',[]):
        if profile.get('template_sha256'):needed.add(profile['template_sha256'])
    if not needed<=set(raw):raise ValueError('同步包缺少原件或派生资源')
    return pid,tables,primary,raw

def apply(packet,actor='local',allow_new=False):
    from . import synchronization as sync
    pid,tables,primary,raw=validate(packet);existing=db.one('SELECT * FROM projects WHERE id=?',(pid,))
    if not existing and not allow_new:raise HTTPException(404,'同步项目尚未导入，请明确选择新项目导入')
    for sha,value in raw.items():security.put_blob(value)
    record_conflicts=[]
    old_heads={s['id']:s['current_revision_id'] for s in db.rows('SELECT * FROM sections WHERE project_id=?',(pid,))} if existing else {};conflicts=[];merged=[];metadata_pending=[]
    with db.transaction() as c:
        if not existing:
            row=tables['projects'][0].copy();row['rule_package_id']=None;insert(c,'projects',row);db.set_setting('sharing:'+pid,False)
        for table in (('projects',)+ORDER if existing else ORDER):
            for incoming in tables.get(table,[]):
                row=incoming.copy();keys=primary[table];old=c.execute('SELECT * FROM '+table+' WHERE '+' AND '.join(k+'=?' for k in keys),tuple(row[k] for k in keys)).fetchone()
                if old:
                    old=dict(old)
                    if table in MUTABLE and record_hash(table,old)!=record_hash(table,row):
                        key=record_key(table,row);base_record=packet.get('base_records',{}).get(key)
                        if record_hash(table,old)==base_record:
                            row=preserve_version_history(table,old,row,pid)
                            fields=[k for k in row if k not in keys and k not in SKIP.get(table,set())]
                            c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE '+' AND '.join(k+'=?' for k in keys),tuple(row[k] for k in fields)+tuple(row[k] for k in keys))
                        else:
                            cid=db.uid();c.execute('INSERT INTO sync_record_conflicts(id,project_id,table_name,key_json,local_row,remote_row,created_at) VALUES(?,?,?,?,?,?,?)',(cid,pid,table,db.dumps({k:row[k] for k in keys}),db.dumps(old),db.dumps(row),db.now()));record_conflicts.append(cid)
                    continue
                if table=='sections':row['current_revision_id']=None
                insert(c,table,row)
                if table=='sources':
                    from .parsers import tokens
                    c.execute('INSERT INTO source_fts VALUES(?,?)',(row['id'],tokens(row['text'])))
        for deleted,base_hash in packet.get('base_records',{}).items():
            if deleted in packet.get('record_heads',{}):continue
            table,separator,encoded=deleted.partition(':')
            if table not in ('terms','locks','rule_conditions') or not separator:continue
            values=db.loads(encoded,[]);keys=[f['name'] for f in sorted(db.rows('PRAGMA table_info('+table+')'),key=lambda x:x['pk']) if f['pk']]
            if not isinstance(values,list) or len(values)!=len(keys):raise ValueError('删除标记的主键无效')
            where=' AND '.join(k+'=?' for k in keys);old=db.one('SELECT * FROM '+table+' WHERE '+where,values)
            if not old:continue
            owner=old.get('project_id')
            if table=='locks':owner=security.require_section(old['section_id'])['project_id']
            if table=='rule_conditions':owner=db.one('SELECT project_id FROM rules WHERE id=?',(old['rule_id'],))['project_id']
            if owner!=pid:raise ValueError('删除标记指向其他项目')
            if record_hash(table,old)==base_hash:c.execute('DELETE FROM '+table+' WHERE '+where,values)
            else:
                cid=db.uid();c.execute('INSERT INTO sync_record_conflicts(id,project_id,table_name,key_json,local_row,remote_row,created_at) VALUES(?,?,?,?,?,?,?)',(cid,pid,table,db.dumps(dict(zip(keys,values))),db.dumps(old),db.dumps({'_deleted':True}),db.now()));record_conflicts.append(cid)
        for sid,head in packet.get('heads',{}).items():
            if sid not in {s['id'] for s in tables.get('sections',[])}:raise ValueError('同步章节头包含外部ID')
            if not head:continue
            revision=db.one('SELECT * FROM revisions WHERE id=? AND section_id=?',(head,sid))
            if not revision:raise ValueError('同步版本不属于指定章节')
            current=old_heads.get(sid)
            if current==head:continue
            base=packet.get('base_heads',{}).get(sid)
            if current is None or current==base:
                c.execute("UPDATE sections SET current_revision_id=?,state='draft',stale=1,stale_reason='同步内容已更新，请复查' WHERE id=?",(head,sid));merged.append(sid)
            else:
                result=sync.apply_operation(pid,actor,{'operation_id':'packet-'+hashlib.sha256((packet['created_at']+sid+head).encode()).hexdigest(),'section_id':sid,'base_revision_id':base,'document':db.loads(revision['document']), 'auto_merge':True})
                if result['status_code']==409:conflicts.append(result['conflict_id'])
                else:merged.append(sid)
        if not existing:
            rp=tables['projects'][0].get('rule_package_id')
            if rp and rp not in {x['id'] for x in tables.get('rule_packages',[])}:raise ValueError('规则包引用无效')
            c.execute('UPDATE projects SET rule_package_id=? WHERE id=?',(rp,pid))
            for k,v in packet.get('settings',{}).items():db.set_setting(k,v)
        db.set_setting('sync_base:'+pid,packet.get('heads',{}));db.set_setting('sync_records:'+pid,packet.get('record_heads',{}));sync.event(pid,actor,'packet_import',{'merged':merged,'conflicts':conflicts});db.audit(pid,'sync_import',{'new_project':not bool(existing),'objects':len(raw),'conflicts':conflicts})
    return {'project_id':pid,'new_project':not bool(existing),'sections_merged':merged,'conflicts':conflicts,'metadata_pending_review':metadata_pending,'record_conflicts':record_conflicts,'resources_verified':len(raw),'human_acceptance':False}

def insert(connection,table,row):
    columns=list(row);connection.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row[k] for k in columns))


def resolve_record(pid,cid,body):
    with db.transaction() as c:
        row=db.one("SELECT * FROM sync_record_conflicts WHERE id=? AND project_id=? AND state='open'",(cid,pid))
        if not row:raise HTTPException(404,'资料或规则冲突不存在')
        table=row['table_name'];key=db.loads(row['key_json']);remote=db.loads(row['remote_row']);where=' AND '.join(k+'=?' for k in key);old=db.one('SELECT * FROM '+table+' WHERE '+where,tuple(key.values()))
        if not old or record_hash(table,old)!=body.get('expected_local_hash'):raise HTTPException(409,'本机记录再次变化，请重新比较')
        if body.get('choice')=='take_remote' and remote.get('_deleted'):
            if table not in ('terms','locks','rule_conditions'):raise ValueError('该类研究资源不能由同步包物理删除')
            c.execute('DELETE FROM '+table+' WHERE '+where,tuple(key.values()))
        elif body.get('choice')=='take_remote':
            remote=preserve_version_history(table,old,remote,pid)
            fields=[k for k in remote if k not in key and k not in SKIP.get(table,set())];c.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE '+where,tuple(remote[k] for k in fields)+tuple(key.values()))
        elif body.get('choice')!='keep_local':raise HTTPException(422,'请选择保留当前记录或采用远端记录')
        c.execute("UPDATE sync_record_conflicts SET state='resolved' WHERE id=?",(cid,));db.audit(pid,'sync_record_resolved',{'conflict_id':cid,'choice':body['choice']})
        return {'resolved':True}
