"""Isolated, budgeted real-provider calibration. Never prints or exports keys."""
import os,sys,json,sqlite3,shutil,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ORIGINAL=ROOT/'data';DATA=ROOT/'.runtime'/'writing-v4-live-data'
DATA.mkdir(parents=True,exist_ok=True)
os.environ['PAPER_DATA_DIR']=str(DATA)
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
from app import db,provider,security,tasks,documents
from app.writing_agent import repository,evidence,contracts,profiles,evaluations,coordinator,adoption,sentences,paper_review
from app.writing_agent.common import state,unblob,blob

def emit(value):print(json.dumps(value,ensure_ascii=False),flush=True)

def main():
    source=sqlite3.connect((ORIGINAL/'workbench.sqlite3').as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    cfg=json.loads(source.execute("SELECT value FROM settings WHERE key='provider'").fetchone()[0]);available=source.execute("SELECT p.budget-COALESCE((SELECT SUM(budget_charge) FROM generation_budget_usage g WHERE g.project_id=p.id AND g.status NOT IN ('rejected','failed_before_send')),0) n FROM projects p WHERE archived=0 ORDER BY created_at LIMIT 1").fetchone()['n'];source.close()
    ceiling=min(6.5,max(0,float(available)))
    if ceiling<2:emit({'status':'blocked','reason':'原项目可用预算不足一次未知价格预留，未增加预算'});return
    for profile in ('credential','gpt-fallback'):
        origin=ORIGINAL/(profile+'.dpapi');dest=DATA/(profile+'.dpapi')
        if origin.is_file() and not dest.exists():shutil.copyfile(origin,dest)
    db.init()
    existing=db.setting('writing_v4_live_acceptance',{})
    if existing:
        pid=existing['project_id'];profile=profiles.get(pid,existing['profile_id']);diag=evaluations.get(pid,existing['diagnostic_id'])
        if not profile['configuration_current']:
            old=profile;profile=profiles.create(pid,{'role':'draft','configuration':old['configuration']});existing['previous_profile_ids']=existing.get('previous_profile_ids',[])+[old['id']];existing['profile_id']=profile['id'];db.set_setting('writing_v4_live_acceptance',existing)
    else:
        db.set_setting('provider',{k:v for k,v in cfg.items() if k not in ('api_key','key','token')})
        pid=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,budget,brief,created_at,updated_at) VALUES(?,?,?,?,?,?)',(pid,'V4真实模型验收（与正式论文隔离）',ceiling,db.dumps({'purpose':'软件诊断和受控项目试点，不代表正式论文定题'}),stamp,stamp))
        profile=profiles.create(pid,{'role':'diagnostic','configuration':{'route':'primary','max_output_tokens':4096,'thinking_enabled':False,'tool_mode':'json','max_turns':3,'max_tool_calls':8,'reconcile_billing':True}})
        diag=evaluations.create(pid,{'profile_id':profile['id'],'repetitions':3,'budget_limit':ceiling})
        existing={'project_id':pid,'profile_id':profile['id'],'diagnostic_id':diag['id'],'budget_limit':ceiling};db.set_setting('writing_v4_live_acceptance',existing)
    emit({'phase':'diagnostics','project_id':pid,'diagnostic_id':diag['id'],'samples':diag['summary']['total'],'budget_limit':ceiling,'scoring':'semantic_scores_and_human_qualification_not_fabricated'})
    n=0
    while tasks.run_one():
        n+=1
        if n%3==0:
            current=evaluations.get(pid,diag['id']);emit({'phase':'diagnostics','processed_tasks':n,'completed_samples':current['summary']['completed'],'sample_total':current['summary']['total'],'run_status':current['status']})
    diag=evaluations.get(pid,diag['id']);report={'date':'2026-10-23','source_mode':'actual_provider','isolated_data_directory':str(DATA),'project_id':pid,'profile':profiles.get(pid,profile['id']),'diagnostic_profile':profiles.get(pid,diag['profile_id']),'diagnostics':diag,'diagnostic_semantic_status':'pending_independent_review','public_keys_included':False}
    from app.writing_agent.billing import recover_adjacent
    emit({'phase':'billing_recheck',**recover_adjacent(pid)})
    if existing.get('pilot_section_id') and existing.get('pipeline_semantics_revision',1)<2:
        previous=coordinator.get(pid,existing['pilot_session_id']);sid=existing['pilot_section_id'];snap=existing['snapshot_id'];references=[]
        for filename,a,b in [('contracts/contracts/core/credential/SBTBase.sol',1,89),('contracts/contracts/core/credential/EvolutionSBT.sol',1,34),('contracts/test/evolution-sbt-behavior.test.ts',160,190)]:
            references.append(evidence.create(pid,{'kind':'test_definition' if '/test/' in filename else 'code_observation','origin_type':'code','origin_id':snap,'locator':{'path':filename,'line_start':a,'line_end':b},'conditions':['所请求范围已完整读取；文件若仍有后续行可按需继续读取','只描述静态实现与测试定义，不声称执行或部署结果']}))
        contract=contracts.save_contract(pid,sid,{'goal':'仅解释SBTBase._update对非零地址间转移的判定和适用边界；继承部分只描述EvolutionSBT的声明行；测试只说明测试意图。未知的执行、部署和性能保持局限，不作为必须补齐的任务。','evidence_ids':[e['id'] for e in references],'depends_on_sections':[]},state(pid)['revision'])
        next_session=coordinator.create(pid,{'section_id':sid,'operation':'generate_unit','base_revision_id':None,'expected_project_state_revision':state(pid)['revision'],'contract_version_id':contract['id'],'profile_id':profile['id'],'policy':{'budget_limit':ceiling}})
        existing['pilot_previous_session_ids']=existing.get('pilot_previous_session_ids',[])+[previous['id']];existing.update(pilot_session_id=next_session['id'],pipeline_semantics_revision=2);db.set_setting('writing_v4_live_acceptance',existing)
    # The first real project unit uses only frozen static observations.
    sid=existing.get('pilot_section_id')
    if not sid:
        repo=repository.bind(pid,{'root':r'D:\桌面\edu-sbt-platform','egress':'selected_content','include':['AGENTS.md','README.md','contracts/contracts/core/credential/*.sol','contracts/test/evolution-sbt-behavior.test.ts','backend/src/domain/ai/agent/*.ts','backend/src/domain/ai/agent/planners/*.ts','docs/handbook/architecture/MODULE-CHAIN-CIRCUIT-MAP.md']})
        snapshot=repository.capture(pid,repo['id'])
        if snapshot['state']!='ready':raise RuntimeError('项目来源在冻结期间变化，未继续生成')
        refs=[]
        for path,start,end in [('contracts/contracts/core/credential/SBTBase.sol',55,88),('contracts/contracts/core/credential/EvolutionSBT.sol',1,34),('contracts/test/evolution-sbt-behavior.test.ts',160,190)]:
            refs.append(evidence.create(pid,{'kind':'test_definition' if '/test/' in path else 'code_observation','origin_type':'code','origin_id':snapshot['id'],'locator':{'path':path,'line_start':start,'line_end':end},'conditions':['静态源码或测试定义，不代表实际执行结果']}))
        rule=db.uid();db.execute('INSERT INTO rules(id,project_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?)',(rule,pid,'严格区分源码实现、测试定义和真实执行；保留条件、否定和局限；不得推导完全防伪或性能优势。','mandatory','confirmed',db.now()))
        from app import rules
        rules.publish(pid)
        sid=db.uid();db.execute('INSERT INTO sections(id,project_id,title,kind,position,target_words,goal) VALUES(?,?,?,?,?,?,?)',(sid,pid,'凭证转移约束及其验证边界','implementation',0,800,'解释非零地址之间的转移限制、继承关系和测试定义的边界'))
        core=contracts.save_core(pid,{'research_question':'Edu SBT如何在凭证实现中约束不同持有者之间的转移？','scope':'当前冻结源码及已存在测试定义','method':'源码与继承关系分析','limitations':['本试点不运行合约测试，不声称真实部署或安全性全面验收']});contracts.publish_core(pid,core['id'],state(pid)['revision'])
        contract=contracts.save_contract(pid,sid,{'goal':'形成解释机制、实现依据、测试定义与局限的连续小节；不将定义写成执行结果','evidence_ids':[x['id'] for x in refs],'depends_on_sections':[]},state(pid)['revision'])
        session=coordinator.create(pid,{'section_id':sid,'operation':'generate_unit','base_revision_id':None,'expected_project_state_revision':state(pid)['revision'],'contract_version_id':contract['id'],'profile_id':profile['id'],'policy':{'budget_limit':ceiling}})
        existing.update(pilot_section_id=sid,pilot_session_id=session['id'],snapshot_id=snapshot['id'],pipeline_semantics_revision=2);db.set_setting('writing_v4_live_acceptance',existing)
    emit({'phase':'project_pilot','section_id':sid,'session_id':existing['pilot_session_id']})
    current_session=coordinator.get(pid,existing['pilot_session_id'])
    if current_session['status']=='failed' and any(x['state']=='failed' and ('CONTEXT_BUDGET_EXCEEDED' in str(x.get('error')) or 'validation errors for Annotation' in str(x.get('error'))) for x in current_session['steps']):
        coordinator.resume(pid,current_session['id']);emit({'phase':'resume_local_context_step','reason':'不重发已完成的付费步骤；只恢复此前未发出的阶段'})
    while tasks.run_one():
        s=coordinator.get(pid,existing['pilot_session_id']);emit({'phase':'project_pilot','status':s['status'],'completed_steps':sum(x['state']=='completed' for x in s['steps'])})
    pilot=coordinator.get(pid,existing['pilot_session_id']);report['pilot']=pilot
    report['billing']={'charged':db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(pid,))['n'],'requests':db.one('SELECT COUNT(*) n FROM generation_runs WHERE project_id=?',(pid,))['n'],'settled_by_exact_interval':db.one("SELECT COUNT(*) n FROM generation_runs WHERE project_id=? AND json_extract(manifest,'$.billing_interval_proof') IS NOT NULL",(pid,))['n'],'limit':ceiling}
    report['pilot_candidates']=[{'id':r['id'],'document':unblob(r['document_blob']),'analysis':[sentences.get(pid,a['id']) for a in db.rows('SELECT id FROM document_analyses WHERE candidate_id=?',(r['id'],))]} for r in db.rows('SELECT * FROM writing_candidates WHERE session_id=?',(pilot['id'],))]
    output=ROOT/'docs'/'writing-agent';output.mkdir(exist_ok=True);(output/'real-model-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    emit({'status':'recorded','diagnostic_samples_completed':diag['summary']['completed'],'pilot_status':pilot['status'],'billing':report['billing'],'human_review':'not_claimed','report':str(output/'real-model-acceptance.json')})

if __name__=='__main__':main()
