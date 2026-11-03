"""Container/remote execution with bounded, traceable image artifacts."""
import base64
import hashlib
import io
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import HTTPException
from . import db,security,tasks,controlled_execution as controlled
from . import research_records as store
from .literature_connectors import http_client

def settings(pid,body=None):
    security.require_project(pid);key='studio:execution:'+pid
    if body is not None:
        provider=body.get('provider','local-container');url=str(body.get('url','')).strip().rstrip('/')
        if provider not in ('local-container','remote-http'):raise HTTPException(422,'执行引擎无效')
        parts=urlsplit(url)
        if provider=='remote-http' and (not parts.hostname or parts.username or parts.scheme!='https' and parts.hostname not in ('localhost','127.0.0.1','::1')):raise HTTPException(422,'远端执行地址需要 HTTPS；本机服务可使用 HTTP')
        if 'api_key' in body:store.write_secret('execution:'+pid,body['api_key'])
        db.set_setting(key,{'provider':provider,'url':url})
    cfg=db.setting(key,{'provider':'local-container','url':''})
    return {**cfg,'key_configured':bool(store.read_secret('execution:'+pid)),'plot_image_ready':bool(db.setting('studio_execution_image'))}

def health(pid):
    cfg=settings(pid)
    if cfg['provider']=='local-container':return {**controlled.capabilities(),'provider':cfg['provider'],'image_ready':cfg['plot_image_ready']}
    key=store.read_secret('execution:'+pid)
    with http_client(cfg['url'],timeout=15) as client:
        response=client.get(cfg['url']+'/health',headers={'Authorization':'Bearer '+key} if key else {});response.raise_for_status();value=response.json()
    if not isinstance(value,dict):raise ValueError('执行服务没有返回有效状态')
    return {**value,'provider':'remote-http'}

def install(task):
    from .config import ROOT
    cli=controlled.docker_cli()
    if not cli or not controlled.capabilities()['docker_engine']:raise ValueError('请先启动 Docker 引擎')
    tasks.event(task['id'],'构建带绘图库的隔离运行镜像',10)
    result=subprocess.run([cli,'build','-f',str(ROOT/'deployment/execution.Dockerfile'),'-t','yantai-execution:local',str(ROOT/'deployment')],capture_output=True,timeout=1200,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    if result.returncode:raise ValueError('绘图镜像构建失败：'+result.stderr.decode(errors='replace')[-1000:])
    inspected=subprocess.run([cli,'image','inspect','yantai-execution:local','--format','{{.Id}}'],capture_output=True,timeout=20)
    image=inspected.stdout.decode().strip()
    if not image.startswith('sha256:'):raise ValueError('未取得执行镜像的固定 ID')
    db.set_setting('studio_execution_image',image)
    return {'installed':True,'image':image}

def collect(pid,raw_images,metadata):
    from PIL import Image
    if len(raw_images)>12:raise ValueError('输出图片超过12张，请缩小任务')
    result=[]
    for name,raw in raw_images:
        if len(raw)>16*1024*1024:raise ValueError('单张图片超过16MB')
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>16000000:raise ValueError('图片分辨率过大')
            converted=io.BytesIO();image.convert('RGB').save(converted,format='PNG')
        identity=db.uid();sha=security.put_blob(converted.getvalue());safe_name=Path(name.replace('\\','/')).name[:200]
        data={**metadata,'egress':'selected_content','original_output_sha256':hashlib.sha256(raw).hexdigest()}
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(identity,pid,safe_name,'figure',sha,db.dumps(data),db.now()))
        result.append({'artifact_id':identity,'name':safe_name,'sha256':sha,'preview_url':f'/api/v1/projects/{pid}/studio/assets/{identity}'})
    return result

def execute(task,body):
    pid=task['project_id'];cfg=settings(pid);code=body.get('code','');timeout=body.get('timeout_seconds',30)
    if not isinstance(code,str) or not 1<=len(code)<=64000 or type(timeout) is not int or not 1<=timeout<=90:raise ValueError('代码长度或执行时限无效')
    from .product_execution import prepare
    from .resource_refs import validate,source_files
    remote=cfg['provider']=='remote-http'
    inputs,source_refs=prepare(pid,body,remote)
    metadata={'task_id':task['id'],'code_sha256':hashlib.sha256(code.encode()).hexdigest(),'input_hashes':[item['sha256'] for item in inputs],
        'source_file_ids':source_files(pid,source_refs),'source_refs':source_refs,
        'input_manifest':[{key:item[key] for key in ('name','sha256','source_ref') if key in item} for item in inputs],
        'code_blob_sha256':security.put_blob(code.encode()),'execution_provider':cfg['provider'],'editor_type':'python'}
    tasks.event(task['id'],'在隔离引擎执行 Python',10)
    if cfg['provider']=='remote-http':
        key=store.read_secret('execution:'+pid);headers={'Authorization':'Bearer '+key} if key else {}
        with http_client(cfg['url'],timeout=timeout+30) as client:
            response=client.post(cfg['url']+'/execute',headers=headers,json={'code':code,'timeout_seconds':timeout,'files':[{'name':item['name'],'sha256':item['sha256'],'content_base64':base64.b64encode(item['raw']).decode()} for item in inputs]})
            response.raise_for_status();value=response.json()
        if not isinstance(value,dict):raise ValueError('远端执行结果无效')
        validate(pid,source_refs,remote=True)
        images=[]
        for item in value.get('images',[]):
            encoded=item.get('content_base64','')
            if not isinstance(encoded,str) or len(encoded)>23000000:raise ValueError('远端图片输出过大')
            raw=base64.b64decode(encoded,validate=True)
            if item.get('sha256') and hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('远端图片哈希校验失败')
            images.append((item.get('name','image.png'),raw))
            if len(images)>12:raise ValueError('远端输出图片过多')
        return {'stdout':str(value.get('stdout',''))[:512000],'stderr':str(value.get('stderr',''))[:512000],'exit_code':value.get('exit_code'),'timed_out_or_output_limited':bool(value.get('timed_out_or_output_limited')),
                'artifacts':collect(pid,images,metadata),'provider':'remote-http','host_execution':False,'source_refs':source_refs,'input_manifest':metadata['input_manifest']}
    value=local(task,code,timeout,inputs,metadata)
    validate(pid,source_refs)
    return {**value,'source_refs':source_refs,'input_manifest':metadata['input_manifest']}

def local(task,code,timeout,inputs,metadata):
    cli=controlled.docker_cli();image=db.setting('studio_execution_image') or db.setting('sandbox_image')
    if not cli or not controlled.capabilities()['docker_engine']:raise ValueError('Docker 引擎不可用；可在运行设置中选择远端执行服务')
    if not image or not image.startswith('sha256:'):raise ValueError('请先在运行设置中准备隔离绘图镜像')
    from .config import DATA
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    with tempfile.TemporaryDirectory(prefix='studio-exec-',dir=DATA/'tmp') as temporary:
        folder=Path(temporary);source=folder/'input';output=folder/'output';source.mkdir();output.mkdir();output.chmod(0o777)
        (source/'main.py').write_text(code,encoding='utf-8')
        for item in inputs:(source/item['name']).write_bytes(item['raw'])
        wrapper="""import os,runpy
os.chdir('/output')
try:
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 import matplotlib.font_manager as fm
 for name in ['Noto Sans CJK SC','Microsoft YaHei','SimHei','WenQuanYi Micro Hei']:
  if name in [font.name for font in fm.fontManager.ttflist]:
   plt.rcParams['font.sans-serif']=[name]
   plt.rcParams['axes.unicode_minus']=False
   break
except ImportError:
 plt=None
try:
 runpy.run_path('/input/main.py',run_name='__main__')
finally:
 if plt is not None:
  for number in list(plt.get_fignums()):
   plt.figure(number).savefig('/output/figure_%s.png'%number,dpi=120,bbox_inches='tight')
"""
        (source/'runner.py').write_text(wrapper,encoding='utf-8');container='yantai-'+db.uid()
        command=[cli,'run','--name',container,'--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=96','--memory=768m','--cpus=1','--user=65534:65534','--tmpfs=/tmp:rw,noexec,nosuid,size=64m','--env','MPLCONFIGDIR=/tmp/matplotlib','--env','PYTHONIOENCODING=utf-8',
                 '--mount',f'type=bind,source={source},target=/input,readonly','--mount',f'type=bind,source={output},target=/output','--workdir=/output',image,'python','-I','-B','/input/runner.py']
        stdout=folder/'stdout';stderr=folder/'stderr';limited=False
        with stdout.open('wb') as out,stderr.open('wb') as err:
            process=subprocess.Popen(command,stdout=out,stderr=err,**flags);deadline=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    cancelled=db.one('SELECT cancel_requested FROM tasks WHERE id=?',(task['id'],))
                    size=sum(file.stat().st_size for file in output.rglob('*') if file.is_file() and not file.is_symlink())
                    if time.monotonic()>deadline or size>64*1024*1024 or stdout.stat().st_size+stderr.stat().st_size>1024*1024 or cancelled and cancelled['cancel_requested']:
                        limited=True;break
                    tasks.STOP.wait(.1)
                    if tasks.STOP.is_set():limited=True;break
            finally:
                if process.poll() is None:
                    subprocess.run([cli,'rm','-f',container],capture_output=True,timeout=20,**flags);process.kill();process.wait(timeout=10)
        images=[]
        for item in sorted(output.iterdir()):
            if item.is_file() and not item.is_symlink() and item.resolve().is_relative_to(output.resolve()) and item.suffix.lower() in ('.png','.jpg','.jpeg'):
                if item.stat().st_size>16*1024*1024:raise ValueError('输出图片过大')
                images.append((item.name,item.read_bytes()))
        from .resource_refs import validate
        validate(task['project_id'],metadata.get('source_refs',[]))
        return {'exit_code':process.returncode,'timed_out_or_output_limited':limited,'stdout':stdout.read_bytes()[:512000].decode('utf-8',errors='replace'),'stderr':stderr.read_bytes()[:512000].decode('utf-8',errors='replace'),
                'artifacts':collect(task['project_id'],images,{**metadata,'image':image}),'provider':'local-container','network':'none','host_execution':False}
