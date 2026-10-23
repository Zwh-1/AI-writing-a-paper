"""Named isolated checks. No arbitrary host command is exposed to a model."""
import subprocess,tempfile,time,re
from pathlib import Path
from .. import db,security,controlled_execution
from .common import owned,fail,blob

def register(pid,body):
    security.require_project(pid)
    kind=body.get('kind');name=body.get('name','').strip();image=body.get('image','')
    if kind not in ('node_test','contracts_test') or not isinstance(name,str) or not name or not isinstance(image,str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',image):fail('INVALID_CHECK','请选择有固定镜像哈希的Node/合约检查')
    checks=db.setting('writing_checks:'+pid,{});identity=db.uid();checks[identity]={'id':identity,'name':name,'kind':kind,'image':image,'enabled':False,'max_seconds':60}
    db.set_setting('writing_checks:'+pid,checks);return checks[identity]

def execute(pid,check_id,snapshot_id):
    checks=db.setting('writing_checks:'+pid,{});check=checks.get(check_id);s=owned('snapshot',snapshot_id,pid)
    if not check or not check.get('enabled'):return {'status':'not_run','reason':'检查未登记或未启用'}
    if not controlled_execution.capabilities()['docker_engine']:return {'status':'unavailable','reason':'Docker不可用，未在宿主机回退执行'}
    root=Path(tempfile.mkdtemp(prefix='writing-check-',dir=security.DATA/'tmp'))
    for f in db.rows('SELECT * FROM repository_snapshot_files WHERE snapshot_id=?',(snapshot_id,)):
        dest=(root/f['relative_path']).resolve()
        if not dest.is_relative_to(root.resolve()):fail('INVALID_PATH','快照路径越界')
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(security.safe_object(f['blob_sha256']).read_bytes())
    command=['node','--test'] if check['kind']=='node_test' else ['npm','run','test','--workspace=contracts']
    cli=controlled_execution.docker_cli();container='paper-writing-'+db.uid();argv=[cli,'run','--name',container,'--rm','--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=128','--memory=512m','--cpus=1','--user=65534:65534','--tmpfs=/tmp:rw,nosuid,size=128m','--mount',f'type=bind,source={root},target=/workspace,readonly','--workdir=/workspace',check['image'],*command]
    output=root/'stdout.capture';error=root/'stderr.capture';limited=False
    with output.open('wb') as out,error.open('wb') as err:
        process=subprocess.Popen(argv,stdout=out,stderr=err,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));deadline=time.monotonic()+check['max_seconds']
        while process.poll() is None:
            if time.monotonic()>deadline or output.stat().st_size+error.stat().st_size>2000000:
                limited=True;subprocess.run([cli,'rm','-f',container],capture_output=True,timeout=10,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));process.kill();process.wait();break
            time.sleep(.1)
    def prefix(p):
        with p.open('rb') as f:return f.read(100000).decode('utf-8',errors='replace')
    result={'actual_execution':process.returncode not in (125,126,127),'execution_outcome':'container_start_failed' if process.returncode in (125,126,127) else 'limited' if limited else 'command_completed','exit_code':process.returncode,'timed_out_or_output_limited':limited,'stdout':prefix(output),'stderr':prefix(error),'snapshot_id':snapshot_id,'check_id':check_id,'image':check['image'],'command':command,'network':'none','started_from':'frozen_snapshot','timestamp':db.now(),'egress':'local_only'}
    sha=blob(result);aid=db.uid();db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)',(aid,pid,check['name'],'writing_check',sha,db.dumps(result),db.now()))
    return {'status':'ok' if result.get('exit_code')==0 and not limited else 'failed','artifact_id':aid,**result}
