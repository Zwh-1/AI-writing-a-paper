from .. import db,security
from .common import owned,blob,unblob,digest,fail
from . import repository

KINDS={'code_observation','test_definition','test_run','deployment','literature','author_statement','data_result'}

def create(pid,body):
    security.require_project(pid)
    if set(body)-{'kind','origin_type','origin_id','locator','conditions','text','title'}:fail('INVALID_EVIDENCE','证据字段无效')
    kind=body.get('kind');origin=body.get('origin_type');locator=body.get('locator',{});origin_id=body.get('origin_id','')
    if kind not in KINDS or not isinstance(locator,dict):fail('INVALID_EVIDENCE','证据类型或定位无效')
    if origin=='code':
        if kind not in ('code_observation','test_definition'):fail('EVIDENCE_LEVEL','源码不能直接升级为运行或部署证据')
        data=repository.read(pid,origin_id,locator.get('path',''),locator.get('line_start',1),locator.get('line_end',150))
        version=data['file_sha256'];payload={**data,'title':body.get('title',data['path'])}
    elif origin=='source':
        s=db.one('SELECT s.*,f.project_id,f.sha256 AS file_hash FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(origin_id,pid))
        if not s:fail('NOT_FOUND','资料来源不属于本项目',404)
        if kind not in ('literature','author_statement','test_definition'):fail('EVIDENCE_LEVEL','普通资料不能自动声称为实际执行记录')
        version=digest([s['text'],s['file_hash']]);payload={'text':s['text'],'locator':db.loads(s['locator'],{}),'file_id':s['file_id'],'method':s['method']}
    elif origin=='artifact':
        a=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(origin_id,pid))
        if not a:fail('NOT_FOUND','研究产物不属于本项目',404)
        meta=db.loads(a['metadata'],{});payload={'text':body.get('text',''),'metadata':meta,'artifact_sha256':a['sha256'],'verification_origin':'recorded_artifact'}
        if not payload['text'] and a['sha256']:
            try:payload['text']=security.safe_object(a['sha256']).read_text(encoding='utf-8')[:40000]
            except UnicodeError:payload['text']=a['name']
        version=digest([a['sha256'],meta])
        if kind in ('test_run','deployment') and not(meta.get('actual_execution') or meta.get('run_id') or meta.get('receipt') or meta.get('verification_origin')=='imported'):
            fail('EVIDENCE_LEVEL','产物缺少实际运行或明确导入记录')
    elif origin=='author':
        if kind!='author_statement':fail('EVIDENCE_LEVEL','作者说明不自动变成运行结果')
        text=body.get('text','')
        if not isinstance(text,str) or not text.strip() or len(text)>20000:fail('INVALID_EVIDENCE','作者说明不能为空或过长')
        origin_id=origin_id or db.uid();payload={'text':text,'verification_origin':'author_declaration'};version=digest(text)
    else:fail('INVALID_EVIDENCE','来源类型无效')
    conditions=body.get('conditions',[])
    if not isinstance(conditions,list) or len(conditions)>30 or any(not isinstance(x,str) or len(x)>1000 for x in conditions):fail('INVALID_CONDITIONS','证据条件无效')
    eid=db.uid();sha=blob(payload)
    db.execute('INSERT INTO evidence_items VALUES(?,?,?,?,?,?,?,?,?,?,?)',(eid,pid,kind,origin,origin_id,version,db.dumps(locator),sha,db.dumps(conditions),'current',db.now()))
    return get(pid,eid)

def author_dependencies(pid,origin_id):
    for table in ('thesis_core_versions','section_contract_versions'):
        row=db.one('SELECT content_json FROM '+table+' WHERE id=? AND project_id=?',(origin_id,pid))
        if not row:continue
        content=db.loads(row['content_json'],{});ids=set(content.get('evidence_ids',[]))
        for cid in content.get('claim_versions',[]):
            owned('claim',cid,pid);ids.update(r['evidence_id'] for r in db.rows('SELECT evidence_id FROM claim_evidence_links WHERE claim_version_id=?',(cid,)))
        return sorted(ids),bool(content.get('claim_versions') or (table=='thesis_core_versions' and content.get('evidence_ids')))
    return [],False

def allowed_remote(pid,eid,_ancestors=None):
    ancestors=set(_ancestors or ())
    if eid in ancestors:return False
    ancestors.add(eid)
    e=owned('evidence',eid,pid)
    if e['validity'] not in ('current','historical'):return False
    if e['origin_type']=='code':
        s=owned('snapshot',e['origin_id'],pid);b=owned('repository',s['binding_id'],pid)
        return b['egress_policy']=='selected_content'
    if e['origin_type']=='source':
        s=db.one('SELECT file_id FROM sources WHERE id=?',(e['origin_id'],))
        return bool(s and security.file_allows_remote(s['file_id'],pid))
    if e['origin_type']=='artifact':
        a=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(e['origin_id'],pid))
        if not a:return False
        m=db.loads(a['metadata'],{})
        if m.get('egress','local_only')=='local_only':return False
        for key in ('snapshot_id','target_snapshot_id'):
            if m.get(key):
                snapshot=db.one('SELECT * FROM repository_snapshots WHERE id=? AND project_id=?',(m[key],pid))
                if not snapshot or owned('repository',snapshot['binding_id'],pid)['egress_policy']!='selected_content':return False
        return not m.get('source_file_id') or security.file_allows_remote(m['source_file_id'],pid)
    if e['origin_type']=='author':
        ids,_=author_dependencies(pid,e['origin_id'])
        return all(allowed_remote(pid,identity,ancestors) for identity in ids)
    return True

def permission_stamp(pid,eid,_ancestors=None):
    ancestors=set(_ancestors or ())
    if eid in ancestors:return digest(['cyclic_author_dependency',eid])
    ancestors.add(eid)
    e=owned('evidence',eid,pid);scope=[e['validity'],e['origin_type'],allowed_remote(pid,eid)]
    if e['origin_type']=='code':
        s=owned('snapshot',e['origin_id'],pid);b=owned('repository',s['binding_id'],pid);scope.extend([b['scope_revision'],b['egress_policy'],s['manifest_hash']])
    elif e['origin_type']=='source':
        f=db.one('SELECT f.* FROM files f JOIN sources s ON s.file_id=f.id WHERE s.id=?',(e['origin_id'],))
        if f:
            scope.extend([f['sha256'],f['active'],f['egress']]);queue=[f['id']];seen=set()
            while queue:
                fid=queue.pop()
                if fid in seen:continue
                seen.add(fid);parent=db.one('SELECT id,sha256,active,egress FROM files WHERE id=?',(fid,));scope.append(parent)
                queue.extend(x['parent_file_id'] for x in db.rows('SELECT parent_file_id FROM file_derivations WHERE child_file_id=?',(fid,)))
    elif e['origin_type']=='artifact':scope.append(db.one('SELECT sha256,metadata FROM artifacts WHERE id=?',(e['origin_id'],)))
    elif e['origin_type']=='author':
        ids,indirect=author_dependencies(pid,e['origin_id'])
        if indirect:scope.append([(identity,permission_stamp(pid,identity,ancestors)) for identity in ids])
    return digest(scope)

def for_model(item):
    """Preserve observations and conditions without duplicating archive manifests."""
    result={k:item[k] for k in ('id','kind','conditions')};payload=item['payload']
    if item['origin_type']=='artifact' and item['kind'] in ('test_run','deployment','data_result'):
        keys=('actual_execution','execution_outcome','exit_code','timed_out','timed_out_or_output_limited','environment','public_network_used','public_network_or_real_deployment','fixture','scope','command','network','timestamp','snapshot_id','started_from','image','compiler','stdout','stderr','verification_origin','seconds')
        meta=payload.get('metadata',{});observed={k:meta[k] for k in keys if k in meta}
        text=payload.get('text','')
        try:
            record=__import__('json').loads(text)
            if isinstance(record,dict):observed.update({k:record[k] for k in keys if k in record})
        except (ValueError,TypeError):observed['text']=text
        result['payload']={'observed':observed,'original_artifact_sha256':payload.get('artifact_sha256'),'full_source_manifest_stored':bool(meta.get('compiled_sources_sha256')),'scope_note':'原始记录和完整产物哈希保留在证据原件，未当作正文重复发送'}
    else:result['payload']=payload
    return result

def current_version(e):
    if e['origin_type']=='source':
        s=db.one('SELECT s.text,f.sha256 FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=?',(e['origin_id'],))
        return digest([s['text'],s['sha256']]) if s else None
    if e['origin_type']=='artifact':
        a=db.one('SELECT * FROM artifacts WHERE id=?',(e['origin_id'],))
        return digest([a['sha256'],db.loads(a['metadata'],{})]) if a else None
    return e['origin_version']

def get(pid,eid,remote=False):
    e=owned('evidence',eid,pid)
    if remote and not allowed_remote(pid,eid):fail('SOURCE_ACCESS_REVOKED','该证据或其派生来源禁止外发',403)
    return {**e,'conditions':db.loads(e['conditions_json'],[]),'payload':unblob(e['payload_hash']),'version_current':current_version(e)==e['origin_version']}

def set_validity(pid,eid,value):
    owned('evidence',eid,pid)
    if value not in ('current','stale','revoked','historical'):fail('INVALID_STATE','证据状态无效')
    db.execute('UPDATE evidence_items SET validity=? WHERE id=?',(value,eid))
    from .impact import changed
    changed(pid,'evidence',eid,'证据状态发生变化')
    return get(pid,eid)
