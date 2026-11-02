"""Recoverable knowledge builds share the existing task and billing ledgers."""
import math
from contextvars import ContextVar
from fastapi import HTTPException
from . import db, security, tasks, provider, material_policy as policy
from . import knowledge_lineage as lineage, knowledge_compiler as compiler, knowledge_wiki as wiki
_BUILDING=ContextVar('knowledge_compiling_files',default=frozenset())

def queue_refresh(pid,fid=None):
    if (pid,fid) in _BUILDING.get() or not db.setting('graph_wiki_enabled:'+pid,False) or not db.setting('knowledge_auto_refresh:'+pid,True):return None
    if fid:
        original=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(fid,pid))
        if not original or not original['active']:return None
    for task in db.rows("SELECT * FROM tasks WHERE project_id=? AND kind='knowledge_refresh' AND status='queued'",(pid,)):
        if db.loads(task['payload'],{}).get('file_id')==fid:return task
    return tasks.enqueue(pid,'knowledge_refresh',{'file_id':fid,'mode':db.setting('knowledge_refresh_mode:'+pid,'literal'),'budget_limit':db.setting('knowledge_refresh_budget:'+pid,0)})

def refresh(task,body):
    pid=task['project_id'];fid=body.get('file_id');mode=body.get('mode','literal')
    if fid:
        original=db.one('SELECT * FROM files WHERE id=? AND project_id=?',(fid,pid))
        if not original or not original['active']:return {'complete':True,'skipped':'来源已归档'}
        scope='literature' if policy.is_research(original) else 'guidance';request={**body,'namespace':scope,'file_ids':[fid]}
    else:request={**body,'namespace':'project'}
    return process(task,{**request,'planned_files':preview(pid,request)['files']})

def preview(pid,body):
    project=security.require_project(pid);mode=body.get('mode','llm');ns=body.get('namespace','literature')
    cap=body.get('budget_limit',project['budget'])
    if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not math.isfinite(cap) or cap<0:lineage.fail('INVALID_BUDGET','预算必须为非负有限数值',422)
    if mode not in ('literal','llm') or ns not in ('all','literature','guidance','project'):lineage.fail('INVALID_BUILD','构建方式或范围无效',422)
    selected=body.get('file_ids',[])
    if not isinstance(selected,list) or any(not isinstance(x,str) for x in selected):lineage.fail('INVALID_BUILD','文件清单无效',422)
    files=[]
    with lineage.validation_scope():
        for original in db.rows("SELECT * FROM files WHERE project_id=? AND active=1 AND status='ready' ORDER BY name,id",(pid,)):
            category='guidance' if ns=='guidance' else 'literature' if policy.is_research(original) else 'guidance'
            if selected and original['id'] not in selected or ns not in ('all',category):continue
            intent='research' if category=='literature' else 'guidance'
            if not policy.source_context(original,intent)['allowed_for_intent']:continue
            if mode=='llm' and not security.file_allows_remote(original['id'],pid):continue
            _,head,stamp=lineage.file_stamp(pid,original['id']);rows=db.rows("SELECT text FROM knowledge_units WHERE parse_id=? AND review_state!='excluded'",(head['id'],)) if head else []
            calls=sum(sum(1 for _ in compiler.ranges(r['text'])) for r in rows) if mode=='llm' else 0
            files.append({'id':original['id'],'name':original['name'],'namespace':category,'source_version':stamp,'parse_id':head['id'] if head else None,'units':len(rows),'characters':sum(len(r['text']) for r in rows),'estimated_extraction_calls':calls})
    if selected and set(selected)-{f['id'] for f in files}:lineage.fail('BUILD_SCOPE_UNAVAILABLE','部分所选文件不属于当前范围、未就绪或禁止外发',422)
    if body.get('file_order')=='smallest_first':files.sort(key=lambda item:(item['estimated_extraction_calls'],item['name'],item['id']))
    return {'mode':mode,'namespace':ns,'files':files,'file_count':len(files),'estimated_extraction_calls':sum(f['estimated_extraction_calls'] for f in files),
            'estimate_note':'请求数不含语义复核、综合与社区；价格按项目接口配置及实际账本结算','budget_limit':min(float(body.get('budget_limit',project['budget'])),project['budget']),
            'input_hash':lineage.digest([mode,ns,files]),'paid_requests':mode=='llm','project_evidence':ns in ('all','project')}

def start(pid,body):
    plan=preview(pid,body);budget=body.get('budget_limit',plan['budget_limit'])
    if isinstance(budget,bool) or not isinstance(budget,(int,float)) or not math.isfinite(budget) or budget<0:lineage.fail('INVALID_BUDGET','预算必须为非负有限数值',422)
    payload={**body,'mode':plan['mode'],'namespace':plan['namespace'],'planned_files':plan['files'],'budget_limit':budget,'preview_hash':plan['input_hash']}
    for running in db.rows("SELECT * FROM tasks WHERE project_id=? AND kind='graph_wiki_build' AND status IN ('queued','running')",(pid,)):
        if db.loads(running['payload'],{}).get('preview_hash')==plan['input_hash']:return running
    return tasks.enqueue(pid,'graph_wiki_build',payload,body.get('idempotency_key'))

def process(task,payload):
    pid=task['project_id'];results=[];failures=[];plan=payload.get('planned_files') or preview(pid,payload)['files'];mode=payload.get('mode','literal')
    for index,item in enumerate(plan):
        tasks.event(task['id'],'整理知识：'+item['name'],int(5+75*index/max(1,len(plan))))
        original,_,stamp=lineage.file_stamp(pid,item['id'])
        if item.get('source_version')!=stamp:
            failures.append({'file_id':item['id'],'code':'SOURCE_CHANGED','message':'预览后原文版本变化，请重新预览'});continue
        model_cfg=(__import__('app.model_routing',fromlist=['config']).config() if payload.get('provider_profile')=='gpt' else provider.config()) if mode=='llm' else {}
        key='file:'+item['id']+':'+item['namespace'];input_hash=lineage.digest([compiler.VERSION,compiler.MODEL_SCHEMA if mode=='llm' else None,mode,stamp,item['namespace'],{k:model_cfg.get(k) for k in ('base_url','model','protocol','max_output_tokens','context_limit','json_mode','thinking_enabled','reasoning_effort','temperature','top_p','seed')}]+([compiler.QUOTE_VALIDATION_VERSION] if mode=='llm' else []))
        iid=db.uid();old=db.one('SELECT * FROM knowledge_build_items WHERE project_id=? AND item_key=? AND input_hash=?',(pid,key,input_hash))
        if old:
            previous=db.loads(old['result_json'],{})
            if old['status']=='completed':
                try:
                    with lineage.validation_scope():
                        lineage.validate_refs(pid,[lineage.ref(pid,'assertion',cid) for cid in previous.get('assertion_ids',[])])
                        for pageid in previous.get('page_ids',[]):lineage.version_of(pid,'wiki_page',pageid)
                    results.append({**previous,'cache_hit':True});continue
                except HTTPException:pass
            iid=old['id']
        db.execute('INSERT INTO knowledge_build_items VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,item_key,input_hash) DO UPDATE SET task_id=excluded.task_id,status=excluded.status,updated_at=excluded.updated_at',
            (iid,pid,task['id'],key,input_hash,'file','running','{}',None,db.now()))
        try:
            def progress(done,total):tasks.event(task['id'],f"{item['name']}：{done}/{total} 原文单元",int(5+75*(index+done/max(1,total))/max(1,len(plan))))
            token=_BUILDING.set(_BUILDING.get()|{(pid,item['id'])})
            try:
                if mode=='literal':
                    # No remote requests occur in this scope. Recheck after closing it.
                    with lineage.validation_scope():result=compiler.compile_file(task,item['id'],mode,progress,item['namespace'])
                    with lineage.validation_scope():lineage.validate_refs(pid,[lineage.ref(pid,'assertion',cid) for cid in result['assertion_ids']])
                elif payload.get('batch_extraction'):
                    from .knowledge_batch_compiler import compile_file
                    result=compile_file(task,item['id'],mode,progress,item['namespace'])
                else:result=compiler.compile_file(task,item['id'],mode,progress,item['namespace'])
            finally:_BUILDING.reset(token)
            results.append(result)
            db.execute("UPDATE knowledge_build_items SET status='completed',result_json=?,error=NULL,updated_at=? WHERE id=?",(db.dumps(result),db.now(),iid))
        except (provider.ProviderError, __import__('app.summary_billing',fromlist=['BudgetExceeded']).BudgetExceeded):
            db.execute("UPDATE knowledge_build_items SET status='paused',updated_at=? WHERE id=?",(db.now(),iid));raise
        except InterruptedError:raise
        except Exception as error:
            message=security.redact(error);failures.append({'file_id':item['id'],'message':message})
            db.execute("UPDATE knowledge_build_items SET status='failed',error=?,updated_at=? WHERE id=?",(message,db.now(),iid))
    with lineage.validation_scope():
        project=compiler.compile_project_evidence(task) if payload.get('namespace') in ('all','project') else {}
        legacy=compiler.backfill_claims(pid);indexes=compiler.update_indexes(pid)
    topic_updates=[];maintenance=None
    from .knowledge_maintenance import prepare as plan_maintenance,run as run_maintenance
    plan_maintenance(pid,payload.get('namespace','all'),support_checks=mode=='llm' and payload.get('support_checks',True),
                     discover_topics=mode=='llm' and payload.get('discover_topics',True),reports=True)
    if mode=='llm' and not payload.get('defer_maintenance'):maintenance=run_maintenance(task,{**payload,'support_checks':False,'discover_topics':False})
    release=publish_release(pid,{'task_id':task['id'],'files':results,'failures':failures,'mode':mode})
    tasks.event(task['id'],'知识构建结果已保存',98)
    return {'files':results,'failures':failures,'project':project,'legacy':legacy,'index_pages':indexes,'topic_updates':topic_updates,'maintenance':maintenance,'maintenance_deferred':bool(payload.get('defer_maintenance')),'release':release,'mode':mode,'complete':not failures and (maintenance is None or maintenance['complete']),'semantic_quality_verified':False,
            'capability':'source_excerpt_index' if mode=='literal' else 'model_compiled_candidates'}

@db.atomic
def publish_release(pid,details=None):
    security.require_project(pid);refs=[];skipped=[]
    with lineage.validation_scope():
        for page in db.rows('SELECT id FROM wiki_pages WHERE project_id=? AND current_version_id IS NOT NULL',(pid,)):
            try:
                p=wiki.get_page(pid,page['id']);refs.append(lineage.ref(pid,'wiki_version',p['version']['id']))
            except HTTPException:skipped.append(page['id'])
    parent=db.setting('knowledge_release:'+pid);manifest={'refs':refs,'details':details or {},'skipped_stale_pages':skipped,'schema':'knowledge.release.v1',
        'assertion_ids':[r['claim_version_id'] for r in db.rows("SELECT claim_version_id FROM kg_assertions WHERE project_id=? AND lifecycle NOT IN ('stale','excluded','superseded')",(pid,))],
        'entity_heads':db.rows('SELECT id,current_version_id,redirect_id FROM kg_entities WHERE project_id=?',(pid,))}
    identity=db.uid();content=lineage.digest(manifest)
    db.execute('INSERT INTO knowledge_releases VALUES(?,?,?,?,?,?,?)',(identity,pid,parent,db.dumps(manifest),content,'published',db.now()))
    lineage.add_dependencies(pid,'release',identity,content,refs);db.set_setting('knowledge_release:'+pid,identity);db.set_setting('knowledge_release_locked:'+pid,False)
    return {'id':identity,'pages':len(refs),'skipped_stale_pages':skipped}

@db.atomic
def rollback(pid,identity):
    release=lineage.owned(pid,'release',identity);manifest=db.loads(release['manifest_json'],{})
    # Restore knowledge heads only; later editorial versions and all prose survive.
    db.execute('UPDATE wiki_pages SET current_version_id=NULL WHERE project_id=?',(pid,))
    for r in manifest.get('refs',[]):
        if r['type']!='wiki_version':continue
        version=lineage.owned(pid,'wiki_version',r['id']);db.execute('UPDATE wiki_pages SET current_version_id=? WHERE id=?',(r['id'],version['page_id']))
    for e in manifest.get('entity_heads',[]):
        lineage.owned(pid,'entity_version',e['current_version_id']);db.execute('UPDATE kg_entities SET current_version_id=?,redirect_id=? WHERE id=? AND project_id=?',(e['current_version_id'],e['redirect_id'],e['id'],pid))
    with lineage.validation_scope():
        for cid in manifest.get('assertion_ids',[]):
            try:
                lineage.validate_refs(pid,lineage.references(pid,'assertion',cid))
                db.execute("UPDATE kg_assertions SET lifecycle='candidate' WHERE project_id=? AND claim_version_id=? AND lifecycle IN ('stale','superseded')",(pid,cid))
            except HTTPException:pass
        for r in manifest.get('refs',[]):
            if r['type']!='wiki_version':continue
            try:
                lineage.validate_refs(pid,lineage.references(pid,'wiki_version',r['id']))
                db.execute("UPDATE wiki_versions SET state='published' WHERE id=? AND project_id=?",(r['id'],pid))
            except HTTPException:pass
    db.set_setting('knowledge_release:'+pid,identity);db.set_setting('knowledge_release_locked:'+pid,True)
    return {'id':identity,'state':release['state'],'manifest':manifest,'source_revalidation_required':True}

def resume(pid,identity,body):
    old=db.one('SELECT * FROM tasks WHERE id=? AND project_id=?',(identity,pid))
    if not old or old['kind']!='graph_wiki_build':lineage.fail('BUILD_NOT_FOUND','知识构建任务不存在',404)
    payload=db.loads(old['payload'],{})
    payload['budget_scope_id']=payload.get('budget_scope_id',old['id'])
    if old['status'] in ('outcome_unknown','completed_invalid') and not body.get('acknowledge_possible_duplicate_charge'):lineage.fail('MODEL_OUTCOME_UNRESOLVED','请先核对远端记录，或明确可能再次计费后重试')
    payload.update({k:v for k,v in body.items() if k in ('budget_limit','acknowledge_possible_duplicate_charge')})
    return start(pid,payload)
