"""Versioned provenance. Every use rechecks originals, including cached summaries."""
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from fastapi import HTTPException
from . import db, security, knowledge_units as units, material_policy as policy

_CACHE = ContextVar('knowledge_validation', default=None)
TABLES = {'observation':'knowledge_observations','entity':'kg_entities','entity_version':'kg_entity_versions',
          'assertion':'kg_assertions','wiki_page':'wiki_pages','wiki_version':'wiki_versions','artifact':'knowledge_artifacts',
          'release':'knowledge_releases','comparison':'knowledge_comparisons','wiki_block':'wiki_blocks'}

def digest(value):
    return hashlib.sha256(db.dumps(value).encode('utf-8')).hexdigest()

def fail(code, message, status=409):
    raise HTTPException(status, {'code':code,'message':message})

@contextmanager
def validation_scope(fresh=False):
    if _CACHE.get() is not None and not fresh:
        yield
        return
    token=_CACHE.set({})
    try:
        with db.read_session(),units.cache_reads(fresh=fresh):yield
    finally: _CACHE.reset(token)

def owned(pid, kind, identity):
    security.require_project(pid)
    if kind not in TABLES: fail('INVALID_KNOWLEDGE_TYPE','知识对象类型无效',422)
    if kind=='wiki_block':
        row=db.one('SELECT b.*,v.project_id,v.page_id FROM wiki_blocks b JOIN wiki_versions v ON v.id=b.version_id WHERE b.id=? AND v.project_id=?',(identity,pid))
        if not row:fail('KNOWLEDGE_NOT_FOUND','Wiki块不属于当前项目',404)
        return row
    key='claim_version_id' if kind=='assertion' else 'id'
    row=db.one(f'SELECT * FROM {TABLES[kind]} WHERE {key}=? AND project_id=?',(identity,pid))
    if not row: fail('KNOWLEDGE_NOT_FOUND','知识对象不属于当前项目',404)
    return row

def file_stamp(pid, fid):
    cache=_CACHE.get(); key=('file',pid,fid)
    if cache is not None and key in cache:return cache[key]
    original=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(fid,pid))
    if not original:fail('SOURCE_MISSING','原件不存在',404)
    head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE h.file_id=?',(fid,))
    stamp=digest([original['sha256'],original['active'],head['id'] if head else None,units.input_stamp(original),policy.usages(original),policy.source_kind(original)])
    result=(original,head,stamp)
    if cache is not None:cache[key]=result
    return result

def references(pid, kind, identity):
    if kind=='wiki_block':
        owned(pid,kind,identity)
        return [{'type':r['target_type'],'id':r['target_id'],'version':r['target_version']} for r in db.rows('SELECT * FROM wiki_links WHERE project_id=? AND block_id=? ORDER BY rowid',(pid,identity))]
    return [{'type':r['upstream_type'],'id':r['upstream_id'],'version':r['upstream_version']} for r in db.rows(
        'SELECT * FROM knowledge_dependencies WHERE project_id=? AND downstream_type=? AND downstream_id=? ORDER BY upstream_type,upstream_id', (pid,kind,identity))]

def descendants_of_files(pid,file_ids):
    """Scope navigation to actual provenance, never filename similarity."""
    if not isinstance(file_ids,list) or len(file_ids)>100 or any(not isinstance(fid,str) for fid in file_ids):fail('INVALID_SOURCE_SCOPE','原件范围无效',422)
    if not file_ids:return None
    ids=list(dict.fromkeys(file_ids));found=db.rows('SELECT id FROM files WHERE project_id=? AND id IN ('+','.join('?' for _ in ids)+')',[pid,*ids])
    if {r['id'] for r in found}!=set(ids):fail('INVALID_SOURCE_SCOPE','原件范围包含其他项目或不存在的资料',404)
    rows=db.rows("WITH RECURSIVE affected(kind,identity) AS (SELECT 'file',id FROM files WHERE project_id=? AND id IN ("+','.join('?' for _ in ids)+") UNION SELECT d.downstream_type,d.downstream_id FROM knowledge_dependencies d JOIN affected a ON a.kind=d.upstream_type AND a.identity=d.upstream_id WHERE d.project_id=?) SELECT kind,identity FROM affected",[pid,*ids,pid])
    result={}
    for row in rows:result.setdefault(row['kind'],set()).add(row['identity'])
    return result

def version_of(pid,kind,identity,remote=False,seen=None):
    cache=_CACHE.get();key=('version',pid,kind,identity,remote)
    if (kind,identity) in (seen or set()):fail('KNOWLEDGE_CYCLE','知识来源依赖出现循环')
    if cache is not None and ('failure',key) in cache:
        status,detail=cache[('failure',key)];raise HTTPException(status,detail)
    if cache is not None and key in cache:return cache[key]
    try:value=_version_of(pid,kind,identity,remote,seen)
    except HTTPException as error:
        if cache is not None and (not isinstance(error.detail,dict) or error.detail.get('code')!='KNOWLEDGE_CYCLE'):cache[('failure',key)]=(error.status_code,error.detail)
        raise
    if cache is not None:cache[key]=value
    return value

def _version_of(pid, kind, identity, remote=False, seen=None):
    seen=set() if seen is None else seen
    key=(kind,identity)
    if key in seen:fail('KNOWLEDGE_CYCLE','知识来源依赖出现循环')
    seen=seen|{key}
    if kind=='file':
        original,head,stamp=file_stamp(pid,identity)
        if not original['active']:fail('SOURCE_STALE','原件已归档')
        if remote and not security.file_allows_remote(identity,pid):fail('SOURCE_ACCESS_REVOKED','派生来源禁止外发',403)
        return stamp
    if kind=='unit':
        row=db.one('SELECT u.*,p.file_id,p.project_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(identity,pid))
        if not row:fail('SOURCE_MISSING','原文单元不存在',404)
        original,head,stamp=file_stamp(pid,row['file_id'])
        if not head or head['id']!=row['parse_id'] or not units.parse_current(head,original) or row['review_state']=='excluded':fail('SOURCE_STALE','原文版本已变更或排除')
        locator=db.loads(row['locator'],{}); pages={locator.get('page',1)}|{s.get('locator',{}).get('page') for s in locator.get('segments',[])}
        if any(p['page_number'] in pages for p in db.rows("SELECT page_number FROM pages WHERE file_id=? AND review_state='not_applicable'",(row['file_id'],))):fail('SOURCE_STALE','原页已排除')
        if remote and not security.file_allows_remote(row['file_id'],pid):fail('SOURCE_ACCESS_REVOKED','原文禁止外发',403)
        return row['content_hash']
    if kind=='evidence':
        raw=db.one('SELECT * FROM evidence_items WHERE id=? AND project_id=?',(identity,pid))
        if raw and raw['origin_type']=='knowledge_unit':
            locator=db.loads(raw['locator_json'],{});start=locator.get('start');end=locator.get('end')
            if type(start) is int and type(end) is int and locator.get('before',1)==0 and locator.get('after',1)==0:
                unit=db.one('SELECT u.*,p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(raw['origin_id'],pid))
                if not unit or raw['validity'] not in ('current','historical'):fail('SOURCE_STALE','证据已失效')
                unit_hash=version_of(pid,'unit',unit['id'],remote,seen);original,_,_=file_stamp(pid,unit['file_id'])
                if not policy.source_context(original,locator.get('intent','research'))['allowed_for_intent']:fail('SOURCE_PURPOSE_CHANGED','证据用途已变化')
                expected=digest([unit['parse_id'],unit_hash,original['sha256'],'explicit_partial_range',unit['text'][start:end]]) if 0<=start<end<=len(unit['text']) else None
                if expected!=raw['origin_version']:fail('SOURCE_STALE','证据读取范围或原文已变化')
                return raw['origin_version']
        from .writing_agent import evidence
        item=evidence.get(pid,identity,remote)
        if not item['version_current'] or item['validity'] not in ('current','historical'):fail('SOURCE_STALE','证据版本失效')
        if item['origin_type']=='code':
            head=db.one('SELECT target_snapshot_id FROM writing_project_state WHERE project_id=?',(pid,))
            if head and head['target_snapshot_id'] and head['target_snapshot_id']!=item['origin_id']:fail('SOURCE_STALE','代码观察不属于当前项目目标快照')
        return item['origin_version']
    row=owned(pid,kind,identity)
    if kind=='observation':
        if row['file_id']:
            original,_,_=file_stamp(pid,row['file_id'])
            if not policy.source_context(original,row['intent'])['allowed_for_intent']:fail('SOURCE_PURPOSE_CHANGED','原件用途已变化')
        version=row['content_hash']
    elif kind=='entity':
        if row['redirect_id']:fail('ENTITY_REDIRECT','实体已归并，请读取归并记录')
        return version_of(pid,'entity_version',row['current_version_id'],remote,seen)
    elif kind=='entity_version':
        head=owned(pid,'entity',row['entity_id'])
        if head['current_version_id']!=identity or head['redirect_id']:fail('KNOWLEDGE_STALE','实体描述版本已变更')
        version=row['content_hash']
    elif kind=='assertion':
        if row['lifecycle'] in ('stale','excluded','superseded'):fail('KNOWLEDGE_STALE','图谱论断已失效')
        assessments=db.rows('SELECT id,support,method,source_version FROM kg_support_assessments WHERE claim_version_id=? ORDER BY created_at,id',(identity,))
        decisions=db.rows('SELECT id,assessment_fingerprint,support FROM kg_support_decisions WHERE claim_version_id=? ORDER BY created_at,id',(identity,))
        version=digest([row['content_hash'],assessments,decisions]) if decisions else digest([row['content_hash'],assessments]) if assessments else row['content_hash']
    elif kind=='wiki_block':
        page=owned(pid,'wiki_page',row['page_id'])
        current=db.one('SELECT * FROM wiki_blocks WHERE version_id=? AND block_key=?',(page['current_version_id'],row['block_key']))
        if not current or block_hash(pid,current)!=block_hash(pid,row):fail('KNOWLEDGE_STALE','已使用的Wiki内容块已改变')
        version=block_hash(pid,row)
    elif kind=='wiki_page':
        if not row['current_version_id']:fail('WIKI_UNPUBLISHED','页面尚未发布')
        return version_of(pid,'wiki_version',row['current_version_id'],remote,seen)
    elif kind=='wiki_version':
        page=owned(pid,'wiki_page',row['page_id'])
        if page['current_version_id']!=identity or row['state']!='published':fail('KNOWLEDGE_STALE','Wiki版本已改变或过期')
        version=row['content_hash']
    else:
        if row.get('state') in ('stale','excluded','failed'):fail('KNOWLEDGE_STALE','派生产物已过期')
        version=row.get('content_hash',identity)
    refs=references(pid,kind,identity)
    if kind in ('observation','assertion','entity_version') and not refs:fail('PROVENANCE_MISSING','知识对象缺少原始来源')
    for ref in refs:
        if version_of(pid,ref['type'],ref['id'],remote,seen)!=ref['version']:fail('KNOWLEDGE_STALE','知识依赖的原文或版本已变化')
    return version

def block_hash(pid,row):
    return digest([row['block_key'],row['kind'],row['text'],db.loads(row['metadata_json'],{}),references(pid,'wiki_block',row['id'])])

def validate_refs(pid, refs, remote=False):
    if not isinstance(refs,list) or len(refs)>10000:fail('INVALID_PROVENANCE','来源列表无效',422)
    with validation_scope():
        for ref in refs:
            if not isinstance(ref,dict) or set(ref)-{'type','id','version'} or not all(isinstance(ref.get(k),str) and ref[k] for k in ('type','id','version')):fail('INVALID_PROVENANCE','来源需要类型、ID和版本',422)
            if version_of(pid,ref['type'],ref['id'],remote)!=ref['version']:fail('KNOWLEDGE_STALE','所选来源版本已变化')

def ref(pid, kind, identity, remote=False):
    return {'type':kind,'id':identity,'version':version_of(pid,kind,identity,remote)}

def add_dependencies(pid, kind, identity, version, refs):
    validate_refs(pid,refs)
    for upstream in refs:
        if upstream['type']==kind and upstream['id']==identity:fail('KNOWLEDGE_CYCLE','知识对象不能引用自身')
        # A new immutable version can only depend on already valid objects.
        db.execute('INSERT OR IGNORE INTO knowledge_dependencies VALUES(?,?,?,?,?,?,?,?,?)',
            (db.uid(),pid,kind,identity,version,upstream['type'],upstream['id'],upstream['version'],'derived_from'))

def trace(pid, kind, identity, remote=False, limit=250):
    version_of(pid,kind,identity,remote)
    queue=[(kind,identity)]; seen=set(); edges=[]; nodes=[]
    while queue and len(seen)<limit:
        typ,uid=queue.pop(0)
        if (typ,uid) in seen:continue
        seen.add((typ,uid));nodes.append({'type':typ,'id':uid})
        for upstream in references(pid,typ,uid):
            edges.append({'from':{'type':typ,'id':uid},'to':upstream});queue.append((upstream['type'],upstream['id']))
    return {'nodes':nodes,'edges':edges,'complete':not queue,'scope':'actual_dependency_paths'}

def source_files(pid, refs):
    queue=[(r['type'],r['id']) for r in refs];seen=set();files=set()
    while queue:
        typ,identity=queue.pop()
        if (typ,identity) in seen:continue
        seen.add((typ,identity))
        if typ=='file':files.add(identity)
        elif typ=='unit':
            row=db.one('SELECT p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(identity,pid))
            if row:files.add(row['file_id'])
        elif typ=='evidence':
            e=db.one('SELECT origin_type,origin_id FROM evidence_items WHERE id=? AND project_id=?',(identity,pid))
            if e:
                if e['origin_type']=='knowledge_unit':
                    u=db.one('SELECT p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(e['origin_id'],pid))
                    if u:files.add(u['file_id'])
                elif e['origin_type']=='source':
                    u=db.one('SELECT s.file_id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(e['origin_id'],pid))
                    if u:files.add(u['file_id'])
                elif e['origin_type']=='document_reading':
                    u=db.one('SELECT file_id FROM document_readings WHERE id=? AND project_id=?',(e['origin_id'],pid))
                    if u:files.add(u['file_id'])
                elif e['origin_type']=='fact':
                    u=db.one('SELECT source_ids FROM facts WHERE id=? AND project_id=?',(e['origin_id'],pid))
                    for sid in db.loads(u['source_ids'],[]) if u else []:
                        source=db.one('SELECT s.file_id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(sid,pid))
                        if source:files.add(source['file_id'])
        queue.extend((r['type'],r['id']) for r in references(pid,typ,identity))
    return sorted(files)

def allow_remote_history(pid,refs):
    """Check ancestor permissions without pretending historical versions are current."""
    queue=list(refs);seen=set()
    while queue:
        r=queue.pop();kind,identity=r['type'],r['id']
        if (kind,identity) in seen:continue
        seen.add((kind,identity))
        if kind=='file':
            if not security.file_allows_remote(identity,pid):return False
        elif kind=='unit':
            u=db.one('SELECT p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(identity,pid))
            if not u or not security.file_allows_remote(u['file_id'],pid):return False
        elif kind=='evidence':
            from .writing_agent.evidence import allowed_remote
            if not allowed_remote(pid,identity):return False
        else:owned(pid,kind,identity)
        queue.extend(references(pid,kind,identity))
    return True

def changed(pid, kind, identity, note):
    # Safe during early migrations and old-version fixture creation.
    if not db.one("SELECT name FROM sqlite_master WHERE name='knowledge_events'"):return
    eid=db.uid();db.execute('INSERT INTO knowledge_events(id,project_id,kind,source_type,source_id,note,created_at) VALUES(?,?,?,?,?,?,?)',(eid,pid,'invalidate',kind,identity,note,db.now()))
    queue=[(kind,identity)];seen=set()
    while queue:
        typ,uid=queue.pop(0)
        if (typ,uid) in seen:continue
        seen.add((typ,uid))
        if typ in ('assertion','observation','entity_version','wiki_version','wiki_block','file','unit','evidence'):
            for link in db.rows('SELECT block_id FROM wiki_links WHERE project_id=? AND target_type=? AND target_id=?',(pid,typ,uid)):
                queue.append(('wiki_block',link['block_id']))
        for link in db.rows('SELECT downstream_type,downstream_id FROM knowledge_dependencies WHERE project_id=? AND upstream_type=? AND upstream_id=?',(pid,typ,uid)):
            child=(link['downstream_type'],link['downstream_id']);queue.append(child)
            if child[0]=='assertion':db.execute("UPDATE kg_assertions SET lifecycle='stale' WHERE claim_version_id=?",(child[1],))
            elif child[0]=='wiki_version':db.execute("UPDATE wiki_versions SET state='stale' WHERE id=?",(child[1],))
            elif child[0]=='artifact':db.execute("UPDATE knowledge_artifacts SET state='stale' WHERE id=?",(child[1],))
    db.execute("UPDATE knowledge_events SET state='processed',attempts=attempts+1,result_json=?,processed_at=? WHERE id=?",(db.dumps({'affected':[list(k) for k in seen]}),db.now(),eid))
    for typ,uid in seen:
        for dependency in db.rows('SELECT DISTINCT section_id FROM dependencies WHERE project_id=? AND upstream_type=? AND upstream_id=?',(pid,'knowledge:'+typ,uid)):
            sid=dependency['section_id'];db.execute('UPDATE sections SET stale=1,stale_reason=? WHERE id=?',(note,sid))
            from .writing_agent.impact import add_reason
            add_reason(pid,sid,'knowledge',uid,'changed',note)
    if kind in ('file','unit','evidence'):
        from .knowledge_builds import queue_refresh
        fid=identity if kind=='file' else None
        if kind=='unit':
            parent=db.one('SELECT p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(identity,pid));fid=parent['file_id'] if parent else None
        queue_refresh(pid,fid)
    return {'id':eid,'affected':len(seen)-1}

def access_changed(pid,fid):
    if not db.one("SELECT name FROM sqlite_master WHERE name='knowledge_events'"):return
    db.execute('INSERT INTO knowledge_events(id,project_id,kind,source_type,source_id,note,state,created_at,processed_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),pid,'access_changed','file',fid,'来源外发权限变化，所有派生内容在使用前重新校验','processed',db.now(),db.now()))
