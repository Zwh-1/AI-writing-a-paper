"""Launch only the separate loopback collaboration gateway, without inheriting model keys."""
import json,os,subprocess,sys,time,hashlib
from . import db,security
from .config import ROOT

def start_collaboration(port=8774):
    import httpx
    port=int(port)
    if not 1024<=port<=65535 or port==int(os.environ.get('PAPER_PORT','8765')):raise ValueError('协作端口无效')
    identity=hashlib.sha256(str(security.DATA.resolve()).encode()).hexdigest()[:16];url='http://127.0.0.1:'+str(port)
    try:
        with httpx.Client(timeout=1,trust_env=False) as c:
            r=c.get(url+'/health')
            if r.status_code==200 and r.json().get('service')=='paper-collaboration':
                if r.json().get('data_id')!=identity:raise ValueError('该端口的协作服务使用另一份数据，拒绝复用')
                return {'url':url,'already_running':True}
            if r.status_code<500:raise ValueError('协作端口已被其他服务占用')
    except httpx.RequestError:pass
    env={k:v for k,v in os.environ.items() if k not in ('PAPER_API_KEY','PAPER_API_KEY_FILE','PAPER_GATEWAY_TOKEN')};env.update(PAPER_DATA_DIR=str(security.DATA),PAPER_COLLAB_BIND='127.0.0.1',PAPER_COLLAB_PORT=str(port),PAPER_COLLAB_HOSTS='127.0.0.1,localhost',PAPER_COLLAB_ORIGINS=url,PAPER_COLLAB_INSECURE='1')
    stdout=(security.DATA/'logs/collaboration.out.log').open('ab');stderr=(security.DATA/'logs/collaboration.error.log').open('ab')
    try:process=subprocess.Popen([sys.executable,'-I','-X','utf8',str(ROOT/'runner.py'),'collaboration-server'],cwd=ROOT,env=env,stdout=stdout,stderr=stderr,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    finally:stdout.close();stderr.close()
    db.set_setting('collaboration_service',{'pid':process.pid,'parent_pid':os.getpid(),'started_at':db.now(),'url':url,'bind':'127.0.0.1'})
    for _ in range(30):
        if process.poll() is not None:raise ValueError('协作服务启动失败，请查看本机日志')
        try:
            with httpx.Client(timeout=.5,trust_env=False) as c:
                r=c.get(url+'/health')
                if r.status_code==200 and r.json().get('service')=='paper-collaboration':return {'url':url,'pid':process.pid,'bind':'127.0.0.1','model_api_key_inherited':False}
        except httpx.RequestError:pass
        time.sleep(.1)
    return {'url':url,'pid':process.pid,'status':'starting','bind':'127.0.0.1'}


def stop_collaboration():
    import datetime,psutil
    state=db.setting('collaboration_service')
    if not state:return {'stopped':False,'reason':'当前工作台未启动受管协作进程'}
    try:process=psutil.Process(int(state['pid']))
    except psutil.NoSuchProcess:db.set_setting('collaboration_service',None);return {'stopped':True,'already_exited':True}
    arguments=process.cmdline();expected=str((ROOT/'runner.py').resolve()).replace('\\','/').casefold()
    if not any(str(a).replace('\\','/').casefold()==expected for a in arguments) or 'collaboration-server' not in arguments:raise ValueError('PID不再对应受管协作服务，拒绝停止')
    created=datetime.datetime.fromisoformat(state['started_at']).timestamp()
    if abs(process.create_time()-created)>15:raise ValueError('进程创建时间不匹配，拒绝停止复用PID')
    process.terminate()
    try:process.wait(5)
    except psutil.TimeoutExpired:process.kill();process.wait(3)
    db.set_setting('collaboration_service',None);return {'stopped':True,'pid':state['pid']}
