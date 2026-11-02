"""Small explicitly synthetic corpus for three-workspace integration testing."""
import json,runpy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,config,parsers,knowledge_units as units,knowledge_builds as builds,model_install
db.init();pid=db.uid();stamp=db.now()
db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'三页面整合 · 合成验收',stamp,stamp))
files=[]
for name,text,role in [('学校写作要求.txt','摘要应说明研究方法和适用条件。\n\n参考文献应能定位原文。','rules'),
                       ('方法甲研究.txt','方法甲仅适用于单用户场景。\n\n样本数为12，并发场景尚未验证。','research'),
                       ('写作示例.txt','本篇示例用于说明方法比较的写法。\n\n示例的测试结果不能证明本项目已经运行。','example')]:
    source,_=parsers.register_file(pid,name,text.encode(),'research' if role=='research' else role);parsers.parse_file(source['id']);units.build(pid,source['id'],allow_material=True);files.append(source)
db.execute('INSERT INTO file_source_profiles VALUES(?,?,?,?)',(files[2]['id'],'research_publication','仅用于多用途机制的合成测试',db.now()))
db.execute('INSERT INTO file_usage_additions VALUES(?,?,?,?)',(files[2]['id'],'research','合成追加研究用途',db.now()))
for f in files[1:]:
    if not db.one('SELECT id FROM bibliography WHERE file_id=?',(f['id'],)):
        db.execute('INSERT INTO bibliography(id,project_id,metadata,file_id,verification,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),pid,db.dumps({'title':f['name'],'authors':['合成作者'],'year':'2025','language':'zh','scope':'full_text'}),f['id'],'unverified',db.now()))
source=db.one('SELECT * FROM sources WHERE file_id=? ORDER BY id LIMIT 1',(files[0]['id'],))
rid=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?,?)',(rid,pid,source['id'],'摘要应说明研究方法和适用条件。','recommended','confirmed',db.now()))
db.execute("UPDATE pages SET review_state='reviewed' WHERE file_id=?",(files[0]['id'],))
db.execute('INSERT INTO terms VALUES(?,?,?,?)',(db.uid(),pid,'条件','condition'))
sid=db.uid();db.execute('INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)',(sid,pid,'研究现状',0))
model_install.status=lambda name:{'ready':False}
task=builds.start(pid,{'mode':'literal','namespace':'all'});result=builds.process(task,db.loads(task['payload'],{}))
db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps(result),task['id']))
db.execute("UPDATE tasks SET status='succeeded' WHERE project_id=? AND status='queued'",(pid,))
db.execute('INSERT INTO generation_runs(id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?)',(db.uid(),pid,'outcome_unknown','synthetic-no-request','openai',db.dumps({'synthetic':True}),1,db.now()))
second=db.uid();db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(second,'另一项目 · 隔离验收',stamp,stamp))
f,_=parsers.register_file(second,'隔离原件.txt','另一个项目的原文。'.encode(),'research');parsers.parse_file(f['id']);db.execute("UPDATE tasks SET status='succeeded' WHERE project_id=?",(second,))
out=ROOT/'.runtime/source-workspace-refactor-20261028';out.mkdir(exist_ok=True)
(out/'fixture.json').write_text(db.dumps({'port':config.PORT,'project_id':pid,'other_project_id':second,'rule_file':files[0]['id'],'research_file':files[1]['id'],'dual_file':files[2]['id'],'rule_id':rid,'section_id':sid}),encoding='utf-8')
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=config.PORT,log_level='warning',access_log=False)
