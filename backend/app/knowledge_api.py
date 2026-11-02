"""Project-local graph and Wiki APIs; original evidence retains its own identity."""
from pathlib import Path
from fastapi import APIRouter,UploadFile,File
from fastapi.responses import FileResponse
from . import db, security, tasks
from . import knowledge_lineage as lineage, knowledge_graph as graph, knowledge_wiki as wiki, knowledge_builds as builds

router=APIRouter(prefix='/projects/{pid}/knowledge')

@router.get('/completion')
def completion_api(pid:str):
    from .knowledge_acceptance import status
    return status(pid)

@router.get('/evaluation/annotation-packet')
def annotation_packet_api(pid:str):
    from .knowledge_acceptance import annotation_packet
    return annotation_packet(pid)

@router.get('/real-paper/acceptance')
def real_paper_status_api(pid:str):
    from .knowledge_acceptance import real_paper_status
    return real_paper_status(pid)

@router.post('/real-paper/acceptance')
def real_paper_review_api(pid:str,body:dict):
    from .knowledge_acceptance import review_real_paper
    return review_real_paper(pid,body)

@db.read_session()
def status(pid):
    project=security.require_project(pid)
    from . import provider
    from .summary_billing import budget
    from .expense_evidence import policy,resolve_config,source_for
    charge=db.one("SELECT COALESCE(SUM(budget_charge),0) AS amount FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(pid,))['amount']
    counts={name:db.one('SELECT COUNT(*) AS n FROM '+name+' WHERE project_id=?',(pid,))['n'] for name in ('knowledge_observations','kg_entities','kg_assertions','wiki_pages','wiki_versions','knowledge_events','knowledge_evaluation_cases','knowledge_comparisons')}
    from .graphrag_adapter import capabilities
    cost=budget(pid);rules=policy(pid);reasons=[]
    if rules['require_pricing_basis'] and rules['block_on_unresolved'] and cost['unknown_calls']:
        reasons.append('仍有'+str(cost['unknown_calls'])+'笔调用的实际费用待核实，请先在费用依据账本补齐证据')
    if cost['available']<=0:reasons.append('项目可用预算不足')
    if rules['require_pricing_basis']:
        try:
            from .summary_billing import key_hash
            key=provider.get_key();cfg=resolve_config(pid,provider.config(),key)
            if not key or not source_for(cfg,key_hash(key)):reasons.append('当前主模型接口尚未匹配完整的计价依据')
            if cfg.get('currency')!=cost['currency']:reasons.append('模型接口币种与项目账本不一致')
            if rules['block_on_unresolved'] and not reasons:
                from .expense_evidence import ledger
                if ledger(pid)['counts']['unsupported_cost']:reasons.append('账本中仍有金额缺少可核对依据')
        except Exception:reasons.append('当前计价依据暂不可验证，请检查接口与费用账本')
    from .knowledge_acceptance import semantic_compilation_status
    return {'counts':counts,'semantic_compilation':semantic_compilation_status(pid),'enabled':db.setting('graph_wiki_enabled:'+pid,False),'auto_refresh':db.setting('knowledge_auto_refresh:'+pid,True),'refresh_mode':db.setting('knowledge_refresh_mode:'+pid,'literal'),'release_id':db.setting('knowledge_release:'+pid),'native_graph':True,'wiki':True,
            'budget':{**cost,'charge':charge},'billing_gate':{'paid_requests_allowed':not reasons,'reasons':reasons,'strict':rules['require_pricing_basis'],'local_work_allowed':True},
            'plugin':capabilities(),'tasks':db.rows("SELECT id,kind,status,phase,progress,error,created_at,json_extract(payload,'$.mode') AS mode FROM tasks WHERE project_id=? AND kind LIKE 'knowledge_%' OR project_id=? AND kind='graph_wiki_build' ORDER BY created_at DESC LIMIT 20",(pid,pid)),
            'semantic_quality_verified':False,'schema_version':db.one('PRAGMA user_version')['user_version']}

@router.get('/status')
def status_api(pid:str):return status(pid)

@router.put('/settings')
def settings_api(pid:str,body:dict):
    security.require_project(pid)
    if type(body.get('enabled')) is not bool:lineage.fail('INVALID_SETTINGS','请提供知识写作开关',422)
    if 'auto_refresh' in body:
        if type(body['auto_refresh']) is not bool:lineage.fail('INVALID_SETTINGS','自动更新开关无效',422)
        db.set_setting('knowledge_auto_refresh:'+pid,body['auto_refresh'])
    if 'refresh_mode' in body:
        if body['refresh_mode'] not in ('literal','llm'):lineage.fail('INVALID_SETTINGS','自动更新模式无效',422)
        cap=body.get('refresh_budget',0)
        if isinstance(cap,bool) or not isinstance(cap,(int,float)) or cap<0 or not __import__('math').isfinite(cap):lineage.fail('INVALID_SETTINGS','自动更新预算无效',422)
        db.set_setting('knowledge_refresh_mode:'+pid,body['refresh_mode']);db.set_setting('knowledge_refresh_budget:'+pid,cap)
    db.set_setting('graph_wiki_enabled:'+pid,body['enabled']);return {'enabled':body['enabled']}

@router.post('/builds/preview')
def preview_api(pid:str,body:dict):return builds.preview(pid,body)

@router.post('/builds')
def build_api(pid:str,body:dict):return builds.start(pid,body)

@router.post('/builds/{identity}/resume')
def resume_api(pid:str,identity:str,body:dict):return builds.resume(pid,identity,body)

@router.get('/builds/{identity}')
def build_detail(pid:str,identity:str):
    security.require_project(pid);task=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(identity,pid))
    if not task:lineage.fail('BUILD_NOT_FOUND','任务不存在',404)
    return {'task':{**task,'result':db.loads(task['result'],{})},'items':db.rows('SELECT * FROM knowledge_build_items WHERE task_id=? AND project_id=?',(identity,pid))}

@router.post('/graph/search')
def graph_api(pid:str,body:dict):return graph.search(pid,body.get('query',''),body.get('namespace','all'),body.get('depth',1),body.get('node_limit',60),body.get('edge_limit',100),bool(body.get('remote')),file_ids=body.get('file_ids'))

@router.get('/graph/code')
def code_api(pid:str,query:str=''):return graph.code_projection(pid,query)

@router.get('/entities/{identity}')
def entity_api(pid:str,identity:str):
    entity=graph.get_entity(pid,identity)
    entity['related_claims']=db.rows("SELECT a.claim_version_id,json_extract(c.proposition_json,'$.statement') AS statement FROM kg_assertions a JOIN claim_versions c ON c.id=a.claim_version_id WHERE a.project_id=? AND (a.subject_id=? OR a.object_id=?) AND a.lifecycle NOT IN ('stale','excluded','superseded') LIMIT 200",(pid,identity,identity))
    return entity

@router.post('/entities')
def add_entity_api(pid:str,body:dict):return graph.entity(pid,body)

@router.post('/entities/merge-proposals')
def merge_api(pid:str,body:dict):
    if body.get('apply') is True:return graph.merge(pid,body)
    a=graph.get_entity(pid,body.get('source_id'));b=graph.get_entity(pid,body.get('target_id'))
    return {'source':a,'target':b,'expected_source_version':a['current_version_id'],'expected_target_version':b['current_version_id'],'automatic_merge':False}

@router.post('/entities/{identity}/unmerge')
def unmerge_api(pid:str,identity:str,body:dict):return graph.unmerge(pid,identity,body)

@router.post('/entities/{identity}/split')
def split_api(pid:str,identity:str,body:dict):return graph.split_entity(pid,identity,body)

@router.get('/assertions/{identity}')
def assertion_api(pid:str,identity:str):return graph.get_assertion(pid,identity)

@router.post('/assertions')
def add_assertion_api(pid:str,body:dict):return graph.assertion(pid,body)

@router.post('/assertions/{identity}/assessments')
def assessment_api(pid:str,identity:str,body:dict):return graph.assess(pid,identity,body)

@router.post('/assess-with-model')
def assess_model_api(pid:str,body:dict):
    for cid in body.get('claim_ids',[]):lineage.version_of(pid,'assertion',cid,True)
    return tasks.enqueue(pid,'knowledge_assess',body)

@router.get('/observations/{identity}')
def observation_api(pid:str,identity:str):
    row=lineage.owned(pid,'observation',identity);lineage.version_of(pid,'observation',identity);return {**row,'locator':db.loads(row['locator_json'],{}),'metadata':db.loads(row['metadata_json'],{})}

@router.get('/evidence/{identity}')
def evidence_api(pid:str,identity:str):
    from .writing_agent.evidence import get
    lineage.version_of(pid,'evidence',identity);record=get(pid,identity)
    return {**record,'quote_text':record['payload'].get('text') or db.dumps(record['payload'])}

@router.get('/wiki')
def pages_api(pid:str,query:str='',namespace:str='all',file_id:str=''):
    security.require_project(pid)
    if query:return wiki.search(pid,query,namespace,100,file_ids=[file_id] if file_id else None)
    rows=db.rows('SELECT * FROM wiki_pages WHERE project_id=?'+(' AND namespace=?' if namespace!='all' else '')+' ORDER BY namespace,title',[pid]+([namespace] if namespace!='all' else []))
    scope=lineage.descendants_of_files(pid,[file_id] if file_id else [])
    result=[]
    for r in rows:
        versions=db.rows('SELECT id,parent_id,state,created_at FROM wiki_versions WHERE page_id=? ORDER BY created_at DESC',(r['id'],))
        if scope is not None and not any(v['id'] in scope.get('wiki_version',set()) and set(lineage.source_files(pid,lineage.references(pid,'wiki_version',v['id'])))<={file_id} for v in versions):continue
        head=next((v for v in versions if v['id']==r['current_version_id']),None)
        result.append({**r,'current':False if not head or head['state']=='stale' else None,'versions':versions,'source_validation':'rechecked_on_read'})
    return {'items':result}

@router.post('/wiki')
def create_page_api(pid:str,body:dict):return wiki.create_page(pid,body)

@router.get('/wiki/{identity}')
def page_api(pid:str,identity:str,version_id:str=''):return wiki.get_page(pid,identity,version_id or None)

@router.post('/wiki/{identity}/revisions')
def revision_api(pid:str,identity:str,body:dict):
    from .knowledge_wiki_merge import submit
    return submit(pid,identity,body)

@router.get('/wiki-conflicts')
def wiki_conflicts_api(pid:str):
    security.require_project(pid)
    return db.rows("SELECT id,page_id,base_version_id,current_version_id,author,state,created_at FROM wiki_edit_conflicts WHERE project_id=? ORDER BY created_at DESC",(pid,))

@router.get('/wiki-conflicts/{identity}')
def wiki_merge_preview(pid:str,identity:str):
    from .knowledge_wiki_merge import preview
    return preview(pid,identity)

@router.post('/wiki-conflicts/{identity}/resolve')
def wiki_merge_resolve(pid:str,identity:str,body:dict):
    from .knowledge_wiki_merge import resolve
    return resolve(pid,identity,body)

@router.post('/assertions/{identity}/decisions')
def decide_support_api(pid:str,identity:str,body:dict):
    from .knowledge_decisions import adjudicate
    return adjudicate(pid,identity,body)

@router.post('/wiki/{identity}/publish')
def publish_api(pid:str,identity:str,body:dict):return wiki.publish(pid,identity,body)

@router.get('/wiki/{identity}/diff')
def diff_api(pid:str,identity:str,left:str,right:str):return wiki.diff(pid,identity,left,right)

@router.post('/wiki/{identity}/synthesize')
def synthesis_api(pid:str,identity:str,body:dict):
    page=lineage.owned(pid,'wiki_page',identity);lineage.validate_refs(pid,body.get('sources',[]),True)
    return tasks.enqueue(pid,'knowledge_synthesize',{**body,'namespace':page['namespace'],'slug':page['slug'],'title':page['title'],'kind':page['kind']})

@router.post('/query')
def query_api(pid:str,body:dict):
    from .knowledge_router import query
    return query(pid,body,bool(body.get('remote')))

@router.post('/query/route-preview')
def route_preview_api(pid:str,body:dict):
    security.require_project(pid)
    from .knowledge_planning import route_plan
    return route_plan(pid,body)

@router.post('/query/execute')
def unified_query_api(pid:str,body:dict):
    security.require_project(pid)
    return tasks.enqueue(pid,'knowledge_query',body)

@router.post('/query/answer')
def answer_api(pid:str,body:dict):
    security.require_project(pid);return tasks.enqueue(pid,'knowledge_answer',body)

@router.get('/answers')
def answers_api(pid:str):
    security.require_project(pid)
    return [{'id':r['id'],'manifest':db.loads(r['manifest_json'],{}),'created_at':r['created_at']} for r in db.rows("SELECT * FROM knowledge_artifacts WHERE project_id=? AND kind='knowledge_answer' ORDER BY created_at DESC LIMIT 20",(pid,))]

@router.get('/answers/{identity}')
def answer_detail_api(pid:str,identity:str):
    import json
    from .config import DATA
    artifact=lineage.owned(pid,'artifact',identity);path=Path(artifact['path']).resolve()
    if artifact['kind']!='knowledge_answer' or not path.is_relative_to((DATA/'knowledge'/'answers').resolve()):lineage.fail('INVALID_ANSWER','回答对象无效',422)
    return {'id':identity,'answer':json.loads(path.read_text(encoding='utf-8')),'manifest':db.loads(artifact['manifest_json'],{})}

@router.post('/answers/{identity}/save-wiki')
@db.atomic
def save_answer_api(pid:str,identity:str,body:dict):
    import json
    artifact=lineage.owned(pid,'artifact',identity);lineage.version_of(pid,'artifact',identity)
    if artifact['kind']!='knowledge_answer':lineage.fail('INVALID_ANSWER','请选择知识回答',422)
    from .config import DATA
    path=Path(artifact['path']).resolve()
    if not path.is_relative_to((DATA/'knowledge'/'answers').resolve()):lineage.fail('INVALID_ANSWER_PATH','回答文件路径无效',422)
    content=json.loads(path.read_text(encoding='utf-8'));page=wiki.create_page(pid,body)
    # Keep original references; saving an answer never creates independent evidence.
    revision=wiki.revise(pid,page['id'],{'base_version_id':page['current_version_id'],'blocks':[{'key':str(i),**b} for i,b in enumerate(content['blocks'])]},'model')
    return {'page_id':page['id'],'version_id':revision['version']['id'],'published':False}

@router.post('/reading-plan')
def reading_plan_api(pid:str,body:dict):
    from .knowledge_router import query,prepare_readings
    result=query(pid,body,True);return {**result,'reading':prepare_readings(pid,body['query'],body['section_id'],result['reading_plan'])}

@router.get('/lineage')
def lineage_api(pid:str,kind:str,identity:str):return lineage.trace(pid,kind,identity)

@router.post('/checks')
def checks_api(pid:str,body:dict):
    from .knowledge_wiki_checks import check
    if body.get('semantic') is True:return tasks.enqueue(pid,'knowledge_semantic_check',body)
    return check(pid)

@router.get('/maintenance')
def maintenance_api(pid:str,namespace:str='all',file_id:str=''):
    from .knowledge_maintenance import status
    return status(pid,namespace,[file_id] if file_id else None)

@router.post('/maintenance/preview')
def maintenance_preview_api(pid:str,body:dict):
    from .knowledge_maintenance import prepare
    return prepare(pid,body.get('namespace','all'),body.get('support_checks',False),body.get('discover_topics',False),body.get('reports',True),body.get('file_ids'))

@router.post('/maintenance/run')
def maintenance_run_api(pid:str,body:dict):
    security.require_project(pid)
    return tasks.enqueue(pid,'knowledge_maintenance',body)

@router.get('/releases')
def releases_api(pid:str):
    security.require_project(pid);return db.rows('SELECT id,parent_id,state,created_at FROM knowledge_releases WHERE project_id=? ORDER BY created_at DESC',(pid,))

@router.post('/releases')
def release_api(pid:str,body:dict):return builds.publish_release(pid,body)

@router.post('/releases/{identity}/activate')
def activate_release_api(pid:str,identity:str):return builds.rollback(pid,identity)

@router.post('/exports')
def exports_api(pid:str,body:dict):
    security.require_project(pid);return tasks.enqueue(pid,'knowledge_export',body)

@router.get('/exports/{identity}')
def download_api(pid:str,identity:str):
    from .config import DATA
    artifact=lineage.owned(pid,'artifact',identity);path=Path(artifact['path']).resolve();base=(DATA/'exports'/'knowledge').resolve()
    if not path.is_relative_to(base) or artifact['kind']!='knowledge_export' or not path.is_file():lineage.fail('EXPORT_NOT_FOUND','知识导出不存在',404)
    return FileResponse(path,filename='知识库-'+identity[:8]+'.zip')

@router.post('/evaluation/cases/seed')
def seed_cases_api(pid:str):
    from .knowledge_comparisons import seed
    return seed(pid)

@router.get('/evaluation/cases')
def cases_api(pid:str):
    security.require_project(pid);return db.rows('SELECT * FROM knowledge_evaluation_cases WHERE project_id=? ORDER BY category,created_at',(pid,))

@router.put('/evaluation/cases/{identity}')
def case_api(pid:str,identity:str,body:dict):
    from .knowledge_comparisons import update_case
    return update_case(pid,identity,body)

@router.post('/evaluation/comparisons')
def compare_api(pid:str,body:dict):
    security.require_project(pid);return tasks.enqueue(pid,'knowledge_compare',body)

@router.get('/evaluation/comparisons')
def comparisons_api(pid:str):
    security.require_project(pid)
    return [{k:r[k] for k in ('id','task_id','state','created_at')}|{'case_count':len(db.loads(r['result_json'],[])),
        'generation':db.loads(r['manifest_json'],{}).get('generation')}
        for r in db.rows('SELECT * FROM knowledge_comparisons WHERE project_id=? ORDER BY created_at DESC',(pid,))]

@router.post('/evaluation/comparisons/{identity}/reviews')
def review_api(pid:str,identity:str,body:dict):
    from .knowledge_comparisons import review
    return review(pid,identity,body)

@router.get('/evaluation/comparisons/{identity}/blind')
def blind_api(pid:str,identity:str,reveal:bool=False):
    from .knowledge_comparisons import blind
    return blind(pid,identity,reveal)

@router.get('/evaluation/comparisons/{identity}/report')
def evaluation_report_api(pid:str,identity:str):
    from .knowledge_comparisons import report
    return report(pid,identity)

@router.post('/graphrag/build')
def plugin_build_api(pid:str,body:dict):
    security.require_project(pid);return tasks.enqueue(pid,'knowledge_graphrag_build',body)

@router.post('/graphrag/query')
def plugin_query_api(pid:str,body:dict):
    security.require_project(pid);return tasks.enqueue(pid,'knowledge_graphrag_query',body)

@router.post('/transfers/export')
def transfer_export_api(pid:str):
    security.require_project(pid);return tasks.enqueue(pid,'knowledge_bundle_export',{})

@router.get('/transfers/{identity}/download')
def transfer_download_api(pid:str,identity:str):
    from .config import DATA
    row=lineage.owned(pid,'artifact',identity);path=Path(row['path']).resolve()
    if row['kind']!='knowledge_bundle' or not path.is_relative_to((DATA/'exports'/'knowledge').resolve()) or not path.is_file():lineage.fail('BUNDLE_NOT_FOUND','完整知识包不存在',404)
    import hashlib
    if hashlib.sha256(path.read_bytes()).hexdigest()!=row['content_hash']:lineage.fail('BUNDLE_CHANGED','知识包哈希不匹配')
    return FileResponse(path,filename='complete-knowledge-'+identity[:8]+'.zip')

@router.post('/transfers/preview')
async def transfer_preview_api(pid:str,file:UploadFile=File(...)):
    from .knowledge_transfer import preview,MAX_ZIP
    security.require_project(pid);raw=await file.read(MAX_ZIP+1);result=preview(raw);blob=security.put_blob(raw)
    db.set_setting('knowledge_transfer_preview:'+pid+':'+blob,{'sha256':result['sha256'],'created_at':db.now()})
    return {**result,'package_blob':blob}

@router.post('/transfers/restore')
def transfer_restore_api(pid:str,body:dict):
    from .knowledge_transfer import restore
    security.require_project(pid);blob=body.get('package_blob','')
    allowed=db.setting('knowledge_transfer_preview:'+pid+':'+blob) or (blob==db.setting('knowledge_cloud_download:'+pid))
    if not allowed:lineage.fail('BUNDLE_PREVIEW_REQUIRED','请先在当前项目预览该知识包',422)
    return restore(security.safe_object(blob).read_bytes(),body)

@router.post('/cloud/{direction}')
def complete_cloud_api(pid:str,direction:str,body:dict):
    from .knowledge_transfer import cloud
    if direction not in ('push','pull'):lineage.fail('INVALID_CLOUD_OPERATION','同步方向无效',422)
    return cloud(pid,body,pull=direction=='pull')
