from .. import db,security
from .common import owned,blob,unblob,digest,fail
from . import repository

KINDS={'code_observation','test_definition','test_run','deployment','literature','material_observation','author_statement','data_result','guidance_observation'}

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
        s=db.one('SELECT s.*,f.project_id,f.sha256 AS file_hash,f.role,f.extension FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(origin_id,pid))
        if not s:fail('NOT_FOUND','资料来源不属于本项目',404)
        if kind not in ('literature','material_observation','author_statement','test_definition'):fail('EVIDENCE_LEVEL','普通资料不能自动声称为实际执行记录')
        version=digest([s['text'],s['file_hash']]);payload={'text':s['text'],'locator':db.loads(s['locator'],{}),'file_id':s['file_id'],'method':s['method']}
        from ..material_policy import source_context,is_research
        file_={'id':s['file_id'],'role':s['role'],'extension':s['extension']}
        payload['material_use']=source_context(file_,'research' if is_research(file_) else 'guidance')
    elif origin=='knowledge_unit':
        if kind not in ('literature','material_observation','guidance_observation'):fail('EVIDENCE_LEVEL','文献知识单元不能自动升级为执行结果')
        from ..knowledge_units import read
        data=read(pid,origin_id,before=int(locator.get('before',1)),after=int(locator.get('after',1)),max_chars=int(locator.get('max_chars',16000)),start=locator.get('start'),end=locator.get('end'),intent=locator.get('intent','research'))
        if kind=='literature' and not data['material_use']['allowed_for_intent']:fail('EVIDENCE_LEVEL','该原文尚未获准用于研究引用')
        if not data['current'] or data['status']!='read':fail('CONTEXT_BUDGET_EXCEEDED','需先读取当前版本的完整原文或明确区间')
        version=digest([data['parse_id'],data['unit_hash'],data['file_hash'],data['scope'],data['text']]);payload=data
    elif origin=='document_reading':
        from ..document_reading import context
        data=context(pid,origin_id)
        expected='literature' if data['intent']=='research' else 'guidance_observation'
        if kind!=expected or not data['current'] or not data['passages']:fail('EVIDENCE_LEVEL','只能依据实际读取且当前有效的原文建立对应用途的观察')
        version=data['version'];payload=data
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
    elif origin=='fact':
        fact=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(origin_id,pid))
        if not fact:fail('NOT_FOUND','研究事实不属于本项目',404)
        if kind!='data_result':fail('EVIDENCE_LEVEL','研究事实只能登记为研究结果依据')
        payload={'label':fact['label'],'text':fact['statement'],'status':fact['status'],'verification':fact['verification'],'source_ids':db.loads(fact['source_ids'],[]),'artifact_id':fact['artifact_id'],'revision':fact['revision'],'verification_origin':'fact_record'}
        version=str(fact['revision'])
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
        from ..knowledge_service import source_is_excluded
        return bool(s and security.file_allows_remote(s['file_id'],pid) and not source_is_excluded(pid,e['origin_id']))
    if e['origin_type']=='knowledge_unit':
        from ..knowledge_units import get
        try:item=get(pid,e['origin_id'],remote=True,intent=db.loads(e['locator_json'],{}).get('intent','research'))
        except __import__('fastapi').HTTPException:return False
        return item['current']
    if e['origin_type']=='document_reading':
        from ..document_reading import context
        try:return context(pid,e['origin_id'],remote=True)['current']
        except __import__('fastapi').HTTPException:return False
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
    if e['origin_type']=='fact':
        fact=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(e['origin_id'],pid))
        if not fact:return False
        for source_id in db.loads(fact['source_ids'],[]):
            source=db.one('SELECT file_id FROM sources WHERE id=?',(source_id,))
            if not source or not security.file_allows_remote(source['file_id'],pid):return False
        if fact['artifact_id']:
            artifact=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(fact['artifact_id'],pid))
            if not artifact:return False
            metadata=db.loads(artifact['metadata'],{})
            if metadata.get('egress','local_only')=='local_only':return False
            if metadata.get('source_file_id') and not security.file_allows_remote(metadata['source_file_id'],pid):return False
        return True
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
    elif e['origin_type']=='knowledge_unit':
        from ..knowledge_units import epoch,get
        item=get(pid,e['origin_id']);scope.extend([epoch(pid),item['current'],item['review_state'],item['file_hash'],security.file_allows_remote(item['file_id'],pid)])
    elif e['origin_type']=='document_reading':
        from ..document_reading import context
        reading=context(pid,e['origin_id']);scope.extend([reading['version'],reading['current'],security.file_allows_remote(reading['file_id'],pid)])
    elif e['origin_type']=='artifact':scope.append(db.one('SELECT sha256,metadata FROM artifacts WHERE id=?',(e['origin_id'],)))
    elif e['origin_type']=='fact':
        fact=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(e['origin_id'],pid))
        scope.append(fact)
        if fact:
            scope.extend(db.rows('SELECT id,sha256,egress,active FROM files WHERE id IN (SELECT file_id FROM sources WHERE id IN ('+','.join('?' for _ in db.loads(fact['source_ids'],[]))+'))',tuple(db.loads(fact['source_ids'],[]))) if db.loads(fact['source_ids'],[]) else [])
            if fact['artifact_id']:scope.append(db.one('SELECT id,sha256,metadata FROM artifacts WHERE id=? AND project_id=?',(fact['artifact_id'],pid)))
    elif e['origin_type']=='author':
        ids,indirect=author_dependencies(pid,e['origin_id'])
        if indirect:scope.append([(identity,permission_stamp(pid,identity,ancestors)) for identity in ids])
    return digest(scope)

def for_model(item):
    """Preserve observations and conditions without duplicating archive manifests."""
    result={k:item[k] for k in ('id','kind','conditions')};payload=item['payload']
    if item['origin_type']=='knowledge_unit':
        from ..knowledge_service import for_model as unit_for_model
        result['payload']=unit_for_model(payload);result['original_payload_hash']=item['payload_hash']
    elif item['origin_type']=='document_reading':
        keys=('reading_id','question','intent','file_id','file_hash','material_use','semantic_context_status','missing_topics','required_topics','parsed_text_coverage_complete','claim_support','passages','findings','scope_note')
        result['payload']={k:payload[k] for k in keys if k in payload};result['original_payload_hash']=item['payload_hash']
    elif item['origin_type']=='source':
        from ..source_payload import compact_locator
        result['payload']={key:value for key,value in payload.items() if key!='file_id'}
        result['payload']['locator']=compact_locator(payload.get('locator'));result['original_payload_hash']=item['payload_hash']
        if item.get('origin_id'):result['payload']['source_id']=item['origin_id']
    elif item['origin_type']=='artifact' and item['kind'] in ('test_run','deployment','data_result'):
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
    if e['origin_type']=='knowledge_unit':
        from ..knowledge_units import read
        locator=db.loads(e['locator_json'],{})
        try:data=read(e['project_id'],e['origin_id'],before=int(locator.get('before',1)),after=int(locator.get('after',1)),max_chars=int(locator.get('max_chars',16000)),start=locator.get('start'),end=locator.get('end'),intent=locator.get('intent','research'))
        except __import__('fastapi').HTTPException:return None
        return digest([data['parse_id'],data['unit_hash'],data['file_hash'],data.get('scope'),data.get('text')]) if data['current'] and data['status']=='read' else None
    if e['origin_type']=='document_reading':
        from ..document_reading import context
        try:data=context(e['project_id'],e['origin_id'])
        except __import__('fastapi').HTTPException:return None
        return data['version'] if data['current'] else None
    if e['origin_type']=='source':
        s=db.one('SELECT s.text,f.sha256 FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=?',(e['origin_id'],))
        return digest([s['text'],s['sha256']]) if s else None
    if e['origin_type']=='artifact':
        a=db.one('SELECT * FROM artifacts WHERE id=?',(e['origin_id'],))
        return digest([a['sha256'],db.loads(a['metadata'],{})]) if a else None
    if e['origin_type']=='fact':
        fact=db.one('SELECT revision FROM facts WHERE id=? AND project_id=?',(e['origin_id'],e['project_id']))
        return str(fact['revision']) if fact else None
    return e['origin_version']

def get(pid,eid,remote=False):
    e=owned('evidence',eid,pid)
    if remote and not allowed_remote(pid,eid):fail('SOURCE_ACCESS_REVOKED','该证据或其派生来源禁止外发',403)
    return {**e,'conditions':db.loads(e['conditions_json'],[]),'payload':unblob(e['payload_hash']),'version_current':current_version(e)==e['origin_version']}

@db.atomic
def sync_fact(pid,fid):
    """Expose the editable research fact as a versioned writing evidence item."""
    fact=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(fid,pid))
    if not fact:fail('NOT_FOUND','研究事实不属于本项目',404)
    version=str(fact['revision'])
    current=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='fact' AND origin_id=? AND origin_version=? AND validity='current' ORDER BY created_at DESC LIMIT 1",(pid,fid,version))
    if current:return get(pid,current['id'])
    previous=db.rows("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='fact' AND origin_id=? AND validity='current'",(pid,fid))
    for row in previous:db.execute("UPDATE evidence_items SET validity='historical' WHERE id=?",(row['id'],))
    return create(pid,{'kind':'data_result','origin_type':'fact','origin_id':fid,'locator':{'fact_id':fid,'revision':fact['revision']},'conditions':['内容来自“研究与成果”的登记记录；状态和核查等级必须随事实一起解释']})

@db.atomic
def sync_artifact(pid,aid):
    """Expose a research result or figure as a versioned writing evidence item."""
    artifact=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
    if not artifact:fail('NOT_FOUND','研究产物不属于本项目',404)
    version=digest([artifact['sha256'],db.loads(artifact['metadata'],{})])
    current=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='artifact' AND origin_id=? AND origin_version=? AND validity='current' ORDER BY created_at DESC LIMIT 1",(pid,aid,version))
    if current:return get(pid,current['id'])
    previous=db.rows("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='artifact' AND origin_id=? AND validity='current'",(pid,aid))
    for row in previous:db.execute("UPDATE evidence_items SET validity='historical' WHERE id=?",(row['id'],))
    return create(pid,{'kind':'data_result','origin_type':'artifact','origin_id':aid,'locator':{'artifact_id':aid,'sha256':artifact['sha256']},'title':artifact['name'],'conditions':['内容来自“研究与成果”登记的研究产物；需结合原始产物类型和实际执行标记解释']})

@db.atomic
def sync_source(pid,sid,kind='material_observation'):
    """Expose a project material or literature chunk as writing evidence."""
    source=db.one('SELECT s.id,s.file_id,s.text,f.project_id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(sid,pid))
    if not source:fail('NOT_FOUND','项目资料片段不属于本项目',404)
    if kind not in ('material_observation','literature'):fail('INVALID_EVIDENCE','资料证据类型无效')
    current=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='source' AND origin_id=? AND kind=? AND validity='current' ORDER BY created_at DESC LIMIT 1",(pid,sid,kind))
    if current:
        previous=get(pid,current['id'])
        if previous['version_current']:return previous
        db.execute("UPDATE evidence_items SET validity='stale' WHERE id=?",(current['id'],))
    return create(pid,{'kind':kind,'origin_type':'source','origin_id':sid,'locator':{},'title':source['file_id'],'conditions':['来源来自当前项目资料库；需结合原文范围、文献元数据和适用规则解释']})

def set_validity(pid,eid,value):
    owned('evidence',eid,pid)
    if value not in ('current','stale','revoked','historical'):fail('INVALID_STATE','证据状态无效')
    db.execute('UPDATE evidence_items SET validity=? WHERE id=?',(value,eid))
    from ..knowledge_lineage import changed as knowledge_changed
    knowledge_changed(pid,'evidence',eid,'项目证据有效性变化')
    from .impact import changed
    changed(pid,'evidence',eid,'证据状态发生变化')
    return get(pid,eid)


@db.atomic
def sync_unit(pid,uid,locator=None):
    locator=locator or {}
    old=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='knowledge_unit' AND origin_id=? AND locator_json=? AND validity='current' ORDER BY created_at DESC LIMIT 1",(pid,uid,db.dumps(locator)))
    if old:
        item=get(pid,old['id'])
        if item['version_current']:return item
        db.execute("UPDATE evidence_items SET validity='stale' WHERE id=?",(old['id'],))
    return create(pid,{'kind':'literature','origin_type':'knowledge_unit','origin_id':uid,'locator':locator,'conditions':['原文及分段核对状态不代表具体论断已获得支持；需保留限定条件和争议']})


@db.atomic
def sync_reading(pid,identity):
    from ..document_reading import context
    reading=context(pid,identity)
    old=db.one("SELECT id FROM evidence_items WHERE project_id=? AND origin_type='document_reading' AND origin_id=? AND origin_version=? AND validity='current' ORDER BY created_at DESC LIMIT 1",(pid,identity,reading['version']))
    if old:return get(pid,old['id'])
    db.execute("UPDATE evidence_items SET validity='historical' WHERE project_id=? AND origin_type='document_reading' AND origin_id=? AND validity='current'",(pid,identity))
    return create(pid,{'kind':'literature' if reading['intent']=='research' else 'guidance_observation','origin_type':'document_reading','origin_id':identity,'locator':{'reading_id':identity},'conditions':['原文按阅读问题和用途提供，覆盖不等于论断正确；仍需核对支持性及条件']})
