"""Synthetic, offline browser fixture; never a real-provider acceptance report."""
import sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
DATA=Path(os.environ.get('PAPER_BROWSER_FIXTURE_DATA',str(ROOT/'.runtime'/'writing-v4-browser')));os.environ['PAPER_DATA_DIR']=str(DATA)
sys.path[:0]=[str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps'),str(ROOT/'backend'),str(ROOT/'tests')]
from app import db,provider,security,tasks
from app.writing_agent import repository,evidence,contracts,profiles,coordinator,evaluations
from app.writing_agent.common import state
import pytest
from test_writing_agent import fake_model

db.init()
if db.one('SELECT id FROM projects LIMIT 1') and '--fresh' not in sys.argv:print('Existing isolated browser fixture retained.');raise SystemExit(0)
db.set_setting('provider',{**provider.DEFAULT,'model':'synthetic-browser-fixture','base_url':'https://example.test/v1','input_price':0,'output_price':0,'context_limit':32000,'max_output_tokens':2048,'max_attempts':1})
p=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(p,'有据写作浏览器验收（合成数据）',stamp,stamp))
from app import rules
db.execute('INSERT INTO rules(id,project_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),p,'保留依据和条件，不得编造测试结果','mandatory','confirmed',stamp));rules.publish(p)
sid=db.uid();db.execute('INSERT INTO sections(id,project_id,title,kind,position,goal) VALUES(?,?,?,?,?,?)',(sid,p,'功能约束','system_design',0,'合取条件与核验范围'))
root=DATA/'synthetic-project';root.mkdir(exist_ok=True);(root/'gate.ts').write_text('export function allowed(issuer: boolean, enabled: boolean) { return issuer && enabled; }\n',encoding='utf-8');(root/'AGENTS.md').write_text('只读调查，保留条件。',encoding='utf-8')
b=repository.bind(p,{'root':str(root),'egress':'selected_content'});snapshot=repository.capture(p,b['id']);e=evidence.create(p,{'kind':'code_observation','origin_type':'code','origin_id':snapshot['id'],'locator':{'path':'gate.ts','line_start':1,'line_end':1}})
patch=pytest.MonkeyPatch();patch.setattr(security,'get_key',lambda profile='':'synthetic-key');patch.setattr(provider,'get_key',lambda:'synthetic-key');fake_model(patch)
core=contracts.save_core(p,{'research_question':'如何保持功能条件的准确表述？','scope':'仅浏览器合成验收数据','method':'冻结源码观察','limitations':['不代表真实模型效果']});contracts.publish_core(p,core['id'],state(p)['revision']);contract=contracts.save_contract(p,sid,{'goal':'解释发行方与启用状态的合取条件','evidence_ids':[e['id']]},state(p)['revision']);profile=profiles.create(p,{'role':'draft','configuration':{'max_output_tokens':2048}})
session=coordinator.create(p,{'section_id':sid,'base_revision_id':None,'expected_project_state_revision':state(p)['revision'],'contract_version_id':contract['id'],'profile_id':profile['id']})
while tasks.run_one():pass
diagnostic=evaluations.create(p,{'profile_id':profile['id'],'case_ids':['D06'],'repetitions':1,'budget_limit':2})
while tasks.run_one():pass
db.set_setting('writing_browser_fixture',{'project_id':p,'section_id':sid,'session_id':session['id'],'diagnostic_id':diagnostic['id'],'source_root':str(root),'synthetic':True});patch.undo()
print('Offline synthetic writing fixture ready.')
