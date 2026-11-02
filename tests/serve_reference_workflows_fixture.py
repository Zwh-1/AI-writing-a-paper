"""Isolated reference workflow acceptance corpus. Never call a remote model."""
import runpy
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT / 'runner.py'), run_name='runtime_setup')
from app import db, config, parsers, knowledge_units, knowledge_builds, model_install, provider, tasks

db.init()
pid, sid = db.uid(), db.uid()
stamp = db.now()
db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',
           (pid, '文献功能验收 · 合成数据', stamp, stamp))
db.execute('INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)', (sid, pid, '文献研究', 0))
db.set_setting('knowledge_auto_refresh:' + pid, False)
db.set_setting('reference_knowledge:' + pid, {'semantic_enabled': False})
model_install.status = lambda name: {'ready': False}

def refuse_remote(*args, **kwargs):
    raise AssertionError('Reference acceptance fixture forbids remote model calls')

provider.generate = refuse_remote
files = []
for name, text in [
    ('甲方法研究.txt', '研究方法采用单用户凭证验证。\n\n适用条件仅为单用户环境。\n\n实验结果只适用于十二份模拟样本。\n\n研究局限是不支持多用户并发。'),
    ('乙并发研究.txt', '研究方法采用多用户并发凭证验证。\n\n乙研究独有定位词蓝鲸，涉及并发条件。\n\n实验结果不可推广到生产环境。'),
]:
    original, _ = parsers.register_file(pid, name, text.encode('utf-8'), 'research')
    parsers.parse_file(original['id'])
    knowledge_units.build(pid, original['id'])
    db.execute('INSERT INTO bibliography(id,project_id,metadata,file_id,verification,created_at) VALUES(?,?,?,?,?,?)',
               (db.uid(), pid, db.dumps({'title': name, 'authors': ['合成作者'], 'year': '2026', 'language': 'zh', 'scope': 'full_text'}), original['id'], 'unverified', stamp))
    files.append(original)
task = knowledge_builds.start(pid, {'mode': 'literal', 'namespace': 'literature'})
result = knowledge_builds.process(task, db.loads(task['payload'], {}))
db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?", (db.dumps(result), task['id']))
db.execute("UPDATE tasks SET status='succeeded' WHERE project_id=? AND status='queued'", (pid,))
out = ROOT / '.runtime' / 'reference-workflows-20261031'
out.mkdir(exist_ok=True)
(out / 'fixture.json').write_text(db.dumps({'port': config.PORT, 'project_id': pid, 'section_id': sid,
                                          'first_file': files[0]['id'], 'second_file': files[1]['id']}), encoding='utf-8')
threading.Thread(target=tasks.run_forever, daemon=True).start()
import uvicorn
uvicorn.run('app.main:app', host='127.0.0.1', port=config.PORT, log_level='warning', access_log=False)
