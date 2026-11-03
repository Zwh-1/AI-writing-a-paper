"""Disposable interop acceptance server; no personal files, credentials or paid calls."""
import io
import json
import os
import runpy
import socket
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get('PAPER_PORT'):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        os.environ['PAPER_PORT'] = str(listener.getsockname()[1])
runpy.run_path(str(ROOT / 'runner.py'), run_name='runtime_setup')
from app import db, config, security, documents, parsers, knowledge_units, material_summary, tasks, model_install
from app import research_records as records, asset_service, reference_service
from app import paper_visuals

if not os.environ.get('PAPER_DATA_DIR') or Path(config.DATA) == ROOT / 'data':
    raise RuntimeError('An explicit disposable data directory is required')
db.init()
pid, sid, stamp = db.uid(), db.uid(), db.now()
db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)', (pid, '产物互通验收 · 合成项目', stamp, stamp))
db.execute('INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)', (sid, pid, '方法与讨论', 0))
documents.save(security.require_section(sid), documents.text_document('这段原有正文应完整保留。'), None)
original, _ = parsers.register_file(pid, '互通验收原文.txt', '合成实验使用十二个样本，结论仅适用于当前条件。'.encode(), 'research')
parsers.parse_file(original['id'])
knowledge_units.build(pid, original['id'])
book=reference_service.upsert_external(pid, {'title': '合成研究论文', 'authors': ['测试作者'], 'year': '2026'}, original['id'])
fingerprint = material_summary.source(original['id'])[2]
data = {'overview': '合成实验使用十二个样本。', 'key_points': [{'text': '结论仅适用于当前条件。', 'pages': [1]}]}
db.execute('INSERT INTO material_summaries VALUES(?,?,?,?,?,?,?)', (original['id'], fingerprint, 'fixture', db.dumps(data), 'synthetic', None, db.now()))
note = records.notes.save(pid, {'title': '互通阅读随记', 'text': '应保留样本范围和适用条件。'})
records.save(pid, 'translation', {'title': '方法译文', 'language': 'en', 'text': 'Twelve samples under the specified conditions.'}, owner=original['id'])
fid = db.uid()
db.execute('INSERT INTO facts(id,project_id,label,statement,created_at,updated_at) VALUES(?,?,?,?,?,?)', (fid, pid, '合成研究事实', '本项成果仍处于计划阶段。', db.now(), db.now()))
db.execute('INSERT INTO advanced_runs VALUES(?,?,?,?,?,?)', (db.uid(), pid, 'advanced_statistics', db.dumps({'file_id': original['id'], 'method': 'welch_t'}), db.dumps({'source_sha256': original['sha256'], 'n': 12, 'p_value': .04}), db.now()))
from PIL import Image, ImageDraw
image = Image.new('RGB', (720, 260), '#eaf0ec')
draw = ImageDraw.Draw(image)
draw.rectangle((40, 70, 280, 190), outline='#37624b', width=4)
draw.rectangle((440, 70, 680, 190), outline='#37624b', width=4)
draw.line((280, 130, 440, 130), fill='#37624b', width=6)
draw.text((90, 125), 'INPUT', fill='#243f31')
draw.text((500, 125), 'OUTPUT', fill='#243f31')
raw = io.BytesIO()
image.save(raw, format='PNG')
aid = db.uid()
db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)', (aid, pid, '互通流程图.png', 'figure', security.put_blob(raw.getvalue()), db.dumps({'egress': 'selected_content'}), db.now()))
asset_service.get(pid, aid)
screenshot_id=db.uid()
db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(screenshot_id,pid,'复查真实截图','screenshot',security.put_blob(raw.getvalue()),db.dumps({'egress':'local_only','capture_method':'actual browser'}),db.now()))
table=paper_visuals.create_visual(pid,{'spec':{'kind':'three_line_table','title':'互通原生三线表','headers':['Group','Count'],'rows':[['A','12']],'note':'Synthetic data'}})
empty_sid=db.uid()
db.execute('INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)',(empty_sid,pid,'尚未写作的复查章节',1))
rich_document=documents.normalize({'type':'doc','content':[
    {'type':'paragraph','content':[{'type':'text','text':'复查富文本条件。','marks':[{'type':'bold'}]},{'type':'citation','attrs':{'citationId':book['id']}}]},
    {'type':'formula','attrs':{'latex':'x^2+y^2'}},
    {'type':'figure','attrs':{'artifactId':aid,'assetVersionId':asset_service.get(pid,aid)['asset_version_id'],'caption':'复查图像'}},
    asset_service.get(pid,table['id'])['node'],
]})
rich_note=records.notes.save(pid,{'title':'复查富文本随记','text':documents.plain(rich_document),'document':rich_document})
canvas=records.canvases.save(pid,{'title':'复查画布','scene':{'version':1,'elements':[]}})
from app import parsing_service,rules
from app.main import add_rule
rule=add_rule(pid,{'statement':'复查规则要求保留全部条件。','modality':'mandatory'})
package=rules.publish(pid)
parse_task=tasks.enqueue(pid,'studio',{'action':'parse','file_id':original['id']})
parse_result=parsing_service.save_result(parse_task,original,'surya',{'blocks':[{'type':'text','page':1,'text':'复查解析结果保留实际原文结构。'}]},None)
db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(parse_task['id'],))
model_install.status = lambda name: {'ready': False}
output = Path(os.environ['PAPER_SESSION_DIR'])
output.mkdir(parents=True, exist_ok=True)
if os.environ.get('PAPER_MODULE_ACCEPTANCE')=='1':
    from app import execution_service
    code_conversation=records.conversations.save(pid,{'title':'模块通信执行验收'})
    records.save(pid,'message',{'role':'assistant','text':'可用以下代码读取所选产物：\n```python\nimport json\nwith open("/input/products.json", encoding="utf-8") as stream:\n    print(json.load(stream)["products"][0]["text"])\n```'},owner=code_conversation['id'])
    def synthetic_local(task,code,timeout,inputs,metadata):
        envelope=json.loads(next(item['raw'] for item in inputs if item['name']=='products.json'))
        (output/'execution-input.json').write_text(db.dumps({'envelope':envelope,'manifest':metadata['input_manifest']}),encoding='utf-8')
        source=envelope['products'][0]['text']
        image=io.BytesIO();Image.new('RGB',(180,100),'#eaf0ec').save(image,format='PNG')
        return {'exit_code':0,'stdout':'实际接收到产物：'+source,'stderr':'','artifacts':execution_service.collect(pid,[('模块执行产物.png',image.getvalue())],metadata),'host_execution':False}
    execution_service.local=synthetic_local
(output / 'fixture.json').write_text(db.dumps({'project_id': pid, 'section_id': sid, 'empty_section_id':empty_sid,'file_id': original['id'], 'note_id': note['id'], 'rich_note_id':rich_note['id'],'canvas_id':canvas['id'],'parse_result_id':parse_result['id'],'rule_id':rule['id'],'asset_id': aid,'screenshot_id':screenshot_id, 'table_id':table['id'], 'port': config.PORT}), encoding='utf-8')

def fake_model(task, system, user, manifest, images=None):
    payload = json.loads(user)
    (output / 'model-input.json').write_text(db.dumps(payload), encoding='utf-8')
    assert payload.get('research_products'), 'Selected products must reach the actual model input'
    return {'text': db.dumps({'text': '已读取所选产物，并保留适用条件。', 'citations': []}), 'run_id': 'synthetic', 'usage': {}, 'cost': 0}

tasks.model_call = fake_model
threading.Thread(target=tasks.run_forever, daemon=True).start()
import uvicorn
uvicorn.run('app.main:app', host='127.0.0.1', port=config.PORT, log_level='warning', access_log=False)
