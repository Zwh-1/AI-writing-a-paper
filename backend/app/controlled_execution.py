"""Docker-only arbitrary-code sandbox and origin-scoped browser capture."""
import hashlib,json,os,shutil,subprocess,tempfile,urllib.parse,time
from pathlib import Path
from . import db,security
from .config import ROOT

_ENGINE_CACHE={}
def docker_cli():
    found=shutil.which('docker')
    fixed=Path(r'C:\Program Files\Docker\Docker\resources\bin\docker.exe')
    return found or (str(fixed) if fixed.exists() else None)
def capabilities():
    cli=docker_cli();now=time.monotonic()
    if now-_ENGINE_CACHE.get('time',-100)>8:
        ready=False;diagnostic='未安装Docker'
        if cli:
            try:
                r=subprocess.run([cli,'info','--format','{{.ServerVersion}}'],capture_output=True,timeout=3,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}));ready=r.returncode==0;diagnostic=r.stdout.decode(errors='replace').strip() if ready else r.stderr.decode(errors='replace')[-300:]
            except subprocess.TimeoutExpired:diagnostic='Docker引擎尚未响应'
        _ENGINE_CACHE.update(time=now,ready=ready,diagnostic=diagnostic)
    return {'docker_cli':bool(cli),'docker_engine':_ENGINE_CACHE['ready'],'docker_diagnostic':_ENGINE_CACHE['diagnostic'],'arbitrary_code_requires_docker':True,'unsafe_host_fallback':False,'screenshot_runtime':bool(node_runtime()[0]),'isolation':'Docker network=none read-only capabilities=none non-root resource limits'}
def install_sandbox():
    cli=docker_cli()
    if not capabilities()['docker_engine']:raise ValueError('Docker引擎未运行，请在本机完成Docker启动后准备镜像')
    r=subprocess.run([cli,'pull','python:3.12-slim'],capture_output=True,timeout=600,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    if r.returncode:raise ValueError('执行镜像准备失败：'+r.stderr.decode(errors='replace')[-500:])
    digest=subprocess.run([cli,'image','inspect','python:3.12-slim','--format','{{.Id}}'],capture_output=True,timeout=10).stdout.decode().strip()
    if not digest.startswith('sha256:'):raise ValueError('未取得可复核的执行镜像ID')
    db.set_setting('sandbox_image',digest);return {'image':'python:3.12-slim','immutable_image_id':digest,'code_executed':False}

def node_runtime():
    bundled=Path(os.environ.get('LOCALAPPDATA','')).parent/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
    # The configured runtime wins; normal desktop installs use the bundled Codex runtime.
    base=Path(os.environ.get('USERPROFILE',''))/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
    local=ROOT/'.runtime/browser';node=os.environ.get('PAPER_NODE') or (str(local/'node.exe') if (local/'ready.json').exists() and (local/'node.exe').exists() else str(base/'node/bin/node.exe'));module=os.environ.get('PAPER_PLAYWRIGHT') or (str(local/'node_modules/playwright') if (local/'ready.json').exists() and (local/'node_modules/playwright').exists() else str(base/'node/node_modules/playwright'))
    return (node if Path(node).exists() else shutil.which('node'),module)

def execute(pid,body):
    cli=docker_cli()
    if not cli or not capabilities()['docker_engine']:raise ValueError('Docker引擎不可用，已拒绝在宿主机直接运行用户代码')
    image=db.setting('sandbox_image')
    if not image or not image.startswith('sha256:'):raise ValueError('请先准备并固定执行镜像')
    if body.get('image') and body['image']!='python:3.12-slim':raise ValueError('不允许自选执行镜像')
    code=body.get('code','');timeout=int(body.get('timeout_seconds',20))
    if not isinstance(code,str) or len(code)>64000 or not 1<=timeout<=60:raise ValueError('代码或时限超出范围')
    parent=security.DATA/'tmp';folder=Path(tempfile.mkdtemp(prefix='sandbox-',dir=parent));script=folder/'main.py';script.write_text(code,encoding='utf-8')
    for fid in body.get('file_ids',[]):
        f=db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1',(fid,pid))
        if not f:raise ValueError('输入文件不属于当前项目')
        shutil.copyfile(security.safe_object(f['sha256']),folder/(fid+f['extension']))
    container='paper-'+db.uid();command=[cli,'run','--name',container,'--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=256m','--cpus=1','--user=65534:65534','--tmpfs=/tmp:rw,noexec,nosuid,size=32m','--env','HTTP_PROXY=','--env','HTTPS_PROXY=','--env','ALL_PROXY=','--env','http_proxy=','--env','https_proxy=','--env','all_proxy=','--mount',f'type=bind,source={folder},target=/input,readonly','--workdir=/input',image,'python','-I','-B','/input/main.py']
    stdout=folder/'stdout.log';stderr=folder/'stderr.log';timed_out=False
    with stdout.open('wb') as out,stderr.open('wb') as err:
        process=subprocess.Popen(command,stdout=out,stderr=err)
        import time
        deadline=time.monotonic()+timeout
        while process.poll() is None:
            if time.monotonic()>deadline or stdout.stat().st_size+stderr.stat().st_size>1024*1024:
                timed_out=True;subprocess.run([cli,'rm','-f',container],capture_output=True,timeout=10);process.kill();process.wait();break
            time.sleep(.05)
    result={'exit_code':process.returncode,'timed_out_or_output_limited':timed_out,'stdout':stdout.read_bytes()[:512000].decode('utf-8',errors='replace'),'stderr':stderr.read_bytes()[:512000].decode('utf-8',errors='replace'),'code_sha256':hashlib.sha256(code.encode()).hexdigest(),'network':'none','host_execution':False,'image':image,'human_interpretation':'pending'}
    return result

def canonical_origin(url):
    parsed=urllib.parse.urlparse(url);host=parsed.hostname
    if not host:raise ValueError('地址缺少主机名')
    port=parsed.port;host='['+host+']' if ':' in host else host
    return parsed.scheme.lower()+'://'+host+((':'+str(port)) if port and port!={'http':80,'https':443}.get(parsed.scheme) else '')

def allowed_target(pid,url):
    parsed=urllib.parse.urlparse(url);origins=db.setting('capture_origins:'+pid,[])
    if parsed.scheme not in ('http','https') or parsed.username or parsed.password or not parsed.hostname or (parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','localhost')):raise ValueError('截图仅允许显式登记的本机HTTP服务')
    sensitive={'api_key','key','access_token','token','password','authorization','sessionid'}
    if any(k.lower() in sensitive for k,v in urllib.parse.parse_qsl(parsed.query)) or any(k.lower() in sensitive for k,v in urllib.parse.parse_qsl(parsed.fragment)):raise ValueError('采集地址不能携带鉴权密钥，请使用加密保存的登录态')
    origin=canonical_origin(url)
    if origin not in origins:raise ValueError('请先登记本项目允许采集的系统地址')
    if parsed.hostname in ('127.0.0.1','localhost') and parsed.port==int(os.environ.get('PAPER_PORT','8765')):raise ValueError('请勿将论文工作台本身登记为研究系统截图目标')
    return origin

def capture(pid,body):
    url=body['url'];origin=allowed_target(pid,url);width=int(body.get('width',1440));height=int(body.get('height',1000))
    if not 320<=width<=3840 or not 240<=height<=2160:raise ValueError('截图尺寸超出范围')
    node,module=node_runtime()
    if not node:raise ValueError('没有可用Node/Playwright运行时')
    folder=Path(tempfile.mkdtemp(prefix='capture-',dir=security.DATA/'tmp'));png=folder/'capture.png';request={'module':module,'url':url,'origin':origin,'width':width,'height':height,'output':str(png),'wait_selector':str(body.get('wait_selector','body'))[:300],'allowed_origins':db.setting('capture_origins:'+pid,[]),'storage_state':load_capture_profile(pid)}
    environment={k:v for k,v in os.environ.items() if k not in ('PAPER_API_KEY','PAPER_API_KEY_FILE','PAPER_GATEWAY_TOKEN')};browser_path=ROOT/'.runtime/browsers'
    if browser_path.exists():environment['PLAYWRIGHT_BROWSERS_PATH']=str(browser_path)
    result=subprocess.run([node,str(ROOT/'scripts/capture-system.cjs'),'-'],input=json.dumps(request).encode(),capture_output=True,timeout=45,env=environment,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    if result.returncode or not png.exists():
        try:failure=json.loads(result.stderr.decode().strip().splitlines()[-1])
        except (ValueError,IndexError):failure={'phase':'browser_process','error_type':'BrowserError'}
        raise ValueError('截图采集失败：阶段 '+str(failure.get('phase'))+'，类型 '+str(failure.get('error_type'))+'。请核对浏览器组件、已登记来源和登录态')
    try:capture_details=json.loads(result.stdout.decode().strip().splitlines()[-1])
    except (ValueError,IndexError):capture_details={}
    sha=security.put_blob(png.read_bytes());aid=db.uid();metadata={'source_url':urllib.parse.urlunparse(urllib.parse.urlparse(url)._replace(fragment='')),'captured_at':db.now(),'viewport':{'width':width,'height':height},'egress':'local_only','capture_method':'Playwright actual browser','capture_details':capture_details,'is_test':bool(body.get('is_test',False))};db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,body.get('name','实际系统截图'),'screenshot',sha,db.dumps(metadata),db.now()));return {'artifact_id':aid,'sha256':sha,'metadata':metadata}


def http_test(pid,body):
    import httpx
    url=body['url'];allowed_target(pid,url);expected=int(body.get('expected_status',200));start=time.monotonic()
    with httpx.Client(timeout=15,trust_env=False,follow_redirects=False) as client:response=client.get(url)
    checks=[{'name':'HTTP状态','expected':expected,'actual':response.status_code,'passed':response.status_code==expected}]
    if body.get('contains'):checks.append({'name':'响应包含指定文字','passed':str(body['contains']) in response.text})
    return {'url':url,'method':'GET','checked_at':db.now(),'elapsed_ms':round((time.monotonic()-start)*1000,2),'response_sha256':hashlib.sha256(response.content).hexdigest(),'checks':checks,'passed':all(c['passed'] for c in checks),'data_origin':'actual_http_response','human_interpretation':'pending'}


def store_capture_profile(pid,state):
    if os.name!='nt':raise ValueError('当前登录态保存使用Windows DPAPI；远程服务不保存浏览器登录态')
    if not isinstance(state,dict) or set(state)-{'cookies','origins'}:raise ValueError('需要Playwright storage_state格式的登录态')
    origins=db.setting('capture_origins:'+pid,[]);hosts={urllib.parse.urlparse(x).hostname for x in origins}
    for cookie in state.get('cookies',[]):
        if cookie.get('name') in ('paper_session','paper_collaboration'):raise ValueError('登录态包含论文工作台会话，请只导出目标研究系统所需的登录态')
        domain=str(cookie.get('domain','')).lstrip('.')
        if not domain or not any(host==domain or host.endswith('.'+domain) for host in hosts):raise ValueError('Cookie域不属于本项目已登记的系统地址')
    for origin in state.get('origins',[]):
        if origin.get('origin') not in origins:raise ValueError('LocalStorage来源不属于本项目已登记地址')
    raw=json.dumps(state).encode()
    if len(raw)>2*1024*1024:raise ValueError('登录态超过2MB')
    directory=security.DATA/'private';directory.mkdir(exist_ok=True);path=directory/('capture-'+pid+'.dpapi');temporary=path.with_suffix('.tmp');temporary.write_bytes(security.protect(raw));os.replace(temporary,path);return {'saved':True,'cookies':len(state.get('cookies',[])),'storage':'Windows DPAPI','included_in_backup_or_cloud':False}

def load_capture_profile(pid):
    path=security.DATA/'private'/('capture-'+pid+'.dpapi')
    return json.loads(security.protect(path.read_bytes(),decrypt=True)) if path.exists() else None
