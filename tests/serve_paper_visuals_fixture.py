"""Isolated UI acceptance data and synthetic AI; no user projects or paid requests."""
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'.runtime'/'python-deps'),str(ROOT/'.runtime'/'advanced-deps'),str(ROOT/'backend')]

if __name__ == '__main__':
    import copy
    port = int(os.environ.get('PAPER_TEST_PORT','8807'))
    directory = Path(tempfile.mkdtemp(prefix='paper-visuals-browser-',dir=ROOT/'.runtime'))
    os.environ['PAPER_DATA_DIR'] = str(directory)
    os.environ['PAPER_PORT'] = str(port)
    import uvicorn
    from app import db,security,tasks,documents
    from app.main import app
    from app.paper_visuals import TEMPLATES
    from app import image_generation
    db.init()
    pid,sid,stamp = db.uid(),db.uid(),db.now()
    db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'图表功能隔离验收',stamp,stamp))
    db.execute('INSERT INTO sections(id,project_id,title,kind,position) VALUES(?,?,?,?,?)',(sid,pid,'第三章 系统设计','system_design',0))
    documents.save(security.require_section(sid),documents.text_document('管理员维护学生、课程和成绩。录入成绩后校验数值，有效时保存，无效时返回修改。学生与课程通过成绩记录关联。'),None)
    def synthetic(task,system,user,manifest,images=None):
        value = db.loads(user)
        spec = copy.deepcopy(value.get('current_spec') or TEMPLATES[value['kind']])
        spec['title'] = '本节'+{'flowchart':'成绩录入流程','use_case':'管理员用例','er':'数据关系','three_line_table':'字段说明'}[value['kind']]
        return {'text':db.dumps({'spec':spec,'missing_materials':[]}), 'run_id':'synthetic-visual-test'}
    tasks.model_call = synthetic
    security.save_key('synthetic-independent-image-key',image_generation.PROFILE)
    def synthetic_image(cfg,key,prompt,reference=None):
        import base64,io
        from PIL import Image
        image=Image.new('RGB',(2048,1152),'white');buffer=io.BytesIO();image.save(buffer,format='PNG')
        return {'model':'gpt-image-2','data':[{'b64_json':base64.b64encode(buffer.getvalue()).decode()}],
                'usage':{'input_tokens':10,'output_tokens':20}}
    image_generation.request_image = synthetic_image
    output = ROOT/'.runtime'/'paper-visuals-qa'
    output.mkdir(exist_ok=True)
    (output/'fixture.json').write_text(db.dumps({'port':port,'project_id':pid,'section_id':sid,'directory':str(directory),'synthetic_ai':True}),encoding='utf-8')
    worker = threading.Thread(target=tasks.run_forever,daemon=True)
    worker.start()
    try:
        uvicorn.run(app,host='127.0.0.1',port=port,log_level='warning',access_log=False)
    finally:
        tasks.STOP.set()
        worker.join(timeout=5)
