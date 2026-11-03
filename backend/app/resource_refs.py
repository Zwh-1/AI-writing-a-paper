"""Typed, versioned resource identities shared by research and writing workflows."""
import hashlib
from contextvars import ContextVar
from functools import wraps
from fastapi import HTTPException
from . import db, security
from .product_adapters import EXTRA_TABLES

RECORD_TYPES = {'note','agent','conversation','message','translation','annotation','comment','canvas','webpage','parse_result','search','deletion','proposal','guide'}
_VISITING=ContextVar('resource_ref_visiting',default=frozenset())


def version_scope(function):
    @wraps(function)
    def guarded(pid,kind,identity,*args,**kwargs):
        active=_VISITING.get();key=(pid,kind,identity)
        if key in active:raise HTTPException(409,'资源来源出现循环')
        token=_VISITING.set(active|{key})
        try:return function(pid,kind,identity,*args,**kwargs)
        finally:_VISITING.reset(token)
    return guarded


def digest(value):
    return hashlib.sha256(db.dumps(value).encode()).hexdigest()


@version_scope
def version_of(pid, kind, identity, remote=False, seen=None):
    security.require_project(pid)
    seen = set(seen or ())
    if (kind, identity) in seen:
        raise HTTPException(409, '资源来源出现循环')
    seen.add((kind, identity))
    if kind == 'source':
        row = db.one('SELECT s.*,f.project_id,f.sha256,f.active,f.egress,p.review_state FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=? AND f.project_id=?', (identity,pid))
        if not row or not row['active'] or row['review_state']=='not_applicable':
            raise HTTPException(409, '来源已停用或排除')
        from .knowledge_service import source_is_excluded
        if source_is_excluded(pid,identity):raise HTTPException(409,'来源已被替换或排除')
        if remote and not security.file_allows_remote(row['file_id'],pid):raise HTTPException(403,'来源不允许外发')
        return digest([row['text'],row['locator'],row['sha256'],row['active'],row['egress']])
    if kind in ('summary', 'fact', 'analysis') or kind in EXTRA_TABLES:
        from .product_sources import version
        return version(pid, kind, identity, remote, seen)
    if kind in RECORD_TYPES:
        row=db.one('SELECT * FROM studio_records WHERE id=? AND project_id=? AND kind=? AND deleted_at IS NULL',(identity,pid,kind))
        if not row:raise HTTPException(404,'研究记录不存在或类型不匹配')
        data=db.loads(row['data'],{})
        # Explicit legacy provenance still enforces permissions before the relation backfill runs.
        file_ids=set(data.get('file_ids',[]))|{c['file_id'] for c in data.get('citations',[]) if c.get('file_id')}
        owner=db.one('SELECT file_id FROM research_record_owners WHERE record_id=?',(identity,))
        if owner and owner['file_id']:file_ids.add(owner['file_id'])
        from .source_access import require_file
        for fid in file_ids:require_file(pid,fid,remote)
        for field,typ in (('message_id','message'),('annotation_id','annotation')):
            if data.get(field):version_of(pid,typ,data[field],remote,seen)
        if remote and data.get('source_revision_id'):
            version_of(pid,'revision',data['source_revision_id'],True,seen)
        state=db.one('SELECT state FROM research_record_states WHERE record_id=?',(identity,))
        if state and state['state']=='stale':raise HTTPException(409,'派生内容的来源版本已变化')
        for link in db.rows('SELECT * FROM research_links WHERE record_id=? AND record_revision=?',(identity,row['revision'])):
            if version_of(pid,link['target_type'],link['target_id'],remote,seen)!=link['target_version']:
                raise HTTPException(409,'派生内容的来源版本已变化')
        return digest([row['kind'],row['revision'],data])
    if kind=='asset':
        row=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(identity,pid))
        if not row:raise HTTPException(404,'资产不属于当前项目')
        for ref in db.loads(row['metadata'],{}).get('source_refs',[]):
            if version_of(pid,ref['type'],ref['id'],remote,seen)!=ref['version']:raise HTTPException(409,'资产的来源版本已变化')
        if remote:
            from .assistant_context import artifact
            artifact(pid,identity,False)
        return digest([row['sha256'],db.loads(row['metadata'],{})])
    if kind=='revision':
        row=db.one('SELECT r.*,s.project_id FROM revisions r JOIN sections s ON s.id=r.section_id WHERE r.id=? AND s.project_id=?',(identity,pid))
        if not row:raise HTTPException(404,'正文版本不属于当前项目')
        if remote:
            from .writing_agent.provenance import revision_permissions
            revision_permissions(pid,identity)
        return digest([row['id'],row['document']])
    from .knowledge_lineage import version_of as knowledge_version
    return knowledge_version(pid,kind,identity,remote,seen-{(kind,identity)})


def make(pid, kind, identity, **location):
    return {'project_id':pid,'type':kind,'id':identity,'version':version_of(pid,kind,identity),**location}


def validate(pid, refs, remote=False, allow_stale=False):
    if not isinstance(refs,list) or len(refs)>500:raise HTTPException(422,'来源列表无效')
    states=[]
    for ref in refs:
        if not isinstance(ref,dict) or not all(isinstance(ref.get(key),str) and ref[key] for key in ('type','id','version')) or ref.get('project_id',pid)!=pid:
            raise HTTPException(422,'来源类型、版本或项目无效')
        require_owned(pid,ref['type'],ref['id'])
        try:
            current=version_of(pid,ref['type'],ref['id'],remote)==ref['version']
            if not current and not allow_stale:raise HTTPException(409,'来源版本已变化')
            states.append(current)
        except HTTPException as error:
            if not allow_stale or error.status_code in (403,422):raise
            states.append(False)
    return all(states)


def require_owned(pid,kind,identity):
    if kind in ('summary', 'fact', 'analysis') or kind in EXTRA_TABLES:
        from .product_sources import owned
        return owned(pid, kind, identity)
    tables={'file':'files','reference':'bibliography','asset':'artifacts','evidence':'evidence_items'}
    if kind in tables:
        owned=db.one('SELECT id FROM '+tables[kind]+' WHERE id=? AND project_id=?',(identity,pid))
    elif kind in RECORD_TYPES:
        owned=db.one('SELECT id FROM studio_records WHERE id=? AND project_id=? AND kind=?',(identity,pid,kind))
    elif kind=='unit':
        owned=db.one('SELECT u.id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=?',(identity,pid))
    elif kind=='source':
        owned=db.one('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(identity,pid))
    elif kind=='revision':
        owned=db.one('SELECT r.id FROM revisions r JOIN sections s ON s.id=r.section_id WHERE r.id=? AND s.project_id=?',(identity,pid))
    else:
        from .knowledge_lineage import owned as knowledge_owned
        owned=knowledge_owned(pid,kind,identity)
    if not owned:raise HTTPException(422,'来源不存在、类型不匹配或不属于当前项目')
    return owned


def for_hit(pid, hit):
    uid=hit.get('knowledge_unit_id') or (hit['id'][5:] if str(hit.get('id','')).startswith('unit:') else None)
    return make(pid,'unit' if uid else 'source',uid or hit['id'],file_id=hit['file_id'],parse_id=hit.get('parse_id'),
                content_hash=hit.get('unit_hash') or hit.get('content_hash') or digest(hit['text']),locator=hit.get('locator',{}))


def source_files(pid, sources):
    """Collect original file identities across research, assets and legacy outputs."""
    validate(pid,sources)
    queue=[(ref['type'],ref['id']) for ref in sources];seen=set();files=set()
    while queue:
        kind,identity=queue.pop()
        if (kind,identity) in seen:continue
        seen.add((kind,identity))
        if kind=='file':files.add(identity)
        elif kind=='source':
            row=db.one('SELECT s.file_id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(identity,pid))
            if row:files.add(row['file_id'])
        elif kind in ('summary','fact','analysis') or kind in EXTRA_TABLES:
            from .product_sources import owned,origins
            queue.extend(origins(pid,kind,owned(pid,kind,identity)))
        elif kind in RECORD_TYPES:
            row=db.one('SELECT * FROM studio_records WHERE id=? AND project_id=?',(identity,pid))
            data=db.loads(row['data'],{});files.update(data.get('file_ids',[]))
            owner=db.one('SELECT file_id FROM research_record_owners WHERE record_id=?',(identity,))
            if owner and owner['file_id']:files.add(owner['file_id'])
            queue.extend((link['target_type'],link['target_id']) for link in db.rows('SELECT * FROM research_links WHERE record_id=? AND record_revision=?',(identity,row['revision'])))
        elif kind=='asset':
            row=db.one('SELECT metadata FROM artifacts WHERE id=? AND project_id=?',(identity,pid))
            meta=db.loads(row['metadata'],{});files.update(meta.get('source_file_ids',[]))
            if meta.get('source_file_id'):files.add(meta['source_file_id'])
            queue.extend((ref['type'],ref['id']) for ref in meta.get('source_refs',[]))
            queue.extend(('source',sid) for sid in meta.get('source_ids',[]))
            queue.extend(('revision',rid) for rid in meta.get('source_revision_ids',[]))
            if meta.get('parent_artifact_id'):queue.append(('asset',meta['parent_artifact_id']))
        elif kind=='revision':
            queue.extend((dep['upstream_type'][len('knowledge:'):],dep['upstream_id']) for dep in db.rows("SELECT * FROM dependencies WHERE revision_id=? AND upstream_type LIKE 'knowledge:%'",(identity,)))
        else:
            from .knowledge_lineage import source_files as knowledge_files
            files.update(knowledge_files(pid,[{'type':kind,'id':identity}]))
    for fid in files:require_owned(pid,'file',fid)
    return sorted(files)


def resolve(pid, ref, remote=False, _seen=None):
    _seen=set(_seen or ())
    if (ref['type'],ref['id']) in _seen:raise HTTPException(409,'来源跳转出现循环')
    _seen.add((ref['type'],ref['id']))
    current=validate(pid,[ref],remote,allow_stale=not remote)
    typ=ref['type'];identity=ref['id'];location={'file_id':ref.get('file_id'),'unit_id':identity if typ=='unit' else None,'parse_id':ref.get('parse_id'),'locator':ref.get('locator',{})}
    if typ=='unit':
        from .knowledge_units import get
        row=get(pid,identity,remote);location.update(file_id=row['file_id'],parse_id=row['parse_id'],locator=row['locator'])
    elif typ=='source':
        row=db.one('SELECT s.* FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(identity,pid))
        if row:location.update(file_id=row['file_id'],locator=db.loads(row['locator'],{}))
    elif typ=='file':location['file_id']=identity
    elif typ=='asset':
        row=db.one('SELECT metadata FROM artifacts WHERE id=? AND project_id=?',(identity,pid))
        data=db.loads(row['metadata'],{})
        if data.get('source_refs'):
            location=resolve(pid,data['source_refs'][0],remote,_seen)['location']
        elif data.get('source_file_id'):
            require_owned(pid,'file',data['source_file_id']);location['file_id']=data['source_file_id']
        elif data.get('source_revision_ids'):
            revision_id=data['source_revision_ids'][0];require_owned(pid,'revision',revision_id)
            location['section_id']=db.one('SELECT section_id FROM revisions WHERE id=?',(revision_id,))['section_id']
    elif typ in ('summary', 'fact', 'analysis') or typ in EXTRA_TABLES:
        from .product_sources import owned, origins
        original = next(iter(origins(pid, typ, owned(pid, typ, identity))), None)
        if original:
            location = resolve(pid, make(pid, *original), remote, _seen)['location']
    elif typ=='revision':
        location['section_id']=db.one('SELECT section_id FROM revisions WHERE id=?',(identity,))['section_id']
    elif typ in RECORD_TYPES:
        row=db.one('SELECT * FROM studio_records WHERE id=? AND project_id=?',(identity,pid))
        if row:
            link=db.one('SELECT source_json FROM research_links WHERE record_id=? AND record_revision=? ORDER BY CASE target_type WHEN \'unit\' THEN 0 WHEN \'source\' THEN 1 ELSE 2 END,id',(identity,row['revision']))
            if link:location=resolve(pid,db.loads(link['source_json'],{}),remote,_seen)['location']
            else:
                data=db.loads(row['data'],{})
                location['section_id']=data.get('section_id')
    return {'ref':ref,'current':current,'location':location}
