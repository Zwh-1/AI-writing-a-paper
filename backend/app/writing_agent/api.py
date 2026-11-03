from fastapi import APIRouter,Request
from .. import db,security,tasks,documents,provider,summary_billing
from .common import owned,state,update_state,candidate,unblob,fail
from . import repository,evidence,claims,contracts,profiles,coordinator,sentences,verification,adoption,evaluations,paper_review,revision,check_registry

router=APIRouter(prefix='/projects/{pid}')

@router.get('/writing-state')
def project_state(pid):return state(pid)

@router.get('/sections/{sid}/materials')
def chapter_materials(pid, sid, q: str = ''):
    from ..writing_materials import load
    return load(pid, sid, q)

@router.get('/writing-link')
def writing_link(pid):
    """Return the shared state used by research, evidence writing, and the editor."""
    p=security.require_project(pid);head=state(pid)
    core_row=db.one('SELECT * FROM thesis_core_versions WHERE id=? AND project_id=?',(head['core_version_id'],pid)) if head['core_version_id'] else None
    core=db.loads(core_row['content_json'],{}) if core_row else None
    facts=[]
    for fact in db.rows('SELECT * FROM facts WHERE project_id=? ORDER BY created_at',(pid,)):
        synced=evidence.sync_fact(pid,fact['id'])
        usage=db.rows("SELECT DISTINCT s.id,s.title FROM dependencies d JOIN sections s ON s.current_revision_id=d.revision_id WHERE d.project_id=? AND d.upstream_type IN ('fact','knowledge:fact') AND d.upstream_id=? AND s.state!='archived' ORDER BY s.position",(pid,fact['id']))
        facts.append({**fact,'evidence_id':synced['id'],'used_by':[dict(row) for row in usage]})
    artifacts=[]
    for artifact in db.rows('SELECT * FROM artifacts WHERE project_id=? ORDER BY created_at DESC',(pid,)):
        synced=evidence.sync_artifact(pid,artifact['id'])
        artifacts.append({**artifact,'evidence_id':synced['id']})
    sections=[]
    for s in db.rows("SELECT * FROM sections WHERE project_id=? AND state!='archived' ORDER BY position",(pid,)):
        rev=documents.current(s);source_ids=set();fact_ids=set();artifact_ids=set()
        if rev:
            for node in documents.nodes(db.loads(rev['document'],{})):
                attrs=node.get('attrs',{});source_ids.update(attrs.get('sourceIds',[]));fact_ids.update(attrs.get('factIds',[]));artifact_id=attrs.get('artifactId')
                if artifact_id:artifact_ids.add(artifact_id)
        contract_id=head['contracts'].get(s['id']);contract_row=db.one('SELECT content_json FROM section_contract_versions WHERE id=? AND project_id=?',(contract_id,pid)) if contract_id else None
        contract=db.loads(contract_row['content_json'],{}) if contract_row else None
        if contract:
            source_ids.update(contract.get('evidence_ids',[]));fact_ids.update(contract.get('fact_ids',[]));artifact_ids.update(contract.get('artifact_ids',[]))
        session=db.one('SELECT id,status,updated_at FROM writing_sessions WHERE project_id=? AND section_id=? ORDER BY created_at DESC LIMIT 1',(pid,s['id']))
        sections.append({'section_id':s['id'],'title':s['title'],'goal':s['goal'],'revision_id':s['current_revision_id'],'stale':bool(s['stale']),'stale_reason':s['stale_reason'],'contract_id':contract_id,'contract':contract,'source_ids':sorted(source_ids),'fact_ids':sorted(fact_ids),'artifact_ids':sorted(artifact_ids),'session':dict(session) if session else None})
    for fact in facts:
        declared=[{'id':item['section_id'],'title':item['title']} for item in sections if fact['id'] in item['fact_ids']]
        seen={item['id'] for item in fact['used_by']}
        fact['used_by']=[*fact['used_by'],*[item for item in declared if item['id'] not in seen]]
    brief=db.loads(p['brief'],{});explicit_question=brief.get('question','') or (core or {}).get('research_question','');research_question=explicit_question or f'围绕“{p["title"]}”组织项目的需求、设计、实现与验证论证。'
    project_stats={name:db.one(f'SELECT COUNT(*) AS n FROM {table} WHERE project_id=?',(pid,))['n'] for name,table in [('files','files'),('rules','rules'),('sections','sections'),('facts','facts'),('artifacts','artifacts'),('references','bibliography')]}
    materials=db.rows("SELECT f.id,f.name,f.role,f.status,f.pages,COUNT(s.id) AS source_count FROM files f LEFT JOIN sources s ON s.file_id=f.id WHERE f.project_id=? AND f.active=1 GROUP BY f.id ORDER BY f.created_at DESC",(pid,))
    references=[{**r,'metadata':db.loads(r['metadata'],{}),'source_count':db.one('SELECT COUNT(*) AS n FROM sources WHERE file_id=?',(r['file_id'],))['n'] if r['file_id'] else 0} for r in db.rows('SELECT * FROM bibliography WHERE project_id=? ORDER BY created_at DESC',(pid,))]
    package=db.one('SELECT * FROM rule_packages WHERE id=? AND project_id=?',(p['rule_package_id'],pid)) if p['rule_package_id'] else None
    rules=[{**r,'operations':db.loads(r['operations'],[])} for r in db.rows("SELECT * FROM rules WHERE project_id=? AND state IN ('confirmed','machine_verified') ORDER BY created_at",(pid,))]
    ready_snapshot=db.one("SELECT id,created_at,head_commit FROM repository_snapshots WHERE project_id=? AND state='ready' ORDER BY created_at DESC LIMIT 1",(pid,))
    model_configuration=provider.config();model={'name':model_configuration.get('model',''),'configured':bool(security.key_status()['configured'] and model_configuration.get('model'))}
    blockers=[]
    if not p['title']: blockers.append('项目缺少有效题目')
    if not sections: blockers.append('项目尚未建立提纲')
    if not p['rule_package_id']: blockers.append('尚未发布论文规则包')
    if not head['target_snapshot_id'] and not ready_snapshot: blockers.append('尚未形成可用的项目代码快照')
    try:
        reserve,_=summary_billing.request_reservation(model_configuration,'','')
        budget=summary_billing.budget(pid,reserve)
        if not budget['can_call']:blockers.append(f'项目可用预算 {budget["available"]:.4f} {model_configuration.get("currency","USD")}，不足单次请求预留 {reserve:.4f}；请核对费用预留或调整项目预算')
    except summary_billing.BudgetExceeded as error:blockers.append(str(error))
    return {'project':{'id':p['id'],'title':p['title'],'paper_type':p['paper_type'],'target_words':p['target_words'],'rule_package_id':p['rule_package_id'],'brief':db.loads(p['brief'],{})},'model':model,'stats':project_stats,'readiness':{'core_ready':bool(head['core_version_id']),'contracts_ready':sum(bool(item['contract_id']) for item in sections),'snapshot':ready_snapshot,'blockers':blockers},'research_question':research_question,'core_version_id':head['core_version_id'],'core':core,'state':head,'materials':materials,'references':references,'rules':rules,'rule_package':{'id':package['id'],'version':package['version'],'rule_count':len(db.loads(package['rules'],[]))} if package else None,'facts':facts,'artifacts':artifacts,'sections':sections}

@router.patch('/writing-state')
def patch_state(pid,body:dict):
    values={}
    if 'target_snapshot_id' in body:owned('snapshot',body['target_snapshot_id'],pid);values['target_snapshot_id']=body['target_snapshot_id']
    if 'policy' in body:
        if set(body['policy'])-{'agent_writing_enabled','strict_export_enabled'}:fail('INVALID_POLICY','项目策略无效')
        values['policy_json']=db.dumps(body['policy'])
    if not values:fail('INVALID_STATE','没有可更新状态')
    result=update_state(pid,body['expected_revision'],**values)
    from .impact import changed
    changed(pid,'project',pid,'研究基线或项目策略已变化');return result

@router.get('/repositories')
def repositories(pid):security.require_project(pid);return db.rows('SELECT * FROM repository_bindings WHERE project_id=? ORDER BY created_at DESC',(pid,))
@router.post('/repositories')
def bind_repository(pid,body:dict):return repository.bind(pid,body)
@router.patch('/repositories/{rid}')
def repository_policy(pid,rid,body:dict):return repository.set_egress(pid,rid,body['egress'])
@router.post('/repositories/{rid}/snapshots')
def snapshot_task(pid,rid,body:dict):owned('repository',rid,pid);return tasks.enqueue(pid,'agent_capture',{'binding_id':rid,'mode':body.get('mode','working_tree'),'ref':body.get('ref')})
@router.get('/snapshots')
def snapshots(pid):security.require_project(pid);return db.rows('SELECT * FROM repository_snapshots WHERE project_id=? ORDER BY created_at DESC',(pid,))
@router.get('/snapshots/{sid}')
def snapshot(pid,sid):return repository.get_snapshot(pid,sid)
@router.get('/snapshots/{sid}/content')
def source_content(pid,sid,path:str,line_start:int=1,line_end:int=150):return repository.read(pid,sid,path,line_start,line_end)
@router.get('/snapshots/{sid}/search')
def source_search(pid,sid,q:str):return repository.search(pid,sid,q)
@router.get('/snapshots/{sid}/symbols')
def source_symbols(pid,sid):owned('snapshot',sid,pid);return db.rows('SELECT * FROM code_units WHERE snapshot_id=? LIMIT 2000',(sid,))
@router.get('/evidence')
def evidence_list(pid):security.require_project(pid);return [evidence.get(pid,r['id']) for r in db.rows('SELECT id FROM evidence_items WHERE project_id=? ORDER BY created_at DESC LIMIT 300',(pid,))]
@router.post('/evidence')
def new_evidence(pid,body:dict):return evidence.create(pid,body)
@router.get('/evidence/{eid}')
def evidence_item(pid,eid):return evidence.get(pid,eid)
@router.patch('/evidence/{eid}')
def evidence_state(pid,eid,body:dict):return evidence.set_validity(pid,eid,body['validity'])
@router.get('/claims')
def claim_list(pid):security.require_project(pid);return [claims.get(pid,r['id']) for r in db.rows('SELECT id FROM claim_versions WHERE project_id=? ORDER BY created_at DESC LIMIT 300',(pid,))]
@router.post('/claims')
def new_claim(pid,body:dict):return claims.create(pid,body)
@router.get('/claims/{cid}')
def claim_item(pid,cid):return claims.get(pid,cid)
@router.get('/thesis-core')
def cores(pid):security.require_project(pid);return {'state':state(pid),'versions':db.rows('SELECT * FROM thesis_core_versions WHERE project_id=? ORDER BY created_at DESC',(pid,))}
@router.post('/thesis-core')
def save_core(pid,body:dict):return contracts.save_core(pid,body.get('content',{}),body.get('parent_id'))
@router.post('/writing-bootstrap')
def writing_bootstrap(pid):return contracts.bootstrap_project(pid)
@router.post('/thesis-core/{cid}/publish')
def publish_core(pid,cid,body:dict):return contracts.publish_core(pid,cid,body['expected_revision'])
@router.get('/section-contracts/{sid}')
def chapter_contracts(pid,sid):
    from .common import section
    section(sid,pid);return {'state':state(pid),'versions':db.rows('SELECT * FROM section_contract_versions WHERE project_id=? AND section_id=? ORDER BY created_at DESC',(pid,sid))}
@router.post('/section-contracts/{sid}')
def save_chapter(pid,sid,body:dict):return contracts.save_contract(pid,sid,body.get('content',{}),body['expected_revision'],body.get('publish',True))
@router.get('/writing-profiles')
def profile_list(pid):security.require_project(pid);return [profiles.get(pid,r['id']) for r in db.rows('SELECT id FROM writing_profiles WHERE project_id=? ORDER BY created_at DESC',(pid,))]
@router.post('/writing-profiles')
def new_profile(pid,body:dict):return profiles.create(pid,body)
@router.post('/writing-start')
def start_writing(pid,body:dict):return coordinator.start(pid,body)
@router.get('/writing-preflight')
def writing_preflight(pid,section_id:str,profile_id:str|None=None,budget_limit:float|None=None):
    from .launch import preflight
    return preflight(pid,section_id,profile_id,budget_limit)
@router.get('/writing-sessions')
def sessions(pid,include_materials:bool=False):
    security.require_project(pid)
    return db.rows("SELECT s.*,(SELECT COUNT(*) FROM writing_candidates c WHERE c.session_id=s.id) candidate_count FROM writing_sessions s WHERE project_id=?"+('' if include_materials else " AND operation!='collect_materials'")+" ORDER BY created_at DESC LIMIT 100",(pid,))
@router.post('/writing-sessions')
def new_session(pid,body:dict):return coordinator.create(pid,body)
@router.get('/writing-sessions/{sid}')
def session(pid,sid):return coordinator.get(pid,sid)
@router.post('/writing-sessions/{sid}/resume')
def resume(pid,sid,body:dict):return coordinator.resume(pid,sid,body.get('acknowledge_unknown',False))
@router.post('/writing-sessions/{sid}/profiles')
def reconfigure_session(pid,sid,body:dict):return coordinator.reconfigure(pid,sid,body)
@router.post('/writing-sessions/{sid}/reparse-draft')
def reparse_draft(pid,sid):return coordinator.reparse_saved_draft(pid,sid)
@router.post('/writing-sessions/{sid}/cancel')
def cancel(pid,sid):return coordinator.cancel(pid,sid)
@router.post('/writing-sessions/{sid}/approve-judgment')
def approve_judgment(pid,sid,body:dict):return coordinator.approve_judgment(pid,sid,body.get('note',''))
@router.post('/writing-sessions/{sid}/revise')
def revise(pid,sid,body:dict):return revision.revise(pid,sid,body)
@router.get('/writing-candidates/{cid}')
def get_candidate(pid,cid):
    c=candidate(cid,pid);analyses=db.rows('SELECT * FROM document_analyses WHERE candidate_id=? ORDER BY rowid DESC',(cid,));a=sentences.get(pid,analyses[0]['id']) if analyses else None
    return {**c,'document':unblob(c['document_blob']),'decision':unblob(c['decision_blob']),'analysis':a,'reviews':verification.runs(pid,a['id']) if a else []}
@router.post('/writing-candidates/{cid}/accept')
def accept(pid,cid,body:dict,request:Request):return adoption.adopt(pid,cid,body,request.headers.get('idempotency-key',''))
@router.post('/writing-candidates/{cid}/prepare-partial')
def partial(pid,cid,body:dict):return adoption.prepare_partial(pid,cid,body)
@router.post('/writing-adoptions')
def multi_adopt(pid,body:dict,request:Request):return adoption.adopt_many(pid,body.get('items'),request.headers.get('idempotency-key',''),body.get('joint_review_id'))
@router.post('/writing-joint-reviews')
def create_joint(pid,body:dict):
    from .joint import create
    return create(pid,body)
@router.get('/writing-joint-candidates')
def joint_candidates(pid):
    security.require_project(pid)
    return db.rows("SELECT c.id candidate_id,s.section_id,x.title,x.current_revision_id base_revision_id,a.state analysis_state FROM writing_candidates c JOIN writing_sessions s ON s.id=c.session_id JOIN sections x ON x.id=s.section_id JOIN document_analyses a ON a.candidate_id=c.id WHERE s.project_id=? AND s.status IN ('needs_review','completed') AND a.rowid=(SELECT MAX(a2.rowid) FROM document_analyses a2 WHERE a2.candidate_id=c.id) ORDER BY c.created_at DESC LIMIT 100",(pid,))
@router.get('/writing-joint-reviews/{jid}')
def get_joint(pid,jid):
    from .joint import get
    return get(pid,jid)
@router.post('/writing-joint-reviews/{jid}/review')
def review_joint(pid,jid,body:dict):
    from .joint import human_review
    return human_review(pid,jid,body)
@router.post('/writing-candidates/{cid}/verify')
def verify_candidate(pid,cid):
    return coordinator.recheck_candidate(pid,cid)
@router.get('/revisions/{rid}/analysis')
def revision_analysis(pid,rid):
    a=db.one('SELECT a.id FROM document_analyses a WHERE a.project_id=? AND a.revision_id=? ORDER BY rowid DESC LIMIT 1',(pid,rid))
    if not a:fail('NOT_FOUND','正文尚未完成句子分析',404)
    return {'analysis':sentences.get(pid,a['id']),'reviews':verification.runs(pid,a['id'])}
@router.post('/writing-analyses/{aid}/review')
def review_analysis(pid,aid,body:dict):return verification.human_review(pid,aid,body)
@router.get('/writing-coverage')
def coverage(pid):return paper_review.coverage(pid)
@router.post('/writing-reviews')
def paper_review_task(pid,body:dict):profiles.get(pid,body['profile_id']);return tasks.enqueue(pid,'agent_paper_review',body)
@router.post('/writing-reviews/human')
def manual_paper_review(pid,body:dict):return paper_review.human_paper_review(pid,body)
@router.get('/writing-export-report')
def writing_report(pid,strict:bool=False):return paper_review.export_report(pid,strict)
@router.get('/writing-stale-reasons')
def stale_reasons(pid,section_id:str|None=None):
    security.require_project(pid)
    from .impact import reasons
    return reasons(pid,section_id)
@router.post('/sections/{sid}/resolve-stale')
def resolve_stale(pid,sid,body:dict):
    from .impact import resolve_manual
    return resolve_manual(pid,sid,body)
@router.get('/diagnostic-dataset')
def diagnostic_dataset(pid):security.require_project(pid);return evaluations.dataset()
@router.get('/diagnostics')
def diagnostics(pid):security.require_project(pid);return db.rows('SELECT * FROM diagnostic_runs WHERE project_id=? ORDER BY created_at DESC',(pid,))
@router.post('/diagnostics')
def new_diagnostic(pid,body:dict):return evaluations.create(pid,body)
@router.get('/diagnostics/{rid}')
def diagnostic(pid,rid):return evaluations.get(pid,rid)
@router.post('/diagnostics/{rid}/review')
def diagnostic_review(pid,rid,body:dict):return evaluations.review(pid,rid,body)
@router.post('/diagnostics/{rid}/qualify')
def diagnostic_qualify(pid,rid,body:dict):return evaluations.qualify(pid,rid,body.get('note',''),body.get('policy_id'))
@router.get('/diagnostic-rubric')
def rubric(pid):
    security.require_project(pid)
    from .diagnostic_scoring import DIMENSIONS
    return {'dimensions':DIMENSIONS,'scale':{'0':'错误或未满足','1':'部分满足','2':'给定范围内满足','null':'有说明的不适用'}}
@router.get('/diagnostics/{rid}/blind-packet')
def blind_packet(pid,rid,evaluator_id:str):
    from .diagnostic_scoring import blind_packet
    return blind_packet(pid,rid,evaluator_id)
@router.post('/diagnostics/{rid}/blind-review')
def blind_review(pid,rid,body:dict):
    from .diagnostic_scoring import review
    if not body.get('packet_id'):fail('INVALID_REVIEW','需有效盲评包')
    result=review(pid,rid,body)
    return {'review_id':result['review_id'],'recorded':True,'method':'human_reviewed'}
@router.post('/diagnostics/{rid}/adjudicate')
def adjudicate(pid,rid,body:dict):
    from .diagnostic_scoring import adjudicate
    return adjudicate(pid,rid,body)
@router.post('/diagnostics/{rid}/quality-policy')
def freeze_policy(pid,rid,body:dict):
    from .diagnostic_scoring import freeze_policy
    return freeze_policy(pid,rid,body)
@router.get('/writing-quality-policies')
def quality_policies(pid):security.require_project(pid);return db.rows('SELECT * FROM writing_quality_policies WHERE project_id=? ORDER BY created_at DESC',(pid,))
@router.post('/diagnostics/{rid}/assisted-review')
def assisted_review(pid,rid,body:dict):
    owned('diagnostic',rid,pid);profiles.get(pid,body['profile_id']);ids=body.get('item_ids',[])
    if not isinstance(ids,list) or not 1<=len(ids)<=102:fail('INVALID_REVIEW','请选择1至102个完成样本')
    for identity in ids:
        if not db.one("SELECT id FROM diagnostic_items WHERE id=? AND run_id=? AND status='completed'",(identity,rid)):fail('NOT_FOUND','样本不属于当前运行或未完成',404)
    return {'tasks':[tasks.enqueue(pid,'writing_diagnostic_review',{'run_id':rid,'item_id':identity,'profile_id':body['profile_id']}) for identity in ids],'method':'model_assisted','human_qualification_granted':False}
@router.get('/diagnostic-comparisons')
def comparisons(pid):security.require_project(pid);return db.rows('SELECT * FROM diagnostic_comparisons WHERE project_id=? ORDER BY created_at DESC',(pid,))
@router.post('/diagnostic-comparisons')
def create_comparison(pid,body:dict):
    from .diagnostic_comparisons import create
    return create(pid,body)
@router.get('/diagnostic-comparisons/{cid}')
def comparison_report(pid,cid):
    from .diagnostic_comparisons import report
    return report(pid,cid)
@router.post('/diagnostic-comparisons/{cid}/position-swap')
def position_swap(pid,cid,body:dict):
    from .diagnostic_comparisons import owned_comparison
    owned_comparison(pid,cid);profiles.get(pid,body['profile_id'])
    if not isinstance(body.get('item_ids'),list) or len(body['item_ids'])!=2:fail('INVALID_COMPARISON','请选择两个可比回答')
    return tasks.enqueue(pid,'writing_position_swap',{'comparison_id':cid,'item_ids':body['item_ids'],'profile_id':body['profile_id']})
@router.post('/diagnostics/{rid}/resume')
def resume_diagnostic(pid,rid,body:dict):return evaluations.resume(pid,rid,body)
@router.get('/writing-checks')
def checks(pid):security.require_project(pid);return list(db.setting('writing_checks:'+pid,{}).values())
@router.post('/writing-billing/reconcile')
def reconcile_writing_billing(pid,body:dict):
    from .billing import sample,recover_adjacent,recover_closed_group,recover_overlapping_intervals
    p,cfg,key=profiles.for_session(pid,body['profile_id'])
    from .. import provider
    with provider.request_scope(cfg,key):current=sample(cfg)
    return {'single_intervals':recover_adjacent(pid),'overlapping_intervals':recover_overlapping_intervals(pid),'closed_intervals':recover_closed_group(pid,current),'unknown_costs_remain_reserved':True}
@router.post('/writing-checks/{cid}/run')
def run_registered_check(pid,cid,body:dict):
    owned('snapshot',body['snapshot_id'],pid)
    return tasks.enqueue(pid,'agent_registered_check',{'check_id':cid,'snapshot_id':body['snapshot_id']})
@router.post('/writing-checks')
def new_check(pid,body:dict):return check_registry.register(pid,body)
@router.patch('/writing-checks/{cid}')
def enable_check(pid,cid,body:dict):
    security.require_project(pid);checks=db.setting('writing_checks:'+pid,{})
    if cid not in checks or type(body.get('enabled')) is not bool:fail('INVALID_CHECK','检查登记或开关无效')
    checks[cid]['enabled']=body['enabled'];db.set_setting('writing_checks:'+pid,checks);return checks[cid]
