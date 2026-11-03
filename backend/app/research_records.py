"""Project-owned, versioned research objects shared by the studio and editor."""
import hashlib
from fastapi import HTTPException
from . import db, security

KINDS={'note','agent','conversation','message','translation','annotation','comment','canvas','webpage','parse_result','search','deletion','proposal','guide'}

def digest(value):
    return hashlib.sha256(db.dumps(value).encode('utf-8')).hexdigest()

def require_file(pid,fid,remote=False):
    from .source_access import require_file as require
    return require(pid,fid,remote)

def require_reference(pid,rid):
    security.require_project(pid)
    row=db.one('SELECT * FROM bibliography WHERE id=? AND project_id=?',(rid,pid))
    if not row:raise HTTPException(404,'文献不属于当前项目')
    return {**row,'metadata':db.loads(row['metadata'],{})}

def unpack(row):
    if not row:return None
    from .research_relations import hydrate
    return hydrate(row['project_id'],{**row,'data':db.loads(row['data'],{})})

def get(pid,identity,kind=None,include_deleted=False):
    security.require_project(pid)
    row=db.one('SELECT * FROM studio_records WHERE id=? AND project_id=?',(identity,pid))
    if not row or kind and row['kind']!=kind or row['deleted_at'] and not include_deleted:raise HTTPException(404,'研究记录不存在或不属于当前项目')
    return unpack(row)

def list_records(pid,kind,owner=None,limit=200,query='',offset=0):
    security.require_project(pid)
    if kind not in KINDS:raise HTTPException(422,'记录类型无效')
    args=[pid,kind];where='project_id=? AND kind=? AND deleted_at IS NULL'
    if owner is not None:where+=' AND owner_id=?';args.append(owner)
    if query:where+=" AND data LIKE ? ESCAPE '\\'";args.append('%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
    if not 0<=int(offset)<=1000000:raise HTTPException(422,'分页位置无效')
    args.extend([min(500,max(1,int(limit))),int(offset)])
    return [unpack(row) for row in reversed(db.rows('SELECT * FROM studio_records WHERE '+where+' ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?',args))]

@db.atomic
def save(pid,kind,data,identity=None,owner='',expected_revision=None):
    security.require_project(pid)
    if kind not in KINDS or not isinstance(data,dict) or len(db.dumps(data).encode())>2_000_000:raise HTTPException(422,'研究记录类型或内容无效')
    if len(owner)>160:raise HTTPException(422,'所属对象标识过长')
    from .research_contracts import validate_record
    data=validate_record(kind,data)
    if identity:
        old=get(pid,identity,kind)
        if expected_revision is None or old['revision']!=expected_revision:raise HTTPException(409,'内容已更新，请重新读取后保存')
        db.execute('INSERT INTO studio_versions VALUES(?,?,?,?,?)',(db.uid(),identity,old['revision'],db.dumps(old['data']),db.now()))
        db.execute('UPDATE studio_records SET data=?,revision=revision+1,updated_at=? WHERE id=?',(db.dumps(data),db.now(),identity))
    else:
        identity=db.uid();stamp=db.now()
        db.execute('INSERT INTO studio_records(id,project_id,kind,owner_id,data,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(identity,pid,kind,owner,db.dumps(data),stamp,stamp))
    from .research_relations import bind
    bind(pid,get(pid,identity,kind))
    if expected_revision is not None:
        from .knowledge_lineage import changed
        changed(pid,kind,identity,'研究记录版本已变化，相关派生内容需复查')
    return get(pid,identity,kind)

@db.atomic
def remove(pid,identity,expected_revision):
    row=get(pid,identity)
    if row['revision']!=expected_revision:raise HTTPException(409,'记录已更新')
    db.execute('UPDATE studio_records SET deleted_at=?,updated_at=? WHERE id=?',(db.now(),db.now(),identity))
    from .knowledge_lineage import changed
    changed(pid,row['kind'],identity,'研究记录已删除，关联内容需复查')
    db.audit(pid,'studio_record_removed',{'id':identity,'kind':row['kind']})
    return {'id':identity,'deleted':True}


class ResearchRepository:
    def __init__(self, kind):
        if kind not in KINDS:raise ValueError('Unknown research kind')
        self.kind=kind
    def get(self,pid,identity):return get(pid,identity,self.kind)
    def list(self,pid,**options):return list_records(pid,self.kind,**options)
    def save(self,pid,data,identity=None,owner='',expected_revision=None):return save(pid,self.kind,data,identity,owner,expected_revision)


notes=ResearchRepository('note')
canvases=ResearchRepository('canvas')
conversations=ResearchRepository('conversation')

def versions(pid,identity):
    record=get(pid,identity)
    return [{'revision':record['revision'],'data':record['data'],'created_at':record['updated_at'],'current':True}]+[
        {**row,'data':db.loads(row['data'],{}),'current':False} for row in db.rows('SELECT revision,data,created_at FROM studio_versions WHERE record_id=? ORDER BY revision DESC',(identity,))]


def sync_history(pid,identity):
    get(pid,identity)
    return [{**row,'snapshot':db.loads(row['snapshot_json'],{})} for row in db.rows('SELECT * FROM research_sync_history WHERE project_id=? AND record_id=? ORDER BY created_at',(pid,identity))]

def restore(pid,identity,target_revision,expected_revision):
    record=get(pid,identity)
    if record['kind'] not in ('note','agent','canvas'):raise HTTPException(422,'该类记录不支持覆盖恢复')
    target=next((item for item in versions(pid,identity) if item['revision']==target_revision),None)
    if target is None:raise HTTPException(404,'历史版本不存在')
    return save(pid,record['kind'],target['data'],identity,record['owner_id'],expected_revision)


@db.atomic
def restore_sync(pid,identity,history_id,target_revision,expected_revision):
    record=get(pid,identity)
    if record['kind'] not in ('note','agent','canvas'):raise HTTPException(422,'该记录不能从远端历史恢复')
    history=db.one('SELECT * FROM research_sync_history WHERE id=? AND record_id=? AND project_id=?',(history_id,identity,pid))
    if not history:raise HTTPException(404,'远端历史分支不存在')
    branch=db.loads(history['snapshot_json'],{})
    target=branch if branch.get('revision')==target_revision else next((row for row in branch.get('versions',[]) if row['revision']==target_revision),None)
    if target is None:raise HTTPException(404,'远端历史版本不存在')
    data=db.loads(target.get('data'),None) if isinstance(target.get('data'),str) else target.get('data')
    from .research_commands import update_record
    restored=update_record(pid,identity,{'revision':expected_revision,'data':data})
    from .research_relations import bind
    bind(pid,restored,legacy=True)
    db.audit(pid,'research_remote_history_restored',{'record_id':identity,'history_id':history_id,'target_revision':target_revision,'new_revision':restored['revision']})
    return get(pid,identity)

def read_secret(name):
    from .credential_service import read_secret as read
    return read(name)

def write_secret(name,value):
    from .credential_service import write_secret as write
    return write(name,value)

def file_units(pid,fid,remote=False):
    from .content_view import file_units as read
    return read(pid,fid,remote)
