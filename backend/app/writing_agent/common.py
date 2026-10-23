import hashlib,json
from fastapi import HTTPException
from .. import db,security

def digest(value):
    raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(raw).hexdigest()

def blob(value):return security.put_blob(db.dumps(value).encode('utf-8'))
def unblob(sha):return json.loads(security.safe_object(sha).read_text(encoding='utf-8'))
def fail(code,message,status=422,**details):raise HTTPException(status,{'code':code,'message':message,**details})
TABLES={'repository':'repository_bindings','snapshot':'repository_snapshots','evidence':'evidence_items','claim':'claim_versions','core':'thesis_core_versions','contract':'section_contract_versions','session':'writing_sessions','profile':'writing_profiles','analysis':'document_analyses','diagnostic':'diagnostic_runs'}

def owned(kind,identity,pid):
    security.require_project(pid)
    if kind not in TABLES:fail('INVALID_TYPE','未知对象类型')
    row=db.one('SELECT * FROM '+TABLES[kind]+' WHERE id=? AND project_id=?',(identity,pid))
    if not row:fail('NOT_FOUND','对象不存在或不属于当前项目',404)
    return row

def candidate(identity,pid):
    row=db.one('SELECT c.* FROM writing_candidates c JOIN writing_sessions s ON s.id=c.session_id WHERE c.id=? AND s.project_id=?',(identity,pid))
    if not row:fail('NOT_FOUND','候选不存在或不属于当前项目',404)
    return row

def section(identity,pid):
    row=security.require_section(identity)
    if row['project_id']!=pid:fail('NOT_FOUND','章节不属于当前项目',404)
    return row

def state(pid):
    security.require_project(pid)
    db.execute('INSERT OR IGNORE INTO writing_project_state(project_id) VALUES(?)',(pid,))
    r=db.one('SELECT * FROM writing_project_state WHERE project_id=?',(pid,))
    return {**r,'contracts':db.loads(r['contract_heads_json'],{}),'policy':db.loads(r['policy_json'],{})}

def update_state(pid,expected,**fields):
    allowed={'core_version_id','target_snapshot_id','contract_heads_json','policy_json'}
    if set(fields)-allowed:fail('INVALID_STATE','状态字段无效')
    with db.transaction() as c:
        old=state(pid)
        if old['revision']!=expected:fail('INPUT_VERSION_CHANGED','项目状态已变化',409,current=old)
        assignments=','.join(k+'=?' for k in fields)
        c.execute('UPDATE writing_project_state SET '+assignments+',revision=revision+1 WHERE project_id=? AND revision=?',(*fields.values(),pid,expected))
    return state(pid)

def dependency(pid,up_type,up_id,up_version,down_type,down_id,down_version,relation='uses'):
    if up_type in TABLES:owned(up_type,up_id,pid)
    elif up_type=='fact':
        if not db.one('SELECT id FROM facts WHERE id=? AND project_id=?',(up_id,pid)):fail('INVALID_DEPENDENCY','事实不属于本项目')
    elif up_type=='revision':
        if not db.one('SELECT r.id FROM revisions r JOIN sections s ON s.id=r.section_id WHERE r.id=? AND s.project_id=?',(up_id,pid)):fail('INVALID_DEPENDENCY','正文不属于本项目')
    db.execute('INSERT OR IGNORE INTO object_dependencies VALUES(?,?,?,?,?,?,?,?)',(pid,up_type,up_id,str(up_version),down_type,down_id,str(down_version),relation))
