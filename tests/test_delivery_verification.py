"""Release provenance and selected-test execution use synthetic local inputs."""
import importlib.util
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

import runner

ROOT=Path(__file__).resolve().parents[1]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


manifest_writer=module('delivery_manifest_writer','scripts/build_runtime_manifest.py')
package=module('delivery_package','scripts/package.py')
native_smoke=module('delivery_native_smoke','scripts/smoke_native_release.py')


@pytest.mark.parametrize('selection',[
    ['tests/test_delivery_verification.py'],
    ['tests/test_delivery_verification.py::test_name','-k','scope'],
    ['--pyargs','a_test_package'],
])
def test_runner_explicit_selection_does_not_collect_the_whole_suite(monkeypatch,selection):
    calls=[]
    monkeypatch.setattr(sys,'argv',['runner.py','test',*selection])
    monkeypatch.setattr('pytest.main',lambda arguments:calls.append(arguments) or 0)
    with pytest.raises(SystemExit) as ended:
        runpy.run_path(str(ROOT/'runner.py'),run_name='__main__')
    assert ended.value.code==0
    assert calls==[['-q',*selection]]
    assert str(ROOT/'tests') not in calls[0]


def test_runner_defaults_to_full_suite_only_without_a_path():
    result=runner.test_arguments(['-k','scope_only','--junitxml=test-results/selected.xml'])
    assert result[0]==str(ROOT/'tests')
    assert result[-2:]==['scope_only','--junitxml=test-results/selected.xml']


def synthetic_root(tmp_path):
    files={'frontend/package.json':'{"version":"1.2.3"}',
           'frontend/package-lock.json':'{"version":"1.2.3","packages":{"":{"version":"1.2.3"}}}',
           'backend/app/config.py':'APP_VERSION="1.2.3"',
           'backend/app/public.py':'PUBLIC_CAPABILITY=True',
           'frontend/src/example.ts':'export const safe="public";',
           'frontend/dist/assets/application.js':'console.log("application");',
           'runner.py':'print("synthetic application")',
           'data/private.db':'private-token-and-paper',
           'private/key.json':'{"key":"synthetic-secret"}'}
    for name,text in files.items():
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(text,encoding='utf-8')
    return tmp_path


def test_frontend_and_final_manifests_are_public_and_content_bound(tmp_path):
    root=synthetic_root(tmp_path)
    frontend=manifest_writer.write_manifest(root,frontend=True)
    assert frontend['version']=='1.2.3'
    assert frontend['frontend_fingerprint'] is None
    assert json.loads((root/'frontend/public/build-info.json').read_text(encoding='utf-8'))==frontend
    final=manifest_writer.write_manifest(root)
    assert final['source_fingerprint']==frontend['source_fingerprint']
    assert final['frontend_fingerprint']
    assert final['build_id']!=frontend['build_id']
    raw=(root/'build-info.json').read_text(encoding='utf-8')
    assert 'synthetic-secret' not in raw and 'private-token-and-paper' not in raw
    assert str(root) not in raw
    source=root/'backend/app/public.py';source.write_text('PUBLIC_CAPABILITY=False',encoding='utf-8')
    changed=manifest_writer.write_manifest(root)
    assert changed['source_fingerprint']!=final['source_fingerprint']
    assert changed['build_id']!=final['build_id']


def test_identity_module_import_does_not_initialize_application_data(tmp_path):
    directory=tmp_path/'must-not-be-created'
    code=('import sys;sys.path.insert(0,'+repr(str(ROOT/'backend'))+');'
          'import app.runtime_identity;assert "app.config" not in sys.modules;'
          'assert "app.db" not in sys.modules')
    environment={**__import__('os').environ,'PAPER_DATA_DIR':str(directory)}
    subprocess.run([sys.executable,'-I','-X','utf8','-c',code],cwd=tmp_path,env=environment,
                   capture_output=True,text=True,check=True)
    assert not directory.exists()


def test_clean_release_rejects_uncommitted_dependency_without_writing(tmp_path,monkeypatch):
    root=synthetic_root(tmp_path)
    monkeypatch.setattr(manifest_writer.subprocess,'run',lambda *args,**kwargs:
        SimpleNamespace(stdout='?? backend/app/new_required_dependency.py\n'))
    with pytest.raises(ValueError,match='clean committed source'):
        manifest_writer.write_manifest(root,require_clean=True)
    assert not (root/'build-info.json').exists()


def test_manifest_writer_refuses_overwriting_configuration(tmp_path):
    root=synthetic_root(tmp_path);expected=(root/'frontend/package.json').read_bytes()
    with pytest.raises(ValueError,match='dedicated JSON'):
        manifest_writer.write_manifest(root,root/'frontend/package.json')
    assert (root/'frontend/package.json').read_bytes()==expected


def test_container_preserves_release_commit_only_for_identical_source_bytes(tmp_path):
    root=synthetic_root(tmp_path)
    original=manifest_writer.write_manifest(root,frontend=True)
    original['source_commit']='b'*40;original['source_dirty']=False
    (root/'frontend/public/build-info.json').write_text(json.dumps(original),encoding='utf-8')
    identity=manifest_writer.write_manifest(root,frontend=True,reuse_source=True)
    assert identity['source_commit']=='b'*40 and identity['source_dirty'] is False
    (root/'backend/app/public.py').write_text('changed source',encoding='utf-8')
    with pytest.raises(ValueError,match='does not match this build context'):
        manifest_writer.write_manifest(root,frontend=True,reuse_source=True)


def test_application_package_embeds_an_independently_hashed_runtime_identity(tmp_path,monkeypatch):
    root=synthetic_root(tmp_path);source=root/'runner.py'
    monkeypatch.setattr(package,'plan',lambda profile,root:[source])
    result=package.build('integrated',root=root,output=root/'test-output.zip')
    with zipfile.ZipFile(result['package']) as archive:
        identity=json.loads(archive.read('build-info.json'))
        manifest=json.loads(archive.read('便携包清单.json'))
        assert identity['build_id']==result['build_id']==manifest['build_identity']['build_id']
        assert manifest['files']['build-info.json']
        assert 'private/key.json' not in manifest['files']
    assert result['manifest_hashes_verified']


def test_version_consistency_check_does_not_edit_sources():
    before=[(ROOT/name).read_bytes() for name in ('frontend/package.json',
        'frontend/package-lock.json','backend/app/config.py','deployment/desktop/windows.nsi')]
    result=subprocess.run(['node.exe' if sys.platform=='win32' else 'node',
        str(ROOT/'scripts/release-version.mjs'),'--check'],cwd=ROOT,capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['consistent'] is True
    after=[(ROOT/name).read_bytes() for name in ('frontend/package.json',
        'frontend/package-lock.json','backend/app/config.py','deployment/desktop/windows.nsi')]
    assert before==after


def test_release_jobs_require_validated_commit_before_tagging_or_publishing():
    # Validate the workflow graph, not just one descriptive step title.
    import yaml
    value=yaml.safe_load((ROOT/'.github/workflows/release.yml').read_text(encoding='utf-8'))
    jobs=value['jobs']
    assert jobs['validate']['with']['commit']=='${{ needs.version.outputs.commit }}'
    assert set(jobs['prepare-release']['needs'])=={'version','validate'}
    for name in ('desktop','images'):
        assert {'version','validate','prepare-release'}<=set(jobs[name]['needs'])
        checkout=next(step for step in jobs[name]['steps'] if step.get('uses','').startswith('actions/checkout'))
        assert checkout['with']['ref']=='${{ needs.version.outputs.commit }}'
    assert {'validate','desktop','images'}<=set(jobs['publish']['needs'])
    for job in jobs.values():
        for step in job.get('steps',[]):
            if 'run' in step:
                assert step.get('shell',job.get('defaults',{}).get('run',{}).get('shell'))=='pwsh'
                assert step['run'].splitlines()[0].startswith('if ($PSVersionTable.PSVersion -le')


def test_container_identity_stage_covers_all_public_fingerprint_inputs():
    from fnmatch import fnmatch
    from app.runtime_identity import _source_files
    text=(ROOT/'deployment/workbench.Dockerfile').read_text(encoding='utf-8')
    identity=text.split('FROM node:',1)[0]
    sources=[part for line in identity.splitlines() if line.startswith('COPY ')
             for part in line.split()[1:-1]]
    missing=[]
    for _,relative in _source_files(ROOT):
        if not any(relative==source or relative.startswith(source.rstrip('/')+'/') or fnmatch(relative,source)
                   for source in sources):missing.append(relative)
    assert missing==[]
    assert 'COPY --from=identity /app/build-info.json ./build-info.json' in text
    assert 'PAPER_PACKAGED_BUILD=1' in text


@pytest.mark.parametrize('matching',[True,False])
def test_native_smoke_uses_empty_data_and_cleans_up_its_own_process(tmp_path,monkeypatch,matching):
    executable=tmp_path/'synthetic-native';executable.write_bytes(b'not executed')
    manifest=tmp_path/'build-info.json'
    manifest.write_text(json.dumps({'build_id':'expected','source_commit':'a'*40}),encoding='utf-8')
    events=[];spawned=[]
    class Process:
        def poll(self):return None
        def terminate(self):events.append('terminate')
        def send_signal(self,value):events.append('signal')
        def wait(self,timeout):events.append('wait');return 0
        def kill(self):events.append('kill')
    def start(command,**options):
        spawned.append((command,options));return Process()
    body={'status':'ok','version':'1.2.3','build_id':'expected' if matching else 'different'}
    def read(url,timeout):
        assert url.startswith('http://127.0.0.1:') and url.endswith('/api/v1/health')
        return io.BytesIO(json.dumps(body).encode())
    monkeypatch.setattr(native_smoke.subprocess,'Popen',start)
    monkeypatch.setattr(native_smoke,'build_opener',lambda *args:SimpleNamespace(open=read))
    if matching:
        assert native_smoke.smoke(executable,manifest)['status']=='passed'
    else:
        with pytest.raises(RuntimeError,match='different build identity'):
            native_smoke.smoke(executable,manifest)
    assert 'wait' in events and ('signal' in events or 'terminate' in events)
    assert 'kill' not in events
    assert spawned[0][0]==[str(executable)]
    environment=spawned[0][1]['env']
    assert environment['PAPER_NO_BROWSER']=='1' and environment['PAPER_TASK_WATCHDOG']=='0'
    assert environment['PAPER_DATA_DIR']!=str(ROOT/'data')
    assert not Path(environment['PAPER_DATA_DIR']).exists()
