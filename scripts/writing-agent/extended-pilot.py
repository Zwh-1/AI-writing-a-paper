"""Actual cross-module source and actual isolated local test evidence pilots."""
import os,sys,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];DATA=ROOT/'.runtime/writing-v4-live-data';os.environ['PAPER_DATA_DIR']=str(DATA)
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
from app import db,tasks,security
from app.writing_agent import repository,evidence,contracts,profiles,coordinator,adoption,paper_review
from app.writing_agent.common import state,blob,unblob,digest

def emit(v):print(json.dumps(v,ensure_ascii=False),flush=True)
db.init();settings=db.setting('writing_v4_live_acceptance');pid=settings['project_id'];previous=db.setting('writing_v4_extended_pilot',{})
if not previous:
    probe=ROOT/'.runtime/edu-sbt-contract-probe';runtime=json.loads((probe/'actual-test-result.json').read_text(encoding='utf-8'))
    if runtime['exit_code']!=0:raise RuntimeError('本地实际运行未通过')
    binding=repository.bind(pid,{'root':r'D:\桌面\edu-sbt-platform','egress':'selected_content','include':['AGENTS.md','README.md','frontend/AGENTS.md','contracts/contracts/core/credential/*.sol','contracts/test/evolution-sbt-behavior.test.ts','backend/src/domain/ai/agent/runner.ts','frontend/src/ai/agent/agent-execution.ts','frontend/src/features/ai-assistant/hooks/useAgentExecutor.ts']})
    snapshot=repository.capture(pid,binding['id'])
    if snapshot['state']!='ready':raise RuntimeError('冻结来源不稳定')
    from app.writing_agent.common import update_state
    update_state(pid,state(pid)['revision'],target_snapshot_id=snapshot['id'])
    core=contracts.save_core(pid,{'research_question':'Edu SBT的凭证约束与Agent操作边界分别怎样实现，现有证据能支持哪些有限结论？','scope':'选定源码快照与隔离副本中的单个本地模拟链用例','method':'跨文件静态关系和实际运行记录分别核对','limitations':['不构成公网部署、真实用户流程或系统全面验收','不以静态存在推出运行成功']},state(pid)['core_version_id']);contracts.publish_core(pid,core['id'],state(pid)['revision'])
    original_profile=profiles.get(pid,settings['profile_id']);profile=profiles.create(pid,{'role':'draft','configuration':{**original_profile['configuration'],'max_turns':2}})
    manifest={}
    for p in (probe/'contracts').rglob('*.sol'):manifest[p.relative_to(probe).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    for filename in ('test/evolution-sbt-behavior.test.ts','test/helpers/material-approval.ts','hardhat.config.ts'):
        manifest[filename]=hashlib.sha256((probe/filename).read_bytes()).hexdigest()
    for filename in ('contracts/core/credential/SBTBase.sol','contracts/core/credential/EvolutionSBT.sol'):
        original='contracts/'+filename
        frozen=db.one('SELECT blob_sha256 FROM repository_snapshot_files WHERE snapshot_id=? AND relative_path=?',(snapshot['id'],original))
        if not frozen or frozen['blob_sha256']!=manifest[filename]:raise RuntimeError('实际编译副本与试点选定来源不同')
    result={**runtime,'actual_execution':True,'verification_origin':'externally_executed_by_coding_agent','snapshot_id':snapshot['id'],'compiled_sources_sha256':manifest,'compiler':'solc0.8.24 optimizer runs1 viaIR; 67 Solidity files compiled','stdout':(probe/'actual-test-output.log').read_text(encoding='utf-8'),'timestamp':db.now(),'egress':'selected_content','public_network_or_real_deployment':False}
    aid=db.uid();sha=blob(result);db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,'隔离副本本地模拟链：转移限制单用例','test_run',sha,db.dumps(result),db.now()))
    execution=evidence.create(pid,{'kind':'test_run','origin_type':'artifact','origin_id':aid,'conditions':['仅一个既有用例','Hardhat本地模拟链1337','材料授权使用离线fixture','不代表公网部署或全部合约安全性']})
    def code(path,a,b,kind='code_observation'):return evidence.create(pid,{'kind':kind,'origin_type':'code','origin_id':snapshot['id'],'locator':{'path':path,'line_start':a,'line_end':b},'conditions':['仅本次准确行范围的源码观察']})
    transfer=code('contracts/contracts/core/credential/SBTBase.sol',55,73);definition=code('contracts/test/evolution-sbt-behavior.test.ts',160,190,'test_definition')
    backend=code('backend/src/domain/ai/agent/runner.ts',19,45);frontend=code('frontend/src/ai/agent/agent-execution.ts',687,716);hook=code('frontend/src/features/ai-assistant/hooks/useAgentExecutor.ts',42,76)
    projects=[('Agent操作暂停的跨层关系','implementation','只写2至3个连续自然段、约400至600中文字符，描述后端将命中特定词的click转为review_pause、前端执行器返回review暂停状态、Hook发出审核提示的静态关系。不能声称匹配器覆盖全部危险操作、已跑过浏览器真实用户流程或绝对安全。',[backend['id'],frontend['id'],hook['id']]),('凭证转移限制的本地单用例结果','results','只写2至3个连续自然段、约400至600中文字符，准确报告记录中一个本地模拟链用例已通过，解释safeTransferFrom拒绝预期与源码的对应。保留模拟环境、离线fixture、单用例范围，不能推广为公网部署或全部安全性。',[transfer['id'],definition['id'],execution['id']])]
    sessions=[]
    for title,kind,goal,ids in projects:
        sid=db.uid();db.execute('INSERT INTO sections(id,project_id,title,kind,position,target_words,goal) VALUES(?,?,?,?,?,?,?)',(sid,pid,title,kind,len(sessions)+1,600,goal));contract=contracts.save_contract(pid,sid,{'goal':goal,'evidence_ids':ids,'depends_on_sections':[]},state(pid)['revision']);sessions.append({'section_id':sid,'contract_id':contract['id']})
    # Freeze both task heads before any generated session captures the state.
    for x in sessions:
        s=coordinator.create(pid,{'section_id':x['section_id'],'operation':'generate_unit','base_revision_id':None,'expected_project_state_revision':state(pid)['revision'],'contract_version_id':x['contract_id'],'profile_id':profile['id'],'policy':{'budget_limit':settings['budget_limit']}});x['session_id']=s['id']
    previous={'project_id':pid,'profile_id':profile['id'],'snapshot_id':snapshot['id'],'runtime_artifact_id':aid,'sessions':sessions};db.set_setting('writing_v4_extended_pilot',previous)
active_profile=profiles.get(pid,previous['profile_id'])
if not active_profile['configuration_current']:
    replacement=profiles.create(pid,{'role':'draft','configuration':{**active_profile['configuration'],'max_turns':1,'max_output_tokens':8192}});previous.setdefault('previous_profile_ids',[]).append(previous['profile_id']);previous['profile_id']=replacement['id']
    for item in previous['sessions']:
        prior=coordinator.get(pid,item['session_id']);item.setdefault('previous_session_ids',[]).append(item['session_id'])
        chapter=db.one('SELECT * FROM sections WHERE id=?',(item['section_id'],));new=coordinator.create(pid,{'section_id':chapter['id'],'operation':'generate_unit','base_revision_id':chapter['current_revision_id'],'expected_project_state_revision':state(pid)['revision'],'contract_version_id':state(pid)['contracts'][chapter['id']],'profile_id':replacement['id'],'policy':{'budget_limit':settings['budget_limit']}});item['session_id']=new['id']
    db.set_setting('writing_v4_extended_pilot',previous)
emit({'phase':'extended_pilots','sessions':previous['sessions']})
while tasks.run_one():emit({'phase':'extended_pilots','states':[coordinator.get(pid,x['session_id'])['status'] for x in previous['sessions']]})
report={**previous,'actual_runtime_proof':json.loads((ROOT/'.runtime/edu-sbt-contract-probe/actual-test-result.json').read_text(encoding='utf-8')),'results':[],'human_approval':'not_claimed'}
for x in previous['sessions']:
    session=coordinator.get(pid,x['session_id']);candidates=[]
    for c in db.rows('SELECT * FROM writing_candidates WHERE session_id=?',(session['id'],)):
        doc=unblob(c['document_blob']);candidates.append({'id':c['id'],'document':doc,'reviews':[{'stage':r['stage'],'result':unblob(r['result_blob'])} for r in db.rows('SELECT r.* FROM writing_verification_runs r JOIN document_analyses a ON a.id=r.analysis_id WHERE a.candidate_id=?',(c['id'],))]})
    entry={'session':session,'candidates':candidates}
    if session['status']=='needs_review' and candidates:entry['draft_adoption']=adoption.adopt(pid,candidates[-1]['id'],{'base_revision_id':None,'accept_policy':'draft'},'extended-pilot-'+session['id'])
    report['results'].append(entry)
report['budget']={'limit':settings['budget_limit'],'charged_or_reserved':db.one("SELECT COALESCE(SUM(budget_charge),0) n FROM generation_budget_usage WHERE project_id=? AND status NOT IN ('rejected','failed_before_send')",(pid,))['n']}
out=ROOT/'docs/writing-agent/extended-real-project-pilots.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');emit({'status':'recorded','states':[x['session']['status'] for x in report['results']],'report':str(out)})
