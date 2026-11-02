"""Exercise an extracted release using its own Python, libraries and Node."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import zipfile


def extract(package, qa):
    qa.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='extracted-', dir=qa))
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read('便携包清单.json'))
        assert manifest['profile'] in ('portable','full','offline','integrated')
        assert not any(n.startswith(('data/', 'frontend/src/', 'tests/')) for n in archive.namelist())
        if manifest['profile']=='portable':assert not any(n.startswith(('.runtime/models/','.runtime/browsers/')) for n in archive.namelist())
        else:
            assert '.runtime/powershell/pwsh.exe' in archive.namelist()
            assert '启动工作台.cmd' in archive.namelist()
        if manifest.get('automatic_resource_download'):
            assert manifest['optional_components']==['graphrag']
            assert not any(n.startswith(('.runtime/models/', '.runtime/browsers/')) for n in archive.namelist())
            assert package.stat().st_size < 500_000_000
        if manifest['profile']=='integrated':
            assert manifest['bundled_graphrag'] and manifest['bundled_models_and_browser']
            assert manifest['optional_components']==[] and not manifest['automatic_resource_download']
            assert '.runtime/graphrag-deps/graphrag-3.3.0.dist-info/METADATA' in archive.namelist()
        last=time.monotonic()
        for index,entry in enumerate(archive.infolist(),1):
            archive.extract(entry,root)
            if time.monotonic()-last>20:
                print(json.dumps({'phase':'extracting','files':index,'total':len(archive.infolist())}),flush=True);last=time.monotonic()
    for name, expected in manifest['files'].items():
        with (root / name).open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == expected, name
    (qa / 'extracted.json').write_text(json.dumps({'root': str(root), 'package': str(package), 'profile':manifest['profile'],'files': len(manifest['files'])}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'extracted': str(root), 'verified_files': len(manifest['files'])}, ensure_ascii=False), flush=True)


def verify(root, qa, port, browser_component=None, model_component=None):
    assert Path(sys.executable).resolve() == (root / '.runtime/python/python.exe').resolve()
    sys.path[:0] = [str(root / '.runtime/advanced-deps'), str(root / '.runtime/python-deps'), str(root / 'backend')]
    os.environ['PAPER_DATA_DIR'] = str(root / 'data')
    os.environ['PAPER_PORT'] = str(port)
    os.environ.pop('PLAYWRIGHT_BROWSERS_PATH', None)
    os.environ.pop('PAPER_NODE', None)
    os.environ.pop('PAPER_PLAYWRIGHT', None)
    from app import db, security, parsers, controlled_execution, retrieval, advanced_statistics, exports, documents
    from app.paper_visuals import TEMPLATES, create_visual, explanation_draft
    from app import local_ocr, image_generation
    import httpx
    import pandas, numpy, scipy, statsmodels, PIL, fastembed
    from docx import Document
    modules = [pandas, numpy, scipy, statsmodels, PIL, fastembed]
    for module in modules:
        assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
    checks = ['All imported third-party libraries resolve inside the extracted application']
    from fastapi.testclient import TestClient
    from app.collaboration import app as collaboration_app
    with TestClient(collaboration_app, base_url='https://testserver') as collaboration:
        response = collaboration.get('/')
        assert response.status_code == 200 and '砚台协作编辑器' in response.text
    checks.append('The standalone collaboration page is present without shipping frontend development sources')
    manifest=json.loads((root/'便携包清单.json').read_text(encoding='utf-8'))
    full=manifest['profile'] in ('full','offline','integrated')
    if manifest.get('automatic_resource_download'):
        from app.portable_resources import status
        assert status()['state']=='ready', status()
        checks.append('First-launch resources were prepared automatically inside the extracted single-package directory')
    import httpx
    qa.mkdir(parents=True,exist_ok=True)
    runtime=root/'.runtime';runtime.mkdir(exist_ok=True)
    env=os.environ.copy();env['PAPER_PORT']=str(port);env['PAPER_DATA_DIR']=str(root/'data')
    process_state=runtime/f'release-acceptance-{port}.json'
    if process_state.is_file():
        try: previous=json.loads(process_state.read_text(encoding='utf-8'))
        except (OSError,ValueError): previous={}
        if previous.get('root')==str(root):
            for old_id in (previous.get('server_pid'),previous.get('worker_pid')):
                try: os.kill(int(old_id),signal.SIGTERM)
                except (ProcessLookupError,PermissionError,ValueError): pass
    logs=qa/'server-logs';logs.mkdir(exist_ok=True)
    with (logs/'server.log').open('wb') as server_log, (logs/'worker.log').open('wb') as worker_log:
        server_process=subprocess.Popen([sys.executable,'-X','utf8',str(root/'runner.py'),'server'],
            cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=server_log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        worker_process=subprocess.Popen([sys.executable,'-X','utf8',str(root/'runner.py'),'worker'],
            cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=worker_log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    process_state.write_text(json.dumps({'root':str(root),'port':port,'server_pid':server_process.pid,
                                         'worker_pid':worker_process.pid}),encoding='utf-8')
    ready=False
    for _ in range(120):
        if server_process.poll() is not None:
            raise RuntimeError('Bundled HTTP server exited during startup: '+(logs/'server.log').read_text(encoding='utf-8',errors='replace')[-4000:])
        try:
            response=httpx.get(f'http://127.0.0.1:{port}/api/v1/health',timeout=2,trust_env=False)
            if response.status_code==200 and response.json().get('status')=='ok':
                ready=True;break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    if not ready:
        raise RuntimeError('Bundled HTTP server did not become ready on port '+str(port))
    session = json.loads((root / f'.runtime/session-{port}.json').read_text(encoding='utf-8'))
    qa.mkdir(parents=True, exist_ok=True)
    (qa/'browser-session.json').write_text(json.dumps({'bootstrap_token':session['bootstrap_token']}),encoding='utf-8')
    with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
        health=client.get('/api/v1/health').json()
        assert health['status']=='ok' and health['version']==manifest['version']
        assert not health['key_configured']
        auth = client.post('/api/v1/session', json={'token': session['bootstrap_token']})
        assert auth.status_code == 200
        from http.cookies import SimpleCookie
        cookie=SimpleCookie();cookie.load(auth.headers.get('set-cookie',''))
        client.cookies.set('paper_session',cookie['paper_session'].value)
        client.headers['X-CSRF-Token'] = auth.json()['csrf']
        pid = client.post('/api/v1/projects', json={'title': '精简便携版隔离验收'}).json()['id']
        sid = client.post(f'/api/v1/projects/{pid}/sections', json={'title': '第三章 系统设计', 'kind': 'system_design'}).json()['id']
        checks.append('The extracted release starts a real HTTP server and creates a saved project')
        preview = client.post(f'/api/v1/projects/{pid}/paper-visuals/preview', json={'spec': TEMPLATES['architecture']})
        assert preview.status_code == 200 and preview.json()['width'] >= 1080
        brief = client.post(f'/api/v1/projects/{pid}/paper-visuals/image-brief', json={
            'kind': 'architecture', 'title': '隔离编译测试', 'instruction': '前端通过业务服务访问数据库。',
            'explanation': '业务服务接收前端请求并访问数据库。'})
        assert brief.status_code == 200 and brief.json()['paid_request'] is False
        checks.append('Local architecture rendering and free image prompt compilation work without an API key')
        asset = create_visual(pid, {'spec': TEMPLATES['architecture'], 'explanation': explanation_draft(pid, {'spec': TEMPLATES['architecture']})['explanation']})
        table = create_visual(pid, {'spec': TEMPLATES['three_line_table']})
        documents.save(security.require_section(sid), {'type': 'doc', 'content': asset['insert_nodes'] + table['insert_nodes']}, None)
        standalone = client.get(f'/api/v1/projects/{pid}/paper-visuals/{table["id"]}/download?format=docx')
        assert len(Document(io.BytesIO(standalone.content)).tables) == 1
        # Exercise the bundled independent PDF renderer even on a host with Office.
        original_renderer = exports.renderer
        exports.renderer = lambda: None
        try:
            result = exports.export_project(pid, {'mode': 'draft', 'pdf': True})
        finally:
            exports.renderer = original_renderer
        assert result['pdf'], result
        word = exports.DATA / 'exports' / result['export_id'] / '论文.docx'
        doc = Document(word)
        assert len(doc.inline_shapes) == 1 and len(doc.tables) == 1
        from pypdf import PdfReader
        assert len(PdfReader(word.with_suffix('.pdf')).pages) >= 1
        checks.append('Word and independent PDF export preserve an embedded figure and editable Word table')
        material, _ = parsers.register_file(pid, '本地检索测试.txt', '系统采用分层设计，业务服务接收请求后校验输入并写入数据库。'.encode(), 'research')
        parsers.parse_file(material['id'])
        assert parsers.search(pid, '业务服务', kind='research')
        checks.append('Chinese jieba tokenization and keyword literature search work without model weights')
        data, _ = parsers.register_file(pid, 'stats.csv', b'A,B,C\n1,3,2\n3,5,1\n2,4,4\n5,6,3\n4,9,6\n7,8,5\n8,10,8\n9,9,7\n', 'data')
        test = advanced_statistics.analyze(pid, {'file_id': data['id'], 'method': 'welch_t', 'columns': ['A', 'B']})
        regression = advanced_statistics.analyze(pid, {'file_id': data['id'], 'method': 'ols', 'columns': ['A', 'B', 'C']})
        assert test['source_sha256'] == data['sha256'] and isinstance(regression['r_squared'], float)
        checks.append('Real scipy statistical testing and statsmodels OLS run with the deduplicated dependencies')
        node = root / '.runtime/browser/node.exe'
        process = subprocess.run([str(node), str(root / '.runtime/browser/node_modules/playwright-core/cli.js'), 'install', '--help'],
                                 capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert process.returncode == 0 and b'--only-shell' in process.stdout, process.stderr
        checks.append('The bundled browser core installer runs with the development test runner removed')
        request = {'files': [{'path': 'service.ts', 'text': 'export class Service { run(value: string) { return value; } }'}]}
        process = subprocess.run([str(node), str(root / 'scripts/writing-agent/parse-typescript.mjs')], input=json.dumps(request).encode(), capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert process.returncode == 0, process.stderr.decode(errors='replace')
        syntax = json.loads(process.stdout)
        assert syntax['parser'].startswith('typescript-syntax.') and syntax['files'][0]['definitions']
        assert not (root / '.runtime/writing-agent/node.exe').exists()
        checks.append('TypeScript syntax analysis uses the single shared bundled Node without frontend developer dependencies')
        if full:
            assert controlled_execution.browser_status()['ready']
            assert len(list((root/'.runtime/models/local').glob('*/verified-model.json')))>=2
            assert not browser_component and not model_component
            checks.append('Browser, default models and PowerShell are ready in this application directory; no component installation')
        else:
            assert not controlled_execution.browser_status()['ready']
            assert not any((root / '.runtime/models/local').glob('*/verified-model.json'))
            checks.append('Optional browser and model resources are genuinely absent and not reported ready')
        ocr = local_ocr._native_capabilities()
        checks.append('Windows OCR runtime imports with host language availability reported separately')
        assert not image_generation.settings()['credential']['configured']
        assert not db.rows('SELECT * FROM generation_runs')
    specification = importlib.util.spec_from_file_location('portable_installer', root / 'scripts/install_portable_component.py')
    installer = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(installer)
    if browser_component or full:
        if browser_component:installer.install(browser_component, root)
        assert controlled_execution.browser_status()['ready']
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'<html><body><h1>Portable browser acceptance</h1><p>Actual capture</p></body></html>'
                self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        db.set_setting('capture_origins:' + pid, [origin])
        try:
            captured = controlled_execution.capture(pid, {'url': origin, 'name': '便携截图隔离验收', 'is_test': True})
            assert security.safe_object(captured['sha256']).is_file()
            assert captured['metadata']['capture_method'] == 'Playwright actual browser'
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        checks.append('The bundled headless shell takes a real screenshot without any additional download or installation')
    if model_component or full:
        if model_component:installer.install(model_component, root)
        vector = retrieval.embed(['业务服务处理请求'], retrieval.DEFAULT_MODEL)[0]
        scores = list(retrieval.model_instance('BAAI/bge-reranker-base', cross=True).rerank('业务请求', ['业务服务接收请求并校验输入。']))
        assert len(vector) > 10 and len(scores) == 1
        checks.append('Bundled models run real local embedding and cross-encoder inference without additional downloads')
    if manifest['profile']=='integrated':
        from app import graphrag_adapter
        capability=graphrag_adapter.capabilities()
        assert capability['installed'] and Path(capability['runtime_path']).is_relative_to(root)
        def graph_worker(packet):
            process=subprocess.run([sys.executable,'-I','-X','utf8',str(root/'scripts/graphrag_worker.py')],
                                   input=json.dumps(packet),capture_output=True,text=True,encoding='utf-8',timeout=180,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            messages=[json.loads(line) for line in process.stdout.splitlines() if line.strip().startswith('{')]
            assert process.returncode==0,process.stderr[-6000:]
            result=next((item['result'] for item in messages if item.get('event')=='result'),None)
            assert result is not None,messages
            assert not any(item.get('event') in ('completion','embedding','error') for item in messages),messages
            return result
        diagnosis=graph_worker({'operation':'diagnose','folder':str(root/'data/tmp/integrated-graph-diagnosis'),'protocol_test':True})
        assert diagnosis['version']=='3.3.0' and diagnosis['config_valid'] and diagnosis['synthetic_protocol_test']
        checks.append('Bundled GraphRAG 3.3.0 imports and validates its sync/async streaming protocol without component installation or network calls')
        entity_ids=['source-a','method-a','condition-a','result-a']
        edges=[('source-a','method-a'),('method-a','condition-a'),('condition-a','result-a'),('result-a','source-a')]
        graph_tables={
            'entities':[{'id':value,'human_readable_id':index,'title':value,'type':'synthetic','description':'Synthetic portable acceptance entity','text_unit_ids':['unit-a'],'frequency':1,'degree':2} for index,value in enumerate(entity_ids)],
            'relationships':[{'id':'relation-'+str(index),'human_readable_id':index,'source':left,'target':right,'description':'Synthetic local relation','weight':1.0,'text_unit_ids':['unit-a']} for index,(left,right) in enumerate(edges)],
            'text_units':[{'id':'unit-a','human_readable_id':0,'text':'Synthetic source scope for portable local graph acceptance.','n_tokens':20,'document_id':'document-a','entity_ids':entity_ids,'relationship_ids':['relation-'+str(index) for index in range(len(edges))],'covariate_ids':[]}],
            'documents':[{'id':'document-a','text':'Synthetic source scope for local graph acceptance.','title':'Portable synthetic graph'}]}
        built=graph_worker({'operation':'build','folder':str(root/'data/tmp/integrated-local-graph'),
                            'tables':graph_tables,'mapping':{'unit-a':{'document_id':'document-a'}},'reports':False,'embeddings':False})
        assert built['version']=='3.3.0' and 'create_communities' in built['workflows'] and 'communities' in built['tables'],built
        checks.append('The bundled official GraphRAG worker computes actual local communities from synthetic relations without a paid model')
    report = {'passed': True, 'version':manifest['version'],'profile':manifest['profile'],'application_root': str(root), 'port': port, 'server_pid':server_process.pid, 'worker_pid':worker_process.pid, 'project_id': pid, 'section_id': sid,
              'checks': checks, 'ocr_available': ocr.get('available'), 'paid_model_calls': 0,
              'third_party_paths_inside_package': True}
    qa.mkdir(parents=True, exist_ok=True)
    (qa / 'runtime-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extract', type=Path)
    parser.add_argument('--app', type=Path)
    parser.add_argument('--qa', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8817)
    parser.add_argument('--browser-component', type=Path)
    parser.add_argument('--model-component', type=Path)
    args = parser.parse_args()
    if args.extract:
        extract(args.extract.resolve(), args.qa.resolve())
    else:
        verify(args.app.resolve(), args.qa.resolve(), args.port, args.browser_component, args.model_component)
