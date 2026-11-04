"""Start one freshly built application against an empty temporary data folder.

Only localhost health is requested. No project, worker task or provider request
is created; all process handles belong to this smoke run.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


def smoke(executable, manifest, *, timeout=60):
    executable=Path(executable).resolve()
    expected=json.loads(Path(manifest).read_text(encoding='utf-8'))
    if not executable.is_file():raise ValueError('Native application executable is missing')
    if not isinstance(expected.get('build_id'),str):raise ValueError('Expected build identity is missing')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    opener=build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='yantai-native-smoke-') as folder:
        root=Path(folder)
        env={**os.environ,'PAPER_DATA_DIR':str(root/'data'),'PAPER_SESSION_DIR':str(root/'session'),
             'PAPER_PORT':str(port),'PAPER_NO_BROWSER':'1','PAPER_TASK_WATCHDOG':'0'}
        # An inherited API key cannot be used by an empty synthetic runtime.
        env.pop('PAPER_API_KEY',None)
        with (root/'output.log').open('wb') as output:
            process=subprocess.Popen([str(executable)],env=env,cwd=executable.parent,
                stdout=output,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
            try:
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    if process.poll() is not None:raise RuntimeError('Native smoke process exited before readiness')
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/api/v1/health',timeout=2) as response:
                            health=json.load(response)
                        if health.get('status')=='ok':break
                    except (URLError,OSError,ValueError):time.sleep(.25)
                else:raise TimeoutError('Native application health check timed out')
                if health.get('build_id')!=expected['build_id']:
                    raise RuntimeError('Packaged application loaded a different build identity')
                return {'status':'passed','build_id':health['build_id'],
                        'version':health.get('version'),'source_commit':expected.get('source_commit'),
                        'scope':'isolated native localhost startup; no model requests'}
            finally:
                if process.poll() is None:
                    # Request graceful shutdown so the native launcher can
                    # close its own supervised worker, including on Windows.
                    if os.name=='nt':
                        try:process.send_signal(signal.CTRL_BREAK_EVENT)
                        except OSError:process.terminate()
                    else:process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill();process.wait(timeout=5)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,default=Path('build-info.json'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    value=smoke(args.executable,args.manifest)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(value))
    return 0


if __name__=='__main__':raise SystemExit(main())
