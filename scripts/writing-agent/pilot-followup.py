"""Bounded real pilot revision; model review is never labeled human acceptance."""
import os,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];DATA=ROOT/'.runtime/writing-v4-live-data';os.environ['PAPER_DATA_DIR']=str(DATA)
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend')]
from app import db,tasks,documents
from app.writing_agent import profiles,coordinator,revision,adoption,paper_review
from app.writing_agent.common import unblob

def emit(v):print(json.dumps(v,ensure_ascii=False),flush=True)
db.init();info=db.setting('writing_v4_live_acceptance');pid=info['project_id'];old=coordinator.get(pid,info['pilot_session_id'])
if info.get('pilot_revision_session_id'):session=coordinator.get(pid,info['pilot_revision_session_id']);profile=profiles.get(pid,session['profile_id'])
else:
    previous=profiles.get(pid,info['profile_id']);profile=profiles.create(pid,{'role':'draft','configuration':previous['configuration']})
    session=revision.revise(pid,old['id'],{'profile_id':profile['id'],'instructions':'将候选压缩为3至4个连续自然段，约600至900个中文字符，仅解释_update合取条件、父类调用边界、EvolutionSBT继承声明和测试定义意图。删除项目brief中的验收说明和不必要的逐条元叙述，不写实验通过、部署或安全效果。保留源码范围、测试未执行及不能推出全面防伪的限制。'});info['pilot_revision_session_id']=session['id'];info['pilot_revision_profile_id']=profile['id'];db.set_setting('writing_v4_live_acceptance',info)
emit({'phase':'real_pilot_revision','session_id':session['id']})
if session['status']=='budget_blocked':coordinator.resume(pid,session['id'])
while tasks.run_one():emit({'phase':'real_pilot_revision','status':coordinator.get(pid,session['id'])['status']})
s=coordinator.get(pid,session['id']);output={'session':s,'candidates':[],'human_review':'not_claimed'}
for c in db.rows('SELECT * FROM writing_candidates WHERE session_id=?',(s['id'],)):
    output['candidates'].append({'id':c['id'],'document':unblob(c['document_blob']),'reviews':[{'stage':r['stage'],'method':r['method'],'status':r['status'],'result':unblob(r['result_blob'])} for r in db.rows('SELECT r.* FROM writing_verification_runs r JOIN document_analyses a ON a.id=r.analysis_id WHERE a.candidate_id=?',(c['id'],))]})
if s['status']=='needs_review' and output['candidates']:
    cid=output['candidates'][-1]['id'];adopted=adoption.adopt(pid,cid,{'base_revision_id':None,'accept_policy':'draft'},'real-project-pilot-draft-v4');output['draft_adoption']=adopted
    emit({'phase':'draft_adopted','review_state':adopted['review_state'],'strict_acceptance':False})
    if not db.one("SELECT id FROM tasks WHERE project_id=? AND kind='agent_paper_review'",(pid,)):tasks.enqueue(pid,'agent_paper_review',{'profile_id':profile['id']})
    while tasks.run_one():pass
    output['coverage']=paper_review.coverage(pid);output['draft_export_report']=paper_review.export_report(pid,False)
out=ROOT/'docs/writing-agent/real-project-pilot-followup.json';out.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8');emit({'status':s['status'],'report':str(out),'strict_human_approval':'not_claimed'})
