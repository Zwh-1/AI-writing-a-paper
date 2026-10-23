"""Budgeted real V4.1 experiments, preserving all failures and zero invented human reviews."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'.runtime/writing-v4-live-data';os.environ['PAPER_DATA_DIR']=str(DATA)
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
from app import db,security,provider,tasks,documents,rules,exports
from app.writing_agent import profiles,repository,evidence,contracts,coordinator,revision,adoption,evaluations,paper_review,diagnostic_comparisons as comparisons
from app.writing_agent.common import state,update_state,blob,unblob,digest

REPORT=ROOT/'docs/writing-agent/completion-real-acceptance.json'

def emit(value):print(json.dumps(value,ensure_ascii=False),flush=True)

def prepare():
    db.init();saved=db.setting('writing_completion_live_v41',{})
    if saved:
        for field,role in [('diagnostic_profile_id','diagnostic'),('draft_profile_id','draft'),('verify_profile_id','verify')]:
            old=profiles.get(saved['project_id'],saved[field])
            if not old['configuration_current']:
                fresh=profiles.create(saved['project_id'],{'role':role,'configuration':old['configuration']});saved.setdefault('previous_profile_ids',[]).append(saved[field]);saved[field]=fresh['id']
        db.set_setting('writing_completion_live_v41',saved);return saved
    prior=db.setting('writing_v4_live_acceptance',{})
    if not prior:raise RuntimeError('缺少既有已授权的隔离验收配置，未创建新的付费路线')
    prior_used=db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE status NOT IN ('rejected','failed_before_send')")['n']
    cap=min(6.5,float(prior['budget_limit']));available=cap-prior_used
    if available<=0:raise RuntimeError('既有验收总预算已耗尽')
    if db.one("SELECT id FROM tasks WHERE status IN ('queued','running') LIMIT 1"):raise RuntimeError('旧隔离任务仍在执行，请先核对其状态，未并行重复提交')
    pid=db.uid();stamp=db.now();brief={'research_object':'Edu SBT Platform','scope':'选定源码的转移约束、操作暂停关系与一份已有本地单用例运行记录','limitations':['不是公网部署验证','不声称全面安全性或性能优势']}
    db.execute('INSERT INTO projects(id,title,budget,brief,created_at,updated_at) VALUES(?,?,?,?,?,?)',(pid,'Edu SBT证据约束写作试点',available,db.dumps(brief),stamp,stamp))
    cfg={'route':'primary','max_output_tokens':4096,'thinking_enabled':False,'tool_mode':'json','max_turns':2,'max_tool_calls':8,'reconcile_billing':True}
    diagnostic=profiles.create(pid,{'role':'diagnostic','configuration':cfg});draft=profiles.create(pid,{'role':'draft','configuration':{**cfg,'max_output_tokens':8192}});verify=profiles.create(pid,{'role':'verify','configuration':cfg})
    binding=repository.bind(pid,{'root':r'D:\桌面\edu-sbt-platform','egress':'selected_content','include':['AGENTS.md','frontend/AGENTS.md','contracts/contracts/core/credential/*.sol','contracts/test/evolution-sbt-behavior.test.ts','backend/src/domain/ai/agent/runner.ts','frontend/src/ai/agent/agent-execution.ts','frontend/src/features/ai-assistant/hooks/useAgentExecutor.ts']})
    snap=repository.capture(pid,binding['id'])
    if snap['state']!='ready' or snap['manifest']['gaps']:raise RuntimeError('试点选定来源有变化或读取缺口，未继续付费写作')
    core=contracts.save_core(pid,{'research_question':'Edu SBT的凭证转移约束和操作暂停边界如何实现，不同证据层次能支持哪些有限结论？','scope':brief['scope'],'method':'源码与继承声明分析、跨文件静态关系核对、独立本地运行记录的有限解释','limitations':brief['limitations']});contracts.publish_core(pid,core['id'],state(pid)['revision'])
    db.execute('INSERT INTO rules(id,project_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),pid,'保留角色、合取条件、否定和版本范围；区分代码、测试定义、实际执行与部署；不将任务说明、评审元指令写成项目成果；不编造性能或全面防伪效果。','mandatory','confirmed',stamp));rules.publish(pid)
    def code(path,a,b,kind='code_observation'):return evidence.create(pid,{'kind':kind,'origin_type':'code','origin_id':snap['id'],'locator':{'path':path,'line_start':a,'line_end':b}})['id']
    static=[code('contracts/contracts/core/credential/SBTBase.sol',1,89),code('contracts/contracts/core/credential/EvolutionSBT.sol',1,40),code('contracts/test/evolution-sbt-behavior.test.ts',160,190,'test_definition')]
    cross=[code('backend/src/domain/ai/agent/runner.ts',19,45),code('frontend/src/ai/agent/agent-execution.ts',687,716),code('frontend/src/features/ai-assistant/hooks/useAgentExecutor.ts',42,76)]
    old=db.one("SELECT * FROM artifacts WHERE project_id=? AND kind='test_run' ORDER BY created_at DESC LIMIT 1",(prior['project_id'],))
    if not old:raise RuntimeError('缺少实际本地运行记录，不能用测试定义代替')
    meta=db.loads(old['metadata'],{});source_manifest=meta.get('compiled_sources_sha256',{})
    matches=[]
    for selected,recorded in [('contracts/contracts/core/credential/SBTBase.sol','contracts/core/credential/SBTBase.sol'),('contracts/contracts/core/credential/EvolutionSBT.sol','contracts/core/credential/EvolutionSBT.sol'),('contracts/test/evolution-sbt-behavior.test.ts','test/evolution-sbt-behavior.test.ts')]:
        frozen=db.one('SELECT blob_sha256 FROM repository_snapshot_files WHERE snapshot_id=? AND relative_path=?',(snap['id'],selected))
        if not frozen or source_manifest.get(recorded)!=frozen['blob_sha256']:raise RuntimeError('引用的源码或测试与实际本地运行记录不一致')
        matches.append({'path':selected,'sha256':frozen['blob_sha256']})
    meta={**meta,'snapshot_id':snap['id'],'egress':'selected_content','source_record_artifact_id':old['id'],'source_files_rechecked':matches,'record_reused_not_rerun':True,'scope':'已有同源代码的单个本地模拟链记录；未声称重新执行、全套测试或公网部署'}
    aid=db.uid();db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'同源代码的已有本地单用例记录','test_run',blob(meta),db.dumps(meta),db.now()))
    runtime=evidence.create(pid,{'kind':'test_run','origin_type':'artifact','origin_id':aid,'conditions':['已有运行记录，不冒充本次重新运行','Hardhat本地模拟链，离线fixture，单个用例','只核对所引用合约和测试的内容哈希，不推出全面安全性']})['id']
    cases=[('凭证转移约束及其证据范围','implementation','用2至3个连续自然段、约400至600个中文字符，解释SBTBase._update对from和to均非零的合取条件、super调用与EvolutionSBT继承声明。测试只作为定义，不说已运行；指出不能推出全面防伪。',static),('操作暂停的跨模块关系','implementation','用2至3个连续自然段、约400至600个中文字符，解释后端review_pause、前端执行器review状态和Hook提示的静态关系；明确不能推出覆盖全部危险操作或已验证真实用户流程。',cross),('本地单用例运行记录与局限','results','用2至3个连续自然段、约400至600个中文字符，根据已有记录解释一个本地模拟链用例通过及其与转移条件的关系；说明记录未在此阶段重新运行、离线fixture、单用例范围，不能推广为公网部署或全部安全性。',[*static,runtime])]
    sections=[]
    for position,(title,kind,goal,ids) in enumerate(cases):
        sid=db.uid();db.execute('INSERT INTO sections(id,project_id,title,kind,position,target_words,goal) VALUES(?,?,?,?,?,?,?)',(sid,pid,title,kind,position,600,goal));contract=contracts.save_contract(pid,sid,{'goal':goal,'evidence_ids':ids,'depends_on_sections':[]},state(pid)['revision']);sections.append({'section_id':sid,'contract_id':contract['id']})
    saved={'project_id':pid,'diagnostic_profile_id':diagnostic['id'],'draft_profile_id':draft['id'],'verify_profile_id':verify['id'],'snapshot_id':snap['id'],'sections':sections,'budget_ceiling_all_isolated_experiments':cap,'prior_experiment_charge':prior_used,'new_project_budget':available,'source_runtime_file_matches':matches,'prepared_at':db.now()}
    db.set_setting('writing_completion_live_v41',saved);return saved

def current_acceptance_task(saved,task):
    """Budget recovery must never revive superseded comparison studies."""
    body=db.loads(task.get('payload'),{});pid=saved['project_id']
    if task.get('project_id')!=pid:return False
    if task['kind'] in ('writing_diagnostic','writing_diagnostic_review'):
        member=db.one('SELECT m.comparison_id FROM diagnostic_items i JOIN diagnostic_comparison_members m ON m.run_id=i.run_id WHERE i.id=?',(body.get('item_id'),))
        return bool(member and member['comparison_id'] in saved.get('comparison_ids',[]))
    if task['kind']=='writing_position_swap':return body.get('comparison_id') in saved.get('comparison_ids',[])
    if task['kind']=='agent_paper_review':return task['id']==saved.get('paper_review_task_id')
    if task['kind']=='agent_step':
        step=db.one('SELECT session_id FROM writing_steps WHERE task_id=?',(task['id'],))
        sessions={s.get('session_id') for s in saved.get('sections',[])}|set(saved.get('revision_session_ids',[]))|{saved.get('adversarial_session_id')}
        return bool(step and step['session_id'] in sessions)
    return False


def drain(saved,phase,concurrency=1):
    n=0
    from concurrent.futures import ThreadPoolExecutor
    from app.writing_agent.billing import sample,recover_adjacent,recover_closed_group,recover_overlapping_intervals
    recovery_rounds=0
    while True:
        _,cfg,_=profiles.for_session(saved['project_id'],saved['diagnostic_profile_id'])
        used=db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(saved['project_id'],))['n'];available=saved['new_project_budget']-used
        ceiling=max(.01,float(cfg.get('max_request_cost',2)));width=min(concurrency,max(1,int(max(0,available)/ceiling)))
        with ThreadPoolExecutor(max_workers=width) as pool:
            completed=list(pool.map(lambda _:tasks.run_one(),range(width)))
        count=sum(bool(x) for x in completed)
        if not count:
            retry=[]
            if recovery_rounds<2:
                for task in db.rows("SELECT * FROM tasks WHERE project_id=? AND status IN ('failed','budget_blocked') AND attempt<3",(saved['project_id'],)):
                    if not current_acceptance_task(saved,task):continue
                    last=db.one('SELECT status FROM generation_runs WHERE task_id=? ORDER BY rowid DESC LIMIT 1',(task['id'],))
                    unknown=db.one("SELECT id FROM generation_runs WHERE task_id=? AND status IN ('pending','sent','outcome_unknown')",(task['id'],))
                    budget_wait=task['status']=='budget_blocked' or ('预算' in str(task.get('error','')) and (not last or last['status'] in ('completed','completed_invalid','failed_before_send','rejected')))
                    if not unknown and ((last and last['status']=='failed_before_send') or (budget_wait and available>=ceiling)):retry.append(task)
            if not retry:break
            recovery_rounds+=1
            for task in retry:
                if task['kind']=='agent_step':
                    step=db.one('SELECT session_id FROM writing_steps WHERE task_id=?',(task['id'],))
                    if step and coordinator.get(saved['project_id'],step['session_id'])['status']=='failed':coordinator.resume(saved['project_id'],step['session_id'])
                else:
                    db.execute("UPDATE tasks SET status='queued',error=NULL,cancel_requested=0 WHERE id=?",(task['id'],))
                    if task['kind']=='writing_diagnostic':db.execute("UPDATE diagnostic_items SET status='queued' WHERE task_id=?",(task['id'],))
            emit({'phase':phase,'known_not_sent_or_temporary_budget_recovery':len(retry),'recovery_round':recovery_rounds,'unknown_requests_never_retried':True});continue
        n+=count
        if cfg.get('reconcile_billing'):
            # Overlapping intervals have no invented per-call allocation. A
            # verified closed group releases reservations as one exact total.
            _,cfg,key=profiles.for_session(saved['project_id'],saved['diagnostic_profile_id'])
            with provider.request_scope(cfg,key):current=sample(cfg)
            recover_adjacent(saved['project_id']);recover_overlapping_intervals(saved['project_id']);recover_closed_group(saved['project_id'],current)
        if n%4==0:
            total=db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE status NOT IN ('rejected','failed_before_send')")['n'];emit({'phase':phase,'processed':n,'all_experiment_charged_or_reserved':round(total,6)})
    return n

def save(saved):
    pid=saved['project_id'];report={**saved,'human_reviews_added_by_agent':0,'human_quality_acceptance':'pending','comparisons':[comparisons.report(pid,x) for x in saved.get('comparison_ids',[])],'pilots':[coordinator.get(pid,x['session_id']) for x in saved['sections'] if x.get('session_id')],'revision_sessions':[coordinator.get(pid,x) for x in saved.get('revision_session_ids',[])],'adversarial_session_id':saved.get('adversarial_session_id'),'budget':db.one("SELECT COUNT(*) requests,COALESCE(SUM(budget_charge),0) charged_or_reserved,SUM(cost IS NULL) individual_cost_unknown FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(pid,)),'all_isolated_budget':db.one("SELECT COUNT(*) requests,COALESCE(SUM(budget_charge),0) charged_or_reserved FROM generation_budget_usage WHERE status NOT IN ('rejected','failed_before_send')"),'saved_at':db.now()}
    if saved.get('exports'):report['exports']=saved['exports']
    report['draft_export_report']=paper_review.export_report(pid,False)
    try:paper_review.export_report(pid,True);report['strict_export']={'allowed':True}
    except Exception as error:report['strict_export']={'allowed':False,'reason':security.redact(error)}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');db.set_setting('writing_completion_live_v41',saved)
    return report

def run_pilots(saved):
    pid=saved['project_id']
    stage_profiles={stage:saved['verify_profile_id'] if stage in ('investigate','annotation','semantic','coherence','regression','judgment_check') else saved['draft_profile_id'] for stage in coordinator.STAGES}
    resumed=False
    for item in saved['sections']:
        if not item.get('session_id'):
            s=coordinator.create(pid,{'section_id':item['section_id'],'expected_project_state_revision':state(pid)['revision'],'contract_version_id':item['contract_id'],'profile_id':saved['draft_profile_id'],'policy':{'budget_limit':saved['new_project_budget'],'stage_profiles':stage_profiles}});item['session_id']=s['id'];db.set_setting('writing_completion_live_v41',saved)
        else:
            prior=coordinator.get(pid,item['session_id'])
            if prior['status'] in ('failed','stale'):
                current=db.one('SELECT current_revision_id FROM sections WHERE id=?',(item['section_id'],))['current_revision_id']
                if prior['status']=='stale' or current!=prior['base_versions'].get('base_revision_id'):
                    fresh=coordinator.create(pid,{'section_id':item['section_id'],'base_revision_id':current,'expected_project_state_revision':state(pid)['revision'],'contract_version_id':item['contract_id'],'profile_id':saved['draft_profile_id'],'policy':{'budget_limit':saved['new_project_budget'],'stage_profiles':stage_profiles}});item.setdefault('previous_session_ids',[]).append(prior['id']);item['session_id']=fresh['id'];resumed=True;continue
                stage_profiles={stage:saved['verify_profile_id'] if stage in ('investigate','annotation','semantic','coherence','regression','judgment_check') else saved['draft_profile_id'] for stage in coordinator.STAGES}
                coordinator.reconfigure(pid,prior['id'],{'stage_profiles':stage_profiles,'note':'保留原始响应与费用；字段分类、短标识、精确去重与按阶段输出预算修补后只恢复未完成阶段。'})
                failed=[s for s in prior['steps'] if s['state']=='failed']
                if failed and failed[-1]['stage']=='draft':
                    try:coordinator.reparse_saved_draft(pid,prior['id'])
                    except Exception as error:
                        from fastapi import HTTPException
                        if isinstance(error,HTTPException) and error.detail.get('code')=='REPARSE_NOT_ALLOWED':coordinator.resume(pid,prior['id'])
                        else:raise
                elif prior['candidates']:coordinator.recheck_candidate(pid,prior['candidates'][-1]['id'])
                else:coordinator.resume(pid,prior['id'])
                resumed=True
    if resumed:
        if saved.get('exports'):saved.setdefault('previous_exports',[]).extend(saved.pop('exports'))
        if saved.get('paper_review_task_id'):saved.setdefault('previous_paper_review_task_ids',[]).append(saved.pop('paper_review_task_id'))
        db.set_setting('writing_completion_live_v41',saved)
    drain(saved,'actual_project_pilots');save(saved)
    initial=coordinator.get(pid,saved['sections'][0]['session_id'])
    if initial['status']=='needs_review' and initial['candidates'] and not saved.get('revision_session_ids'):
        s=revision.revise(pid,initial['id'],{'instructions':'将机制、继承声明、测试定义与限制组织成3个自然段，缩减任务元叙述和重复，但保留原来正确的from、to合取条件和未运行的边界。','profile_id':saved['draft_profile_id'],'stage_profiles':stage_profiles});saved['revision_session_ids']=[s['id']];db.set_setting('writing_completion_live_v41',saved)
    elif saved.get('revision_session_ids'):
        prior=coordinator.get(pid,saved['revision_session_ids'][-1]);current=db.one('SELECT current_revision_id FROM sections WHERE id=?',(prior['section_id'],))['current_revision_id']
        if prior['status'] in ('failed','stale') and prior['candidates']:
            if current!=prior['base_versions'].get('base_revision_id'):
                fresh=revision.revise(pid,prior['id'],{'instructions':'保留from和to均非零的合取条件、super调用边界与测试未在本小节运行的限定，仅改正措辞和指代；不可扩大为全面防伪。','profile_id':saved['draft_profile_id'],'stage_profiles':stage_profiles});saved['revision_session_ids'].append(fresh['id'])
            else:
                stage_profiles={stage:saved['verify_profile_id'] if stage in ('investigate','annotation','semantic','coherence','regression','judgment_check') else saved['draft_profile_id'] for stage in coordinator.STAGES}
                coordinator.reconfigure(pid,prior['id'],{'stage_profiles':stage_profiles,'note':'使用短标识与按阶段输出预算重做未完成核验，保留正文原始响应与全部失败记录。'});coordinator.recheck_candidate(pid,prior['candidates'][-1]['id'])
            db.set_setting('writing_completion_live_v41',saved)
        elif prior['status']=='failed' and current==prior['base_versions'].get('base_revision_id'):
            coordinator.reconfigure(pid,prior['id'],{'stage_profiles':stage_profiles,'note':'保留既有原始结果和费用；按阶段输出预算与调查范围修补后恢复确定失败的阶段。'});coordinator.resume(pid,prior['id']);db.set_setting('writing_completion_live_v41',saved)
    drain(saved,'actual_revision_and_regression');save(saved)
    # A deliberately injected false conclusion is evaluated as a separate control.
    if initial['candidates'] and not saved.get('adversarial_session_id'):
        s=coordinator.create(pid,{'section_id':initial['section_id'],'expected_project_state_revision':state(pid)['revision'],'contract_version_id':saved['sections'][0]['contract_id'],'profile_id':saved['draft_profile_id'],'policy':{'budget_limit':saved['new_project_budget']}})
        source=db.one('SELECT * FROM writing_candidates WHERE id=?',(initial['candidates'][-1]['id'],));doc=unblob(source['document_blob']);doc['content'].append(documents.text_document('因此，该系统已经能够阻止任何形式的凭证内容伪造。')['content'][0]);doc=documents.normalize(doc);cid=db.uid()
        db.execute('INSERT INTO writing_candidates VALUES(?,?,?,?,?,?,?,?)',(cid,s['id'],source['id'],blob(doc),digest(doc),source['decision_blob'],source['context_id'],db.now()))
        first=db.one('SELECT * FROM writing_steps WHERE session_id=?',(s['id'],));db.execute("UPDATE writing_steps SET state='completed',output_blob=? WHERE id=?",(blob({'summary':'评估侧程序注入控制句，未调用写作者生成；只用于检测无依据的新主张','evidence_ids':[]}),first['id']));db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(first['task_id'],))
        db.execute('INSERT INTO writing_steps VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),s['id'],'judgment',0,digest([cid,'control-frozen-judgment']),'completed',0,None,blob({'judgment':unblob(source['decision_blob'])['judgment']}),None))
        db.execute('INSERT INTO writing_steps VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),s['id'],'draft',0,digest([cid,'adversarial-control']),'completed',0,None,blob({'candidate_id':cid,'missing_materials':[]}),None));coordinator.enqueue(s['id'],'annotation');saved['adversarial_session_id']=s['id'];db.set_setting('writing_completion_live_v41',saved)
    drain(saved,'adversarial_condition_control');save(saved)
    # Draft adoption is explicitly retained; no human declaration is fabricated.
    for item in saved['sections']:
        session=coordinator.get(pid,item['session_id']);source=session['candidates'][-1]['id'] if session['candidates'] else None
        if item==saved['sections'][0] and saved.get('revision_session_ids'):
            revised=coordinator.get(pid,saved['revision_session_ids'][-1])
            if revised['status']=='needs_review' and revised['candidates']:source=revised['candidates'][-1]['id']
        prior_receipt=db.one('SELECT candidate_id FROM writing_adoptions WHERE id=?',(item.get('draft_adoption',{}).get('adoption_id'),))
        if prior_receipt and prior_receipt['candidate_id']==source:continue
        if source and (session['status']=='needs_review' or (item==saved['sections'][0] and saved.get('revision_session_ids') and coordinator.get(pid,saved['revision_session_ids'][-1])['status']=='needs_review')):
            candidate=db.one('SELECT decision_blob FROM writing_candidates WHERE id=?',(source,));base=unblob(candidate['decision_blob'])['manifest']['base_revision_id']
            if item.get('draft_adoption'):item.setdefault('previous_draft_adoptions',[]).append(item['draft_adoption'])
            item['draft_adoption']=adoption.adopt(pid,source,{'base_revision_id':base,'accept_policy':'draft'},'v41-draft-'+source)
            if saved.get('exports'):saved.setdefault('previous_exports',[]).extend(saved.pop('exports'))
            if saved.get('paper_review_task_id'):saved.setdefault('previous_paper_review_task_ids',[]).append(saved.pop('paper_review_task_id'))
            db.set_setting('writing_completion_live_v41',saved)
    if not saved.get('paper_review_task_id'):
        task=tasks.enqueue(pid,'agent_paper_review',{'profile_id':saved['verify_profile_id']});saved['paper_review_task_id']=task['id'];db.set_setting('writing_completion_live_v41',saved)
    drain(saved,'actual_multi_section_review')
    if not saved.get('exports'):
        exports_list=[]
        try:exports_list.append({'mode':'draft','result':exports.export_project(pid,{'mode':'draft','pdf':False})})
        except Exception as error:exports_list.append({'mode':'draft','error':security.redact(error)})
        try:exports.export_project(pid,{'mode':'strict','pdf':False});exports_list.append({'mode':'strict','allowed':True})
        except Exception as error:exports_list.append({'mode':'strict','allowed':False,'reason':security.redact(error)})
        saved['exports']=exports_list
    if not saved.get('version_change_probe'):
        from app.writing_agent import impact
        contract=db.one('SELECT content_json FROM section_contract_versions WHERE id=?',(saved['sections'][0]['contract_id'],));eid=db.loads(contract['content_json'],{})['evidence_ids'][0]
        before={s['id']:bool(s['stale']) for s in db.rows('SELECT id,stale FROM sections WHERE project_id=?',(pid,))};observed={}
        class ProbeRollback(Exception):pass
        try:
            with db.transaction():
                evidence.set_validity(pid,eid,'revoked')
                observed={s['id']:bool(s['stale']) for s in db.rows('SELECT id,stale FROM sections WHERE project_id=?',(pid,))}
                raise ProbeRollback()
        except ProbeRollback:pass
        after={s['id']:bool(s['stale']) for s in db.rows('SELECT id,stale FROM sections WHERE project_id=?',(pid,))}
        saved['version_change_probe']={'evidence_id':eid,'before':before,'observed_during_revocation':observed,'after_rollback':after,'rolled_back_to_preserve_review_material':before==after,'method':'local_transaction_probe_not_a_human_semantic_review'}
    save(saved)

def run_comparisons(saved):
    pid=saved['project_id']
    if not saved.get('comparison_ids'):
        cases=[c['id'] for c in evaluations.dataset()['cases']]
        saved['comparison_ids']=[]
        for factor,selected in [('workflow',cases),('revision_state',['D12']),('annotation',['D10'])]:
            r=comparisons.create(pid,{'factor':factor,'profile_id':saved['diagnostic_profile_id'],'case_ids':selected,'repetitions':3,'budget_limit':saved['new_project_budget']});saved['comparison_ids'].append(r['id']);db.set_setting('writing_completion_live_v41',saved)
    drain(saved,'controlled_model_comparisons',2);save(saved)
    if not saved.get('assisted_review_tasks_created'):
        for cid in saved['comparison_ids']:
            for row in db.rows("SELECT i.id,i.run_id FROM diagnostic_items i JOIN diagnostic_comparison_members m ON m.run_id=i.run_id WHERE m.comparison_id=? AND i.status='completed'",(cid,)):
                tasks.enqueue(pid,'writing_diagnostic_review',{'run_id':row['run_id'],'item_id':row['id'],'profile_id':saved['verify_profile_id']},'v41-score-'+digest([row['id'],saved['verify_profile_id']]))
        saved['assisted_review_tasks_created']=True;db.set_setting('writing_completion_live_v41',saved)
    drain(saved,'independent_assisted_dimension_scoring',2);save(saved)
    if not saved.get('position_swap_tasks_created'):
        for cid in saved['comparison_ids']:
            report=comparisons.report(pid,cid);pair=next((p for p in report['paired_samples'] if len(p['arms'])==2 and all(a['status']=='completed' for a in p['arms'].values())),None)
            if pair:tasks.enqueue(pid,'writing_position_swap',{'comparison_id':cid,'item_ids':[a['item_id'] for a in pair['arms'].values()],'profile_id':saved['verify_profile_id']})
        saved['position_swap_tasks_created']=True;db.set_setting('writing_completion_live_v41',saved)
    drain(saved,'counterbalanced_position_checks',1);save(saved)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=('pilots','comparisons','all'),default='all');args=parser.parse_args();saved=prepare();emit({'phase':'prepared','project_id':saved['project_id'],'remaining_project_budget':saved['new_project_budget'],'all_experiment_ceiling':saved['budget_ceiling_all_isolated_experiments']})
    if args.phase in ('pilots','all'):run_pilots(saved)
    if args.phase in ('comparisons','all'):run_comparisons(saved)
    report=save(saved);emit({'phase':'recorded','report':str(REPORT),'budget':report['budget'],'human_quality_acceptance':'pending_not_fabricated'})

if __name__=='__main__':main()
