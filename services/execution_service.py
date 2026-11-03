"""An authenticated remote broker. User code runs only in Docker, never here."""
import base64,hashlib,hmac,os,sys,threading
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,Header,HTTPException
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'.runtime/python-deps'),str(ROOT/'.runtime/advanced-deps')]
from app import db,security,tasks
from app import studio_execution as execution

@asynccontextmanager
async def lifespan(app):
    db.init()
    image=os.environ.get('PAPER_EXECUTION_IMAGE','')
    if image:
        if not image.startswith('sha256:'):raise RuntimeError('PAPER_EXECUTION_IMAGE must be a fixed sha256 image ID')
        db.set_setting('studio_execution_image',image)
    elif not db.setting('studio_execution_image'):
        import subprocess
        cli=execution.controlled.docker_cli()
        if cli:
            result=subprocess.run([cli,'image','inspect','yantai-execution:local','--format','{{.Id}}'],capture_output=True,timeout=20)
            image=result.stdout.decode().strip()
            if result.returncode==0 and image.startswith('sha256:'):db.set_setting('studio_execution_image',image)
    yield

app=FastAPI(title='Yantai isolated execution broker',lifespan=lifespan);gate=threading.BoundedSemaphore(2)
token=os.environ.get('PAPER_EXECUTION_TOKEN','')
if len(token)<24:raise RuntimeError('Set PAPER_EXECUTION_TOKEN to at least 24 random characters before starting the execution service')

def authorize(value):
    if not hmac.compare_digest(value or '','Bearer '+token):raise HTTPException(401,'Invalid execution credential')

@app.get('/health')
def health(authorization:str=Header(default='')):
    authorize(authorization);return {'ready':execution.controlled.capabilities()['docker_engine'],'image_ready':bool(db.setting('studio_execution_image'))}

@app.post('/execute')
def run(body:dict,authorization:str=Header(default='')):
    authorize(authorization)
    if not gate.acquire(blocking=False):raise HTTPException(429,'Execution capacity reached')
    try:
        db.init();pid='00000000000000000000000000000001'
        if not db.one('SELECT id FROM projects WHERE id=?',(pid,)):
            db.execute('INSERT INTO projects(id,title,created_at,updated_at) VALUES(?,?,?,?)',(pid,'Remote execution',db.now(),db.now()))
        code=body.get('code','');timeout=body.get('timeout_seconds',30)
        if not isinstance(code,str) or not 1<=len(code)<=64000 or type(timeout)is not int or not 1<=timeout<=90:raise HTTPException(422,'Invalid code or timeout')
        inputs=[];total=0
        for item in body.get('files',[]):
            name=Path(item['name']).name
            if name!=item['name'] or len(name)>200:raise HTTPException(422,'Invalid input filename')
            raw=base64.b64decode(item['content_base64'],validate=True);total+=len(raw)
            if total>80*1024*1024 or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise HTTPException(422,'Invalid input bytes or hash')
            inputs.append({'name':name,'sha256':item['sha256'],'raw':raw})
        task=tasks.enqueue(pid,'studio',{'action':'remote_execution','code_hash':hashlib.sha256(code.encode()).hexdigest()})
        try:
            result=execution.local(task,code,timeout,inputs,{'remote':True,'code_sha256':hashlib.sha256(code.encode()).hexdigest()})
            result['images']=[]
            for artifact in result.pop('artifacts'):
                raw=security.safe_object(artifact['sha256']).read_bytes();result['images'].append({'name':artifact['name'],'content_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()})
            db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(task['id'],))
            return result
        except Exception:
            db.execute("UPDATE tasks SET status='failed' WHERE id=?",(task['id'],));raise
    finally:gate.release()
