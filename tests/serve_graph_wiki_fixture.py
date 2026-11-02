"""Synthetic UI fixture; never writes to the user's main database."""
import json,runpy,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,config,parsers,knowledge_units as units,knowledge_builds as builds,knowledge_comparisons
db.init();pid=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'图谱与Wiki浏览器合成验收',stamp,stamp))
files=[]
for i,text in enumerate(['方法仅适用于单用户环境。\n\n样本数为12，并发场景尚未验证。','另一项研究采用分层结构。\n\n单用户环境下验证，不能推出并发性能结论。']):
    source,_=parsers.register_file(pid,f'合成研究{i+1}.txt',text.encode(),'research');parsers.parse_file(source['id']);units.build(pid,source['id']);files.append(source['id'])
sid=db.uid();db.execute('INSERT INTO sections(id,project_id,title,kind,position) VALUES(?,?,?,?,?)',(sid,pid,'研究现状','body',0))
task=builds.start(pid,{'mode':'literal','namespace':'literature'});result=builds.process(task,db.loads(task['payload'],{}));db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?",(db.dumps(result),task['id']))
folder=ROOT/'.runtime/graph-wiki-delivery-20261027/browser';folder.mkdir(parents=True,exist_ok=True);(folder/'fixture.json').write_text(db.dumps({'port':config.PORT,'project_id':pid,'section_id':sid,'files':files}),encoding='utf-8')
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=config.PORT,access_log=False,log_level='warning')
