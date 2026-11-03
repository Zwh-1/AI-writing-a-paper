"""Disposable browser fixture. Remote operations are deterministic synthetic replies."""
import json,os,runpy,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,config,parsers,knowledge_units,tasks,documents,security,model_install
from app import studio_store as store,studio_imports as imports

db.init();stamp=db.now();pid=db.uid();sid=db.uid()
db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'研究助手验收 · 合成论文',stamp,stamp))
db.execute('INSERT INTO sections(id,project_id,title,position) VALUES(?,?,?,?)',(sid,pid,'方法与讨论',0))
section=security.require_section(sid);documents.save(section,documents.text_document('该方法的适用条件需要逐项核对。'),None)
original,_=parsers.register_file(pid,'SyntheticPaper.txt',('1 Introduction\nThe method applies only to synthetic examples [1].\n\n2 Method\nThe sample size is twelve.\n\nReferences\n[1] Example Author. Example Study. doi:10.1234/example').encode(),'research');parsers.parse_file(original['id']);knowledge_units.build(pid,original['id'])
reference=imports.upsert_reference(pid,{'title':'合成研究论文','authors':['示例作者'],'year':'2026','abstract':'The abstract is a synthetic fixture.','tags':['合成数据']},original['id'])
store.save(pid,'note',{'title':'初始阅读想法','text':'需要核对方法条件。','tags':['阅读']})
model_install.status=lambda name:{'ready':False}

def fake_model(task,system,user,manifest,images=None):
    payload=json.loads(user);operation=manifest.get('operation','')
    if operation=='studio_translate':value={'text':'测试译文：'+payload['source']}
    elif operation=='studio_abstract_translation':value={'text':'这是一段合成摘要的译文。'}
    elif operation=='studio_chat':
        value={'text':'这是基于合成验收问题的回复。请继续核对原件。','citations':[]}
        if payload.get('current_document') and 'operations' in payload.get('schema',{}) and '改写当前' in payload['messages'][-1]['text']:
            paragraph=next(item for item in payload['current_document']['paragraphs'] if '需要逐项核对' in item['text'])
            value={'text':'已生成正文改写候选，请逐项确认。','citations':[],'operations':[{'node_id':paragraph['node_id'],'before':'需要逐项核对','after':'须对照原文逐项核对'}]}
    elif operation.startswith('studio_note_'):value={'title':'整理后的阅读思路','text':'先核对方法，再核对限制条件。'}
    elif operation=='studio_completion':value={'text':'应保留样本与方法的适用范围。'}
    elif operation=='studio_comment_reply':value={'text':'建议补充原始来源与适用条件。'}
    elif operation=='studio_webpage':value={'html':'<!doctype html><html><body><h1>合成论文方法演示</h1><input type="range" min="1" max="12" value="6" oninput="document.querySelector(\'output\').value=this.value"><output>6</output></body></html>'}
    elif operation=='studio_guide':value={'summary':'论文讨论合成样本下的方法范围。','keypoints':[{'text':'方法只适用于合成样本','unit_id':payload['units'][0]['id'],'quote':'synthetic examples'}],'questions':['是否可以推广？'],'limitations':['只覆盖合成样本']}
    else:raise AssertionError('Unexpected synthetic model operation: '+operation)
    return {'text':db.dumps(value),'run_id':'synthetic-model-run','usage':{'input_tokens':0,'output_tokens':0},'cost':0}
tasks.model_call=fake_model
imports.openalex=lambda pid,body:{'query':body['query'],'page':1,'total':1,'items':[{'id':'https://openalex.org/WTEST','title':'合成检索结果','authors':['示例作者'],'year':'2026','doi':'10.1234/discovered','url':'https://example.org/paper','abstract':'A synthetic search result.','tags':['发现'],'metadata_source':'fixture'}]}
fixture=ROOT/'.runtime/studio-acceptance-20261102';fixture.mkdir(exist_ok=True)
(fixture/'fixture.json').write_text(db.dumps({'project_id':pid,'section_id':sid,'file_id':original['id'],'reference_id':reference['id'],'port':config.PORT}),encoding='utf-8')
threading.Thread(target=tasks.run_forever,daemon=True).start()
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=config.PORT,log_level='warning',access_log=False)
