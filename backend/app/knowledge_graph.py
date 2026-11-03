"""Evidence-bound entity graph; discovery paths never certify a proposition."""
import re
from fastapi import HTTPException
from . import db, security
from . import knowledge_lineage as lineage

NAMESPACES={'literature','project','guidance'}
KINDS={'document','author','keyword','question','concept','method','system','module','dataset','metric','environment','result','section','rule'}

def namespace(value):
    if value not in NAMESPACES:lineage.fail('INVALID_NAMESPACE','知识范围无效',422)
    return value

def normalized(text):return ' '.join(str(text).casefold().split())

def tokens(text):
    latin=re.findall(r'[a-z0-9_]{2,}',normalized(text)); chinese=re.findall(r'[\u4e00-\u9fff]+',str(text))
    return set(latin+[s[i:i+2] for s in chinese for i in range(max(1,len(s)-1))])

def relevance(text, query):return len(tokens(text)&tokens(query))

@db.atomic
def entity(pid, body):
    security.require_project(pid);ns=namespace(body.get('namespace','literature'));kind=body.get('kind','concept')
    name=str(body.get('name','')).strip();key=str(body.get('key') or kind+':'+normalized(name));refs=body.get('sources',[])
    if kind not in KINDS or not name or len(name)>500 or len(key)>1000:lineage.fail('INVALID_ENTITY','实体名称或类型无效',422)
    if not refs:lineage.fail('PROVENANCE_MISSING','实体需要来源',422)
    lineage.validate_refs(pid,refs)
    old=db.one('SELECT * FROM kg_entities WHERE project_id=? AND namespace=? AND entity_key=?',(pid,ns,key))
    if old and old['redirect_id']:
        return get_entity(pid,old['redirect_id'])
    eid=old['id'] if old else db.uid();description=str(body.get('description',''))
    content=lineage.digest([name,description,refs]);stamp=db.now()
    if old:
        current=lineage.owned(pid,'entity_version',old['current_version_id'])
        if current['content_hash']==content:return get_entity(pid,eid)
        if current['name']==name and current['description']==description:
            try:lineage.validate_refs(pid,lineage.references(pid,'entity_version',current['id']));return get_entity(pid,eid)
            except HTTPException:pass
    else:db.execute('INSERT INTO kg_entities(id,project_id,namespace,entity_key,kind,created_at) VALUES(?,?,?,?,?,?)',(eid,pid,ns,key,kind,stamp))
    vid=db.uid();db.execute('INSERT INTO kg_entity_versions VALUES(?,?,?,?,?,?,?,?,?)',(vid,pid,eid,old['current_version_id'] if old else None,name,description,content,body.get('method','candidate'),stamp))
    lineage.add_dependencies(pid,'entity_version',vid,content,refs)
    db.execute('UPDATE kg_entities SET current_version_id=? WHERE id=?',(vid,eid))
    for alias in list(dict.fromkeys([name,*body.get('aliases',[])]))[:30]:
        if isinstance(alias,str) and alias.strip():db.execute('INSERT OR IGNORE INTO kg_aliases VALUES(?,?,?,?,?,?,?,?)',(db.uid(),pid,eid,alias,normalized(alias),'candidate','{}',stamp))
    if old:lineage.changed(pid,'entity_version',old['current_version_id'],'实体描述已更新')
    return get_entity(pid,eid)

def get_entity(pid,eid,remote=False):
    row=lineage.owned(pid,'entity',eid);version=lineage.owned(pid,'entity_version',row['current_version_id'])
    try:lineage.version_of(pid,'entity_version',version['id'],remote);current=True
    except HTTPException:
        if remote:raise
        current=False
    return {**row,'version':version,'name':version['name'],'description':version['description'],'current':current,
            'aliases':db.rows('SELECT * FROM kg_aliases WHERE entity_id=?',(eid,)),
            'sources':lineage.references(pid,'entity_version',version['id'])}

@db.atomic
def merge(pid,body):
    source=lineage.owned(pid,'entity',body.get('source_id'));target=lineage.owned(pid,'entity',body.get('target_id'))
    note=str(body.get('note','')).strip();reviewer=str(body.get('reviewer','')).strip()
    if source['id']==target['id'] or source['namespace']!=target['namespace'] or source['kind']!=target['kind'] or not note or not reviewer:lineage.fail('INVALID_MERGE','归并需要同范围同类型实体、核对人及理由',422)
    if body.get('expected_source_version')!=source['current_version_id'] or body.get('expected_target_version')!=target['current_version_id']:lineage.fail('ENTITY_CHANGED','实体版本变化，请重新比较')
    if target['redirect_id'] or source['redirect_id']:lineage.fail('ENTITY_REDIRECT','请使用当前未归并实体')
    decision={'source':source['id'],'target':target['id'],'note':note,'reviewer':reviewer,'created_at':db.now()}
    db.execute('UPDATE kg_entities SET redirect_id=? WHERE id=?',(target['id'],source['id']))
    for alias in db.rows('SELECT alias,normalized FROM kg_aliases WHERE entity_id=?',(source['id'],)):
        db.execute('INSERT OR IGNORE INTO kg_aliases VALUES(?,?,?,?,?,?,?,?)',(db.uid(),pid,target['id'],alias['alias'],alias['normalized'],'human',db.dumps(decision),db.now()))
    db.audit(pid,'knowledge_entity_merge',decision);lineage.changed(pid,'entity_version',source['current_version_id'],'实体已归并，相关知识需重编译')
    for claim in db.rows("SELECT claim_version_id FROM kg_assertions WHERE project_id=? AND (subject_id=? OR object_id=?) AND lifecycle IN ('candidate','current')",(pid,source['id'],source['id'])):
        db.execute("UPDATE kg_assertions SET lifecycle='superseded' WHERE claim_version_id=?",(claim['claim_version_id'],));lineage.changed(pid,'assertion',claim['claim_version_id'],'实体归并后需要使用新关联版本')
    return {'decision':decision,'target':get_entity(pid,target['id'])}

@db.atomic
def unmerge(pid,eid,body):
    row=lineage.owned(pid,'entity',eid)
    if not row['redirect_id'] or not body.get('reviewer') or not body.get('note'):lineage.fail('INVALID_SPLIT','撤销归并需要原实体、核对人及理由',422)
    target=row['redirect_id'];db.execute('UPDATE kg_entities SET redirect_id=NULL WHERE id=?',(eid,))
    for alias in db.rows('SELECT id,decision_json FROM kg_aliases WHERE entity_id=?',(target,)):
        if db.loads(alias['decision_json'],{}).get('source')==eid:db.execute("UPDATE kg_aliases SET state='retracted' WHERE id=?",(alias['id'],))
    for claim in db.rows("SELECT claim_version_id FROM kg_assertions WHERE project_id=? AND json_extract(object_json,'$.entity_resolution.source_entity_id')=?",(pid,eid)):
        db.execute("UPDATE kg_assertions SET lifecycle='stale' WHERE claim_version_id=?",(claim['claim_version_id'],));lineage.changed(pid,'assertion',claim['claim_version_id'],'归并决策已撤回，关联论断需重新整理')
    db.audit(pid,'knowledge_entity_unmerge',{'entity_id':eid,**body});lineage.changed(pid,'entity_version',row['current_version_id'],'归并已撤销，相关知识需复查')
    return get_entity(pid,eid)

@db.atomic
def split_entity(pid,eid,body):
    original=get_entity(pid,eid);ids=body.get('claim_ids',[]);name=str(body.get('name','')).strip();reviewer=str(body.get('reviewer','')).strip();note=str(body.get('note','')).strip()
    if original['redirect_id'] or original['kind']=='document' or not name or not reviewer or not note or not isinstance(ids,list) or not 1<=len(ids)<=60:lineage.fail('INVALID_ENTITY_SPLIT','请选择1至60条关联论断，并填写新实体名称、核对人与拆分理由',422)
    selected=[get_assertion(pid,cid) for cid in dict.fromkeys(ids)]
    if any(eid not in (a['subject_id'],a['object_id']) for a in selected):lineage.fail('INVALID_ENTITY_SPLIT','只能拆分关联到当前实体的论断',422)
    for a in selected:lineage.version_of(pid,'assertion',a['claim_version_id'])
    created=entity(pid,{'namespace':original['namespace'],'kind':original['kind'],'key':'split:'+eid+':'+db.uid(),'name':name,'aliases':[original['name']],
                        'description':original['description'],'sources':original['sources'],'method':'human_resolution'})
    observation_ids=sorted({r['id'] for a in selected for r in a['sources'] if r['type']=='observation'})
    decision={'source_entity_id':eid,'reviewer':reviewer,'note':note,'observation_ids':observation_ids,'statements':[a['proposition']['statement'] for a in selected],'created_at':db.now()}
    db.execute("UPDATE kg_aliases SET state='human',decision_json=? WHERE entity_id=? AND normalized=?",(db.dumps(decision),created['id'],normalized(original['name'])))
    revisions=[]
    for old in selected:
        c=db.one('SELECT * FROM claim_versions WHERE id=? AND project_id=?',(old['claim_version_id'],pid))
        linked=[{'evidence_id':e['evidence_id'],'relation':e['relation'],'scope':db.loads(e['scope_json'],{})} for e in old['evidence']]
        fresh=assertion(pid,{'namespace':old['namespace'],'claim_key':c['claim_key'],'kind':c['kind'],'statement':old['proposition']['statement'],
                            'subject_id':created['id'] if old['subject_id']==eid else old['subject_id'],'object_id':created['id'] if old['object_id']==eid else old['object_id'],
                            'predicate':old['predicate'],'object':old['object'],'attribution':old['attribution'],'conditions':old['conditions'],'polarity':old['polarity'],'modality':old['modality'],
                            'sources':[*old['sources'],lineage.ref(pid,'entity_version',created['current_version_id'])],'evidence':linked,'method':'human_entity_resolution'})
        db.execute("UPDATE kg_assertions SET lifecycle='superseded' WHERE claim_version_id=?",(old['claim_version_id'],));lineage.changed(pid,'assertion',old['claim_version_id'],'实体已拆分，原论断版本保留并复查派生知识');revisions.append(fresh['claim_version_id'])
    db.audit(pid,'knowledge_entity_split',{'original_id':eid,'new_id':created['id'],'old_claim_ids':ids,'new_claim_ids':revisions,**decision})
    return {'entity':created,'claim_ids':revisions,'human_support_checks_copied':False,'decision':decision}

@db.atomic
def assertion(pid,body):
    from .writing_agent import claims
    ns=namespace(body.get('namespace','literature'));refs=list(body.get('sources',[]));statement=str(body.get('statement','')).strip()
    if not statement or len(statement)>12000 or not refs:lineage.fail('INVALID_ASSERTION','论断需要明确陈述与来源',422)
    lineage.validate_refs(pid,refs)
    subject=body.get('subject_id');obj=body.get('object_id')
    for identity in [subject,obj]:
        if identity:
            e=lineage.owned(pid,'entity',identity)
            if e['namespace']!=ns:lineage.fail('INVALID_NAMESPACE','关系端点范围不一致',422)
            if e['kind']!='document':refs.append(lineage.ref(pid,'entity_version',e['current_version_id']))
    refs=list({(r['type'],r['id'],r['version']):r for r in refs}.values());lineage.validate_refs(pid,refs)
    proposition={'statement':statement,'conditions':body.get('conditions',[]),'modality':body.get('modality','reported'),'attribution':body.get('attribution',''),'semantic_support':'not_checked'}
    signature=lineage.digest([ns,subject,body.get('predicate','reports'),obj,body.get('object',{}),proposition,refs])
    old=db.one("SELECT claim_version_id FROM kg_assertions WHERE project_id=? AND content_hash=? AND lifecycle NOT IN ('stale','excluded','superseded')",(pid,signature))
    if old:return get_assertion(pid,old['claim_version_id'])
    evidence_links=body.get('evidence',[])
    created=claims.create(pid,{'claim_key':body.get('claim_key') or 'knowledge:'+signature,'kind':body.get('kind','fact'),'proposition':proposition,'evidence':evidence_links})
    cid=created['id'];stamp=db.now()
    db.execute('INSERT INTO kg_assertions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,pid,ns,subject,str(body.get('predicate','reports')),obj,db.dumps(body.get('object',{})),body.get('attribution',''),db.dumps(body.get('conditions',[])),body.get('polarity','unknown'),body.get('modality','reported'),body.get('method','candidate'),'candidate',signature,stamp))
    lineage.add_dependencies(pid,'assertion',cid,signature,refs)
    return get_assertion(pid,cid)

def get_assertion(pid,cid,remote=False):
    from .writing_agent.claims import get
    row=lineage.owned(pid,'assertion',cid);claim=get(pid,cid)
    try:version_hash=lineage.version_of(pid,'assertion',cid,remote);current=True
    except HTTPException:
        if remote:raise
        current=False;version_hash=None
    checks=db.rows('SELECT * FROM kg_support_assessments WHERE claim_version_id=? ORDER BY created_at,id',(cid,))
    from .knowledge_decisions import state
    decision=state(pid,cid,checks) if current else {'support_state':'not_checked','human_reviewed':False,'requires_adjudication':False}
    return {**row,'proposition':claim['proposition'],'evidence':claim['evidence'],'conditions':db.loads(row['conditions_json'],[]),
            'object':db.loads(row['object_json'],{}),'sources':lineage.references(pid,'assertion',cid),'assessments':checks,'current':current,
            'version_hash':version_hash,**decision}

@db.atomic
def assess(pid,cid,body,method='human'):
    row=get_assertion(pid,cid);lineage.version_of(pid,'assertion',cid)
    oid=body.get('observation_id');eid=body.get('evidence_id');kind='observation' if oid else 'evidence';identity=oid or eid
    if not identity:lineage.fail('INVALID_ASSESSMENT','请选择具体原文或证据',422)
    allowed={(r['type'],r['id']) for r in row['sources']}
    if (kind,identity) not in allowed:lineage.fail('INVALID_ASSESSMENT','核对来源必须属于该论断',422)
    if kind=='observation':
        source=lineage.owned(pid,kind,identity);text=source['text']
        scope=db.loads(source['metadata_json'],{}).get('observation_scope') or {}
        if scope.get('cannot_support_claims') and body.get('support')=='supported':lineage.fail('SOURCE_SCOPE_UNVERIFIED','该图像观察尚不能支持完整论断，请先对照原件修订转录')
    else:
        from .writing_agent.evidence import get
        payload=get(pid,identity)['payload'];text=payload.get('text') or db.dumps(payload)
    quote=body.get('quote','');support=body.get('support','not_checked');reviewer=str(body.get('reviewer','')).strip();note=str(body.get('note','')).strip()
    if support not in ('supported','partial','contradicted','insufficient','not_checked') or not note or method not in ('human','model'):lineage.fail('INVALID_ASSESSMENT','支持性状态、方法或理由无效',422)
    if support in ('supported','partial','contradicted') and (not quote or quote not in text):lineage.fail('INVALID_QUOTE','需要逐字匹配当前来源的引句',422)
    if method=='human' and (not reviewer or body.get('compared_original') is not True):lineage.fail('ORIGINAL_COMPARISON_REQUIRED','人工核对需要核对人及明确原件对照',422)
    aid=db.uid();version=lineage.version_of(pid,kind,identity)
    db.execute('INSERT INTO kg_support_assessments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(aid,pid,cid,eid,oid,version,support,quote,db.dumps(body.get('conditions',[])),method,reviewer,note,db.now()))
    lineage.changed(pid,'assertion',cid,'论断支持性记录变化，相关综合与正文需复查')
    return db.one('SELECT * FROM kg_support_assessments WHERE id=?',(aid,))

def search(pid,query,ns='all',depth=1,node_limit=60,edge_limit=100,remote=False,file_ids=None):
    security.require_project(pid)
    if not isinstance(query,str) or len(query)>5000 or ns not in NAMESPACES|{'all'} or type(depth) is not int or not 0<=depth<=2:lineage.fail('INVALID_QUERY','图谱查询参数无效',422)
    if type(node_limit) is not int or not 1<=node_limit<=100 or type(edge_limit) is not int or not 1<=edge_limit<=200:lineage.fail('INVALID_LIMIT','图谱展开范围无效',422)
    sql='SELECT e.*,v.name,v.description FROM kg_entities e JOIN kg_entity_versions v ON v.id=e.current_version_id WHERE e.project_id=? AND e.redirect_id IS NULL';args=[pid]
    if ns!='all':sql+=' AND e.namespace=?';args.append(ns)
    entities=db.rows(sql,args);release_manifest=None
    scope=lineage.descendants_of_files(pid,file_ids or [])
    if scope is not None:entities=[e for e in entities if e['current_version_id'] in scope.get('entity_version',set())]
    if db.setting('knowledge_release_locked:'+pid,False):
        release_manifest=db.loads(lineage.owned(pid,'release',db.setting('knowledge_release:'+pid))['manifest_json'],{})
        allowed_entities={e['id'] for e in release_manifest.get('entity_heads',[])};entities=[e for e in entities if e['id'] in allowed_entities]
    ranked=sorted(entities,key=lambda e:(-relevance(e['name']+' '+e['description'],query),e['id']))
    seeds={e['id'] for e in ranked[:min(15,node_limit)] if not query or relevance(e['name']+' '+e['description'],query)}
    for alias in db.rows("SELECT * FROM kg_aliases WHERE project_id=? AND state!='retracted'",(pid,)):
        if query and alias['entity_id'] in {e['id'] for e in entities} and (normalized(query) in alias['normalized'] or alias['normalized'] in normalized(query)):seeds.add(alias['entity_id'])
    rows=db.rows('SELECT a.*,c.proposition_json FROM kg_assertions a JOIN claim_versions c ON c.id=a.claim_version_id WHERE a.project_id=? AND a.lifecycle NOT IN (?,?,?)'+(' AND a.namespace=?' if ns!='all' else ''),[pid,'stale','excluded','superseded']+([ns] if ns!='all' else []))
    if scope is not None:rows=[r for r in rows if r['claim_version_id'] in scope.get('assertion',set())]
    if release_manifest is not None:
        allowed=set(release_manifest.get('assertion_ids',[]));rows=[r for r in rows if r['claim_version_id'] in allowed]
    rows.sort(key=lambda a:(-relevance(a['proposition_json'],query),a['claim_version_id']))
    selected=[];nodes=set(seeds);seen=set();truncated=False
    with lineage.validation_scope():
        for level in range(depth+1):
            frontier=set(nodes)
            for row in rows:
                cid=row['claim_version_id'];matches=not query or relevance(row['proposition_json'],query)>0
                if cid in seen or (level==0 and not matches) or (level>0 and row['subject_id'] not in frontier and row['object_id'] not in frontier):continue
                endpoints={x for x in (row['subject_id'],row['object_id']) if x}
                if len(selected)>=edge_limit or len(nodes|endpoints)>node_limit:truncated=True;continue
                try:item=get_assertion(pid,cid,remote)
                except HTTPException:continue
                if not item['current']:continue
                seen.add(cid);selected.append(item);nodes|=endpoints
        safe=[]
        for e in ranked:
            if e['id'] not in nodes:continue
            try:item=get_entity(pid,e['id'],remote)
            except HTTPException:continue
            if item['current']:safe.append(item)
    return {'nodes':safe[:node_limit],'assertions':selected,'edges':[{'id':a['claim_version_id'],'source':a['subject_id'],'target':a['object_id'],'relation':a['predicate'],'claim_id':a['claim_version_id'],'support_state':a['support_state']} for a in selected],
            'depth':depth,'truncated':truncated,'complete':False,'backend':'native_evidence_graph','scope':'bounded_query_expansion','semantic_support':'claim_specific_check_required'}

def code_projection(pid,query='',limit=60):
    from .writing_agent.common import state
    snapshot=state(pid)['target_snapshot_id']
    if not snapshot:return {'nodes':[],'edges':[],'snapshot_id':None,'scope':'no_target_snapshot'}
    rows=db.rows('SELECT * FROM code_units WHERE project_id=? AND snapshot_id=? ORDER BY relative_path,line_start LIMIT 5000',(pid,snapshot))
    chosen=sorted(rows,key=lambda r:-relevance(r['symbol']+' '+r['relative_path'],query))[:limit];ids={r['id'] for r in chosen}
    edges=[r for r in db.rows('SELECT * FROM code_edges WHERE snapshot_id=?',(snapshot,)) if r['from_unit_id'] in ids and r['to_unit_id'] in ids][:100]
    return {'nodes':chosen,'edges':edges,'snapshot_id':snapshot,'scope':'target_snapshot_code_structure_not_runtime_proof','truncated':len(rows)>limit}
