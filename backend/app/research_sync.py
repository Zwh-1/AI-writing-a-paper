"""Version-aware research record merge on a shared project baseline.

Originals, parses, revisions and assets must already exist with the same identities,
usually established by a complete knowledge bundle. Packets never overwrite them.
"""
import hashlib,json,re
from fastapi import HTTPException
from . import db, security, research_records as records, resource_refs as refs

def fingerprint(row):
    sources=row.get('source_refs')
    if sources is None:
        sources=[db.loads(link['source_json'],{}) for link in db.rows('SELECT source_json FROM research_links WHERE record_id=? AND record_revision=?',(row['id'],row['revision']))]
    return refs.digest({**{key:row.get(key) for key in ('id','kind','owner_id','revision','data','deleted_at')},
        'source_refs':sorted({json.dumps(source,sort_keys=True,ensure_ascii=False) for source in sources})})


@db.atomic
def snapshot(pid, base=None):
    security.require_project(pid)
    from .research_relations import backfill
    backfill(pid)
    rows=db.rows('SELECT * FROM studio_records WHERE project_id=? ORDER BY created_at,id',(pid,))
    for row in rows:
        row['source_refs']=[db.loads(link['source_json'],{}) for link in db.rows('SELECT source_json FROM research_links WHERE record_id=? AND record_revision=?',(row['id'],row['revision']))]
        row['versions']=db.rows('SELECT revision,data,created_at FROM studio_versions WHERE record_id=? ORDER BY revision',(row['id'],))
    hashes={row['id']:fingerprint(row) for row in rows}
    return {'format':'research-sync.v1','project_id':pid,'base':base or {},'heads':hashes,'records':[r for r in rows if (base or {}).get(r['id'])!=hashes[r['id']]],
        'merge_policy':{'messages':'append_only','editable':'optimistic_versions','deletion':'tombstone','missing_originals':'require_shared_baseline'},'credentials_included':False}


def check(pid, packet):
    security.require_project(pid)
    if not isinstance(packet,dict) or packet.get('format')!='research-sync.v1' or packet.get('project_id')!=pid or not isinstance(packet.get('records'),list) or len(packet['records'])>100000:
        raise HTTPException(422,'研究同步包类型、项目或规模无效')
    if not isinstance(packet.get('base',{}),dict) or len(db.dumps(packet).encode())>20*1024*1024:raise HTTPException(422,'研究同步基线或文件体积无效')
    seen=set()
    for row in packet['records']:
        if not isinstance(row,dict) or row.get('project_id')!=pid or not isinstance(row.get('id'),str) or row['id'] in seen or type(row.get('revision')) is not int or row['revision']<1:
            raise HTTPException(422,'研究同步记录无效')
        if any(not isinstance(row.get(key),str) for key in ('kind','owner_id','data','created_at','updated_at')) or row.get('deleted_at') is not None and not isinstance(row['deleted_at'],str):
            raise HTTPException(422,'研究同步字段类型无效')
        if not re.fullmatch('[0-9a-f]{32}',row['id']):raise HTTPException(422,'研究记录标识无效')
        if len(row['data'].encode())>2_000_000 or len(row['owner_id'])>160:raise HTTPException(422,'研究同步内容过大')
        if row.get('schema_version',1)!=1:raise HTTPException(422,'研究记录版本不受支持')
        if not isinstance(row.get('source_refs',[]),list):raise HTTPException(422,'来源关系必须为列表')
        seen.add(row['id'])
        from .research_contracts import validate_record
        try:data=json.loads(row['data'])
        except ValueError as error:raise HTTPException(422,'研究记录JSON无效') from error
        validate_record(row.get('kind'),data)
        local=db.one('SELECT project_id,kind,owner_id FROM studio_records WHERE id=?',(row['id'],))
        if local and local['project_id']!=pid:raise HTTPException(422,'记录标识已属于其他项目')
        if local and (row['kind']!=local['kind'] or row['owner_id']!=local['owner_id']):raise HTTPException(422,'同步不能改变现有记录的类型或所属对象')
    # Validate links against local originals and incoming record identities. Do not invent missing baselines.
    for row in packet['records']:
        if row['owner_id']:
            data=json.loads(row['data'])
            owner_type='conversation' if row['kind']=='message' else 'revision_owner' if row['kind'] in ('comment','proposal') else 'reference' if row['kind']=='translation' and data.get('reference_id') else 'file'
            if owner_type=='conversation' and row['owner_id'] in seen:
                if next(r for r in packet['records'] if r['id']==row['owner_id'])['kind']!='conversation':raise HTTPException(422,'消息所属对象必须是会话')
            elif owner_type=='revision_owner':
                if not db.one('SELECT id FROM sections WHERE id=? AND project_id=?',(row['owner_id'],pid)):raise HTTPException(409,'缺少共同章节基线')
            else:refs.require_owned(pid,owner_type,row['owner_id'])
        for ref in row.get('source_refs',[]):
            if not isinstance(ref,dict) or not all(isinstance(ref.get(key),str) and ref[key] for key in ('type','id','version')) or ref.get('project_id',pid)!=pid:
                raise HTTPException(422,'同步来源关系无效')
            if ref.get('type') in refs.RECORD_TYPES and ref.get('id') in seen:continue
            table={'unit':'knowledge_units','file':'files','source':'sources','reference':'bibliography','revision':'revisions','asset':'artifacts'}.get(ref.get('type'),'studio_records' if ref.get('type') in refs.RECORD_TYPES else None)
            if table and not db.one('SELECT id FROM '+table+' WHERE id=?',(ref.get('id'),)):
                raise HTTPException(409,'原件或正文基线尚未导入，请先使用完整知识包建立共同项目')
            refs.validate(pid,[ref],allow_stale=True)
    return packet


@db.atomic
def apply(pid, packet):
    packet=check(pid,packet);packet_hash=refs.digest(packet)
    old=db.one('SELECT result_json FROM research_sync_receipts WHERE project_id=? AND packet_hash=?',(pid,packet_hash))
    if old:return {**db.loads(old['result_json'],{}),'idempotent_replay':True}
    merged=[];conflicts=[]
    incoming={row['id']:row for row in packet['records']}
    # Conversations precede messages; remaining references are rebound after every row is present.
    for row in sorted(packet['records'],key=lambda r:(r['kind']=='message',r['created_at'],r['id'])):
        identity=row['id'];local=db.one('SELECT * FROM studio_records WHERE id=?',(identity,))
        remote_hash=fingerprint(row)
        if local and fingerprint(local)==remote_hash:continue
        safe=local is None or row['kind']!='message' and packet.get('base',{}).get(identity)==fingerprint(local)
        if not safe:
            conflict_id=db.uid()
            db.execute('INSERT INTO research_sync_conflicts VALUES(?,?,?,?,?,?,?,?,?)',(conflict_id,pid,identity,db.dumps(local),db.dumps(row),fingerprint(local),'open',None,db.now()))
            conflicts.append(conflict_id);continue
        _put(pid,row,local)
        merged.append(identity)
    from .research_relations import bind
    for identity in merged:
        row=records.get(pid,identity,include_deleted=True)
        if not row['deleted_at']:
            data={**row['data'],'source_refs':incoming[identity].get('source_refs',[])}
            bind(pid,{**row,'data':data},legacy=True)
    result={'merged':merged,'conflicts':conflicts,'histories_preserved':True,'baseline_required':True}
    db.execute('INSERT INTO research_sync_receipts VALUES(?,?,?,?,?)',(db.uid(),pid,packet_hash,db.dumps(result),db.now()))
    return result


def _put(pid,row,local):
    if local:
        db.execute('INSERT OR IGNORE INTO studio_versions VALUES(?,?,?,?,?)',(db.uid(),local['id'],local['revision'],local['data'],db.now()))
        revision=max(local['revision']+1,row['revision'])
        db.execute('UPDATE studio_records SET data=?,revision=?,updated_at=?,deleted_at=? WHERE id=?',(row['data'],revision,db.now(),row.get('deleted_at'),row['id']))
    else:
        columns=('id','project_id','kind','owner_id','revision','data','created_at','updated_at','deleted_at','schema_version')
        db.execute('INSERT INTO studio_records('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row.get(key,1 if key=='schema_version' else None) for key in columns))
    branch={key:row.get(key) for key in ('id','revision','data','versions','source_refs','deleted_at')}
    db.execute('INSERT OR IGNORE INTO research_sync_history VALUES(?,?,?,?,?,?)',(db.uid(),pid,row['id'],refs.digest(branch),db.dumps(branch),db.now()))
    from .knowledge_lineage import changed
    changed(pid,row['kind'],row['id'],'研究记录已同步，派生内容需复查')


def conflicts(pid):
    security.require_project(pid)
    return [{**row,'local':db.loads(row['local_json'],{}),'remote':db.loads(row['remote_json'],{})} for row in db.rows("SELECT * FROM research_sync_conflicts WHERE project_id=? AND state='open' ORDER BY created_at,id",(pid,))]


@db.atomic
def resolve(pid, identity, body):
    security.require_project(pid)
    conflict=db.one("SELECT * FROM research_sync_conflicts WHERE id=? AND project_id=? AND state='open'",(identity,pid))
    if not conflict:raise HTTPException(404,'同步冲突不存在')
    if body.get('choice') not in ('local','remote','copy_remote'):raise HTTPException(422,'请选择保留本地、采用远端或另存远端')
    local=db.one('SELECT * FROM studio_records WHERE id=?',(conflict['record_id'],))
    if not local or fingerprint(local)!=conflict['local_hash'] or body.get('local_hash')!=conflict['local_hash']:raise HTTPException(409,'本地记录已再次变化，请重新比较')
    remote=db.loads(conflict['remote_json'],{})
    if remote['kind']=='message' and body['choice']=='remote':raise HTTPException(409,'消息为追加记录，请保留本地或另存远端')
    if body['choice']!='local':
        if body['choice']=='copy_remote':remote={**remote,'id':db.uid(),'revision':1};local=None
        _put(pid,remote,local)
        from .research_relations import bind
        row=records.get(pid,remote['id'],include_deleted=True)
        if not row['deleted_at']:bind(pid,{**row,'data':{**row['data'],'source_refs':remote.get('source_refs',[])}},legacy=True)
    db.execute("UPDATE research_sync_conflicts SET state='resolved',resolution=? WHERE id=?",(body['choice'],identity))
    return {'resolved':identity,'choice':body['choice'],'record_id':remote['id'] if body['choice']!='local' else local['id']}
