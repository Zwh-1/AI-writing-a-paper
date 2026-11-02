"""Isolated real-API fixture. Every model call is synthetic."""
import os,sys,tempfile,threading,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'.runtime/python-deps'),str(root/'.runtime/advanced-deps'),str(root/'backend'),str(root/'tests')]
if __name__=='__main__':
    import pytest,uvicorn
    from test_writing_agent import env
    from test_writing_integrity import generate
    from app import db,documents,security,tasks
    import app.main as main
    directory=Path(tempfile.mkdtemp(prefix='writing-integrity-browser-',dir=root/'.runtime'))
    patch=pytest.MonkeyPatch();fixture=env.__wrapped__(directory,patch);e=next(fixture)
    task,result,_=generate(e,patch,visual=True)
    accepted=e['client'].post(f'/api/v1/sections/{e["sid"]}/accept',json={'task_id':task['id'],'base_revision_id':None})
    if accepted.status_code!=200:raise RuntimeError(accepted.text)
    reference=db.uid();db.execute('INSERT INTO bibliography(id,project_id,metadata,created_at) VALUES(?,?,?,?)',(reference,e['pid'],db.dumps({'title':'初始文献标题','authors':['测试作者'],'year':'2025'}),db.now()))
    current=documents.current(security.require_section(e['sid']));doc=db.loads(current['document'])
    doc['content'][0]['content'].append({'type':'citation','attrs':{'citationId':reference}})
    documents.save(security.require_section(e['sid']),doc,current['id'])
    port=int(os.environ.get('PAPER_TEST_PORT','8883'));patch.setattr(main,'PORT',port)
    (root/'.runtime/writing-integrity/browser-fixture.json').write_text(db.dumps({'port':port,'project_id':e['pid'],'section_id':e['sid'],
        'reference_id':reference,'evidence_id':e['eid'],'task_id':task['id'],'data_directory':str(directory),'synthetic_model':True}),encoding='utf-8')
    worker=threading.Thread(target=tasks.run_forever,daemon=True);worker.start()
    try:uvicorn.run(main.app,host='127.0.0.1',port=port,log_level='warning',access_log=False)
    finally:
        tasks.STOP.set();worker.join(timeout=5)
        try:next(fixture)
        except StopIteration:pass
        patch.undo()
