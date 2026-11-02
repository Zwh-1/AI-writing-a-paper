import sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.runtime'/'python-deps'))
sys.path.insert(0,str(ROOT/'.runtime'/'advanced-deps'))
sys.path.insert(0,str(ROOT/'backend'))
if __name__=='__main__':
    mode=sys.argv[1] if len(sys.argv)>1 else 'server'
    if mode=='server':
        import uvicorn
        from app.config import HOST,PORT
        uvicorn.run('app.main:app',host=HOST,port=PORT,log_level='warning',access_log=False)
    elif mode=='collaboration-server':
        import uvicorn
        uvicorn.run('app.collaboration:app',host=os.environ.get('PAPER_COLLAB_BIND','127.0.0.1'),port=int(os.environ.get('PAPER_COLLAB_PORT','8774')),log_level='warning',access_log=False,forwarded_allow_ips=os.environ.get('PAPER_PROXY_IPS','127.0.0.1'))
    elif mode=='collaboration-admin':
        from app import db
        from app.collaboration import create_user
        db.init()
        username=sys.argv[2] if len(sys.argv)>2 else 'admin'
        password=sys.stdin.readline().rstrip('\r\n')
        print(db.dumps(create_user(username,password,True)))
    elif mode=='neural-download':
        from app.retrieval import model_instance,DEFAULT_MODEL
        model_instance(sys.argv[2] if len(sys.argv)>2 else DEFAULT_MODEL,download=True)
        print('Local neural model is ready.')
    elif mode=='portable-resources':
        from app.portable_resources import prepare
        raise SystemExit(1 if prepare()['state']=='failed' else 0)
    elif mode=='portable-resources-start':
        import json
        from app.portable_resources import start_background
        print(json.dumps(start_background(),ensure_ascii=False))
    elif mode=='remote-ai-budget':
        from app import db
        import math
        db.init();amount=float(sys.argv[2])
        if not math.isfinite(amount) or not 0<=amount<=1000000:raise ValueError('Invalid remote AI budget')
        db.set_setting('remote_ai_global_budget',amount);print('Remote AI budget updated.')
    elif mode=='ocr-capabilities':
        import json
        from app.local_ocr import _native_capabilities
        print(json.dumps(_native_capabilities(),ensure_ascii=False))
    elif mode=='ocr-image':
        import asyncio,json
        from app.local_ocr import _recognize_path_native
        value=json.loads(sys.stdin.read(1024*1024))
        print(json.dumps(asyncio.run(_recognize_path_native(value['path'],value.get('language','zh-Hans-CN'))),ensure_ascii=False))
    elif mode=='worker':
        from app.tasks import run_forever
        run_forever()
    elif mode=='test':
        import pytest
        raise SystemExit(pytest.main([str(ROOT/'tests'),'-q',*sys.argv[2:]]))
    elif mode=='seed':
        from app import db,parsers,tasks,material_quality
        db.init()
        projects=db.rows('SELECT * FROM projects ORDER BY created_at')
        if projects: pid=projects[0]['id']
        else:
            pid=db.uid();stamp=db.now();db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'我的毕业论文',stamp,stamp))
        source=ROOT.parent/'格式';added=0
        if source.exists():
            for path in sorted(source.iterdir()):
                if path.is_file() and path.suffix.lower() in parsers.SUPPORTED:
                    role='rules' if path.suffix.lower()=='.pptx' or '说明' in path.name else 'example'
                    f,new=parsers.register_file(pid,path.name,path.read_bytes(),role)
                    if new or f['status']!='ready' or material_quality.summary(f)['repair_needed']: tasks.enqueue(pid,'parse',{'file_id':f['id']});added+=1
        if added:tasks.enqueue(pid,'extract_rules',{})
        print(db.dumps({'project_id':pid,'queued_files':added}))
