"""Runtime identities, drain and metrics use isolated files and synthetic data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import importlib.util
import sys

import pytest

from test_workbench import client, project  # noqa: F401
from app import db, runtime_api, runtime_identity as identity, tasks


def source_tree(tmp_path):
    (tmp_path/'backend/app/writing_agent').mkdir(parents=True)
    (tmp_path/'frontend/src').mkdir(parents=True)
    (tmp_path/'frontend/dist').mkdir(parents=True)
    (tmp_path/'frontend/package.json').write_text('{"version":"1.2.3"}')
    (tmp_path/'backend/app/example.py').write_text('VALUE=1\n')
    (tmp_path/'backend/app/writing_agent/migrations.py').write_text('CURRENT=27\n')
    (tmp_path/'frontend/src/app.ts').write_text('export const a=1;')
    (tmp_path/'frontend/dist/index.html').write_text('<html>synthetic</html>')
    return tmp_path


def test_manifest_uses_public_allowlist_and_detects_content_without_private_data(tmp_path):
    root=source_tree(tmp_path)
    first=identity.build_manifest(root)
    (root/'data').mkdir();(root/'data/secret.txt').write_text('private manuscript and key')
    (root/'.runtime').mkdir();(root/'.runtime/session.json').write_text('bootstrap secret')
    (root/'build-info.json').write_text('generated')
    (root/'frontend/dist/build-info.json').write_text('generated')
    second=identity.build_manifest(root)
    assert first['source_fingerprint']==second['source_fingerprint']
    assert first['frontend_fingerprint']==second['frontend_fingerprint']
    assert first['version']=='1.2.3' and first['database_schema_version']==27
    assert 'private manuscript' not in json.dumps(second)
    (root/'backend/app/example.py').write_text('VALUE=2\n')
    assert identity.build_manifest(root)['source_fingerprint']!=first['source_fingerprint']


def test_loaded_process_identity_never_rehashes_modified_live_checkout(monkeypatch):
    state={'source_fingerprint':'first','build_id':'old','capabilities':[]}
    monkeypatch.setattr(identity,'_LOADED',{})
    monkeypatch.setattr(identity,'build_manifest',lambda *a,**k:deepcopy(state))
    monkeypatch.setattr(identity,'packaged_manifest',lambda *a,**k:None)
    before=identity.loaded('server')
    state.update(source_fingerprint='second',build_id='new')
    assert identity.loaded('server')==before
    assert identity.loaded('worker')['source_fingerprint']=='second'


def test_packaged_identity_requires_actual_runtime_file_hashes(tmp_path):
    root=source_tree(tmp_path)
    manifest=identity.build_manifest(root)
    path=root/'build-info.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
           ('build-info.json','backend/app/example.py','frontend/dist/index.html')}
    (root/'便携包清单.json').write_text(json.dumps({'files':files}),encoding='utf-8')
    assert identity.packaged_manifest(root)==manifest
    (root/'backend/app/example.py').write_text('edited source')
    assert identity.packaged_manifest(root) is None


def test_worker_heartbeat_reports_frozen_identity_and_cleans_up(client,monkeypatch):
    monkeypatch.setattr(identity,'loaded',lambda role:{'pid':123,'version':'test','source_fingerprint':'loaded','build_id':'frozen'})
    with identity.worker_heartbeat(interval=.05):
        rows=identity.workers()
        assert len(rows)==1 and rows[0]['source_fingerprint']=='loaded'
        assert rows[0]['status']=='healthy'
        future=datetime.now(timezone.utc)+timedelta(seconds=60)
        assert identity.workers(future)[0]['status']=='stale'
    assert identity.workers()==[]


def test_drain_is_explicit_strict_and_does_not_rewrite_active_tasks(client):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    assert identity.is_draining() is False
    with pytest.raises(Exception):runtime_api.drain({'enabled':'true'})
    assert identity.is_draining() is False
    result=runtime_api.drain({'enabled':True})
    assert result['enabled'] and result['queued_tasks']==1
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='queued'
    result=runtime_api.drain({'enabled':False})
    assert result['enabled'] is False


def test_readiness_does_not_confuse_http_liveness_with_worker_health(client,monkeypatch):
    frozen={'version':'test','build_id':'test','source_fingerprint':'a','loaded_at':'start','capabilities':[]}
    monkeypatch.setattr(identity,'loaded',lambda role='server':deepcopy(frozen))
    monkeypatch.setattr(identity,'build_manifest',lambda *a,**k:deepcopy(frozen))
    monkeypatch.setattr(identity,'packaged_manifest',lambda:None)
    monkeypatch.setattr(identity,'workers',lambda:[])
    state=identity.status()
    assert not state['readiness']['ready'] and 'worker_unavailable' in state['readiness']['issues']
    monkeypatch.setattr(identity,'workers',lambda:[{**frozen,'status':'healthy'}])
    assert identity.status()['readiness']['ready']
    runtime_api.drain({'enabled':True})
    assert not identity.status()['readiness']['ready']


def test_cache_metrics_keep_unknown_separate_from_known_zero(client):
    pid=project(client);task=tasks.enqueue(pid,'provider_probe',{})
    def insert(usage):
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
            (db.uid(),task['id'],pid,'completed','synthetic','openai',
             db.dumps({'writing_stage':'semantic_review','provider':{'base_url':'https://example.invalid','protocol':'openai','model':'synthetic'}}),
             db.dumps(usage),db.now()))
    insert({'prompt_tokens':100,'completion_tokens':7,'prompt_cache_hit_tokens':25,'prompt_cache_miss_tokens':75})
    insert({})
    db.audit(pid,'writing_response_cache_reused',{'stage':'semantic_review'})
    row=runtime_api.metrics(pid)['stages'][0]
    assert row['requests']==2 and row['cache_unknown_runs']==1 and row['cache_known_runs']==1
    assert row['cache_hit_tokens']==25 and row['cache_miss_tokens']==75 and row['weighted_hit_rate']==.25
    assert row['local_response_reuses']==1


def test_runtime_endpoints_require_session_and_csrf(client):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as raw:
        assert raw.get('/api/v1/runtime/status').status_code==401
    result=client.get('/api/v1/runtime/status')
    assert result.status_code==200
    assert result.json()['server']['loaded_at']
    response=client.put('/api/v1/runtime/drain',json={'enabled':False})
    assert response.status_code==200


@pytest.mark.parametrize('frozen',[False,True])
def test_native_launcher_keeps_worker_in_an_owned_supervised_process(tmp_path,monkeypatch,frozen):
    import uvicorn
    source=Path(__file__).resolve().parents[1]/'deployment/desktop/launcher.py'
    spec=importlib.util.spec_from_file_location('isolated_native_launcher',source)
    launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
    monkeypatch.setenv('APPDATA',str(tmp_path));monkeypatch.setenv('PAPER_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('PAPER_SESSION_DIR',str(tmp_path/'session'));monkeypatch.setenv('PAPER_NO_BROWSER','1')
    monkeypatch.setenv('PAPER_PORT','0');monkeypatch.setattr(db,'init',lambda:None)
    monkeypatch.setattr(sys,'argv',['Yantai']);monkeypatch.setattr(sys,'frozen',frozen,raising=False)
    if frozen:monkeypatch.setattr(sys,'_MEIPASS',str(source.parents[2]),raising=False)
    calls=[]
    class OwnedWorker:
        stdin=None
        ended=False
        def poll(self):return 0 if self.ended else None
        def terminate(self):self.ended=True;calls.append('terminate')
        def wait(self,timeout=None):calls.append('wait');return 0
    owned=OwnedWorker()
    def launch(command,**kwargs):
        calls.append((command,kwargs));return owned
    monkeypatch.setattr(launcher.subprocess,'Popen',launch)
    monkeypatch.setattr(uvicorn,'run',lambda *a,**k:calls.append('http-server'))
    launcher.main()
    command,options=calls[0]
    assert command[-1]=='worker' and command[0]==sys.executable
    assert options['env']['PAPER_WORKER_MONITOR_PARENT']=='1'
    assert calls[-3:]==['http-server','terminate','wait']


def test_material_and_process_identity_are_distinct_from_live_source(client,monkeypatch):
    frozen={'version':'x','source_fingerprint':'loaded','build_id':'loaded','capabilities':[]}
    monkeypatch.setattr(identity,'loaded',lambda role='server':deepcopy(frozen))
    monkeypatch.setattr(identity,'packaged_manifest',lambda:None)
    monkeypatch.setattr(identity,'build_manifest',lambda *a,**k:{**frozen,'source_fingerprint':'edited'})
    monkeypatch.setattr(identity,'workers',lambda:[{**frozen,'source_fingerprint':'other-worker','status':'healthy'}])
    result=identity.status()
    assert result['server']['source_fingerprint']=='loaded'
    assert result['source']['source_fingerprint']=='edited'
    assert set(result['readiness']['issues'])=={'source_changed_since_start','server_worker_build_mismatch'}
