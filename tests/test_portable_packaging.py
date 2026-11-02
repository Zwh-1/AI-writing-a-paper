"""Release selection, dependency precedence and safe optional component installs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load('package')
installer = load('install_portable_component')


def put(root, name, content=b'file'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_explicit_small_profile_excludes_large_resources_and_retains_complete_app():
    names = {package.archive_name(p) for p in package.plan('portable')}
    assert not any(n.startswith(('.runtime/browsers/', '.runtime/models/', 'frontend/src/', 'tests/')) for n in names)
    assert '.runtime/writing-agent/node.exe' not in names
    assert '.runtime/browser/node.exe' in names
    assert '.runtime/writing-agent/typescript/lib/typescript.js' in names
    assert 'backend/app/image_generation.py' in names and 'backend/app/image_briefs.py' in names
    assert 'backend/app/advanced_statistics.py' in names and 'backend/app/retrieval.py' in names
    assert 'scripts/capture-system.cjs' in names and 'scripts/install_portable_component.py' in names
    assert 'scripts/writing-agent/live-acceptance.py' not in names
    assert not any('/jieba/lac_small/' in n for n in names)
    assert '.runtime/python-deps/jieba/__init__.py' in names
    assert not any(package._private(n) for n in names)


def test_dependency_dedup_keeps_the_actual_advanced_version_and_namespace(tmp_path):
    put(tmp_path, '.runtime/python-deps/demo/__init__.py', b'old')
    put(tmp_path, '.runtime/python-deps/demo/old_only.py')
    put(tmp_path, '.runtime/python-deps/demo-1.0.dist-info/METADATA')
    put(tmp_path, '.runtime/advanced-deps/demo/__init__.py', b'new')
    put(tmp_path, '.runtime/advanced-deps/demo/new_only.py')
    put(tmp_path, '.runtime/advanced-deps/demo-2.0.dist-info/METADATA')
    put(tmp_path, '.runtime/python-deps/shared/one.py')
    put(tmp_path, '.runtime/advanced-deps/shared/two.py')
    names = {p.relative_to(tmp_path).as_posix() for p in package.selected_files('portable', root=tmp_path)}
    assert not any(n.startswith(('.runtime/python-deps/demo/', '.runtime/python-deps/demo-1.0.')) for n in names)
    assert '.runtime/advanced-deps/demo/new_only.py' in names
    assert '.runtime/advanced-deps/demo-2.0.dist-info/METADATA' in names
    assert '.runtime/python-deps/shared/one.py' in names and '.runtime/advanced-deps/shared/two.py' in names


def test_default_is_one_archive_with_checked_shell_and_automatic_resource_downloads():
    names = {package.archive_name(p) for p in package.plan()}
    assert '.runtime/powershell/pwsh.exe' in names and '.runtime/powershell/LICENSE.txt' in names
    assert '启动工作台.cmd' in names and '停止工作台.cmd' in names
    assert 'scripts/portable-start.ps1' in names
    assert '准备便携资源.cmd' in names and 'backend/app/portable_resources.py' in names
    assert 'frontend/collaboration.html' in names and 'frontend/src/collaboration.html' not in names
    assert '.runtime/browser/node_modules/playwright-core/cli.js' in names
    assert not any(n.startswith('.runtime/browser/node_modules/playwright/lib/') for n in names)
    assert not any(n.startswith(('.runtime/browsers/', '.runtime/models/')) for n in names)
    assert not any('/typescript/lib/' in n and not n.endswith('/typescript.js') for n in names)
    assert not any(n.endswith(('.pyi', '.map')) or '/_pytest/' in n or '/pytest/' in n for n in names)
    assert '.runtime/writing-agent/typescript/LICENSE.txt' in names
    assert '.runtime/advanced-deps/scipy/__init__.py' in names
    assert '.runtime/advanced-deps/statsmodels/__init__.py' in names


def test_explicit_offline_profile_keeps_browser_models_and_checked_shell():
    names = {package.archive_name(p) for p in package.plan('offline')}
    assert '.runtime/powershell/pwsh.exe' in names
    assert any(n.startswith('.runtime/browsers/chromium_headless_shell-') for n in names)
    assert len([n for n in names if n.endswith('/verified-model.json')]) >= 2
    assert not any(n.startswith('frontend/src/') or '/chromium-1234/' in n for n in names)
    assert not any(n.endswith('/writing-agent/node.exe') for n in names)


def test_integrated_release_contains_graph_runtime_browser_and_models_together():
    names = {package.archive_name(p) for p in package.plan('integrated')}
    assert '.runtime/graphrag-deps/graphrag-3.3.0.dist-info/METADATA' in names
    assert '.runtime/graphrag-deps/graphrag/api/__init__.py' in names
    assert 'scripts/graphrag_worker.py' in names
    assert 'backend/app/knowledge_wiki.py' in names
    assert '.runtime/powershell/pwsh.exe' in names
    assert any(n.startswith('.runtime/browsers/chromium_headless_shell-') for n in names)
    assert len([n for n in names if n.endswith('/verified-model.json')]) >= 2
    assert not any(package._private(n) for n in names)
    assert not any(n.startswith(('data/', 'frontend/src/', 'tests/')) for n in names)


def test_integrated_manifest_requires_no_component_install_and_has_no_legacy_size_cap(tmp_path, monkeypatch):
    put(tmp_path, 'frontend/package.json', b'{"version":"0.7.3"}')
    put(tmp_path, 'frontend/package-lock.json', b'{"version":"0.7.3","packages":{"":{"version":"0.7.3"}}}')
    put(tmp_path, 'backend/app/config.py', b'APP_VERSION="0.7.3"')
    source = put(tmp_path, 'runner.py', b'print("integrated")')
    monkeypatch.setattr(package, 'plan', lambda profile, root: [source])
    result = package.build('integrated', root=tmp_path, output=tmp_path/'integrated.zip')
    assert result['size_limit_bytes'] is None
    with zipfile.ZipFile(result['package']) as archive:
        manifest = json.loads(archive.read('便携包清单.json'))
    assert manifest['package_kind']=='application'
    assert manifest['bundled_graphrag'] and manifest['bundled_models_and_browser']
    assert manifest['bundled_powershell']=='7.6.5 Core'
    assert manifest['optional_components']==[] and not manifest['automatic_resource_download']
    assert result['manifest_hashes_verified']


def test_size_budget_preserves_existing_release_and_reports_download_contract(tmp_path, monkeypatch):
    put(tmp_path, 'frontend/package.json', b'{"version":"0.6.1"}')
    put(tmp_path, 'frontend/package-lock.json', b'{"version":"0.6.1","packages":{"":{"version":"0.6.1"}}}')
    put(tmp_path, 'backend/app/config.py', b'APP_VERSION="0.6.1"')
    source = put(tmp_path, 'runner.py', b'print("standalone")')
    asset = put(tmp_path, 'frontend/src/collaboration.html', b'<html>collaboration</html>')
    monkeypatch.setattr(package, 'plan', lambda profile, root: [source, asset])
    output = put(tmp_path, 'release.zip', b'existing good release')
    with pytest.raises(RuntimeError, match='must be below'):
        package.build(root=tmp_path, output=output, max_bytes=1)
    assert output.read_bytes() == b'existing good release'
    assert not output.with_suffix('.partial.zip').exists()
    result = package.build(root=tmp_path, output=output, max_bytes=600_000_000)
    assert result['size_limit_bytes'] == 500_000_000
    assert result['package_bytes'] < 500_000_000
    assert result['manifest_hashes_verified']
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('便携包清单.json'))
        assert archive.read('frontend/collaboration.html') == asset.read_bytes()
        assert 'frontend/src/collaboration.html' not in archive.namelist()
    assert manifest['automatic_resource_download'] and manifest['optional_components']==['graphrag']
    assert manifest['download_models'] == ['BAAI/bge-small-zh-v1.5', 'BAAI/bge-reranker-base']


def test_browser_component_contains_only_the_matching_headless_shell(tmp_path):
    metadata = {'browsers': [{'name': 'chromium-headless-shell', 'revision': '1234'}]}
    put(tmp_path, '.runtime/browser/node_modules/playwright-core/browsers.json', json.dumps(metadata).encode())
    expected = '.runtime/browsers/chromium_headless_shell-1234/chrome-headless-shell-win64/chrome-headless-shell.exe'
    put(tmp_path, expected)
    put(tmp_path, '.runtime/browsers/chromium-1234/chrome-win64/chrome.dll')
    put(tmp_path, '.runtime/browsers/chromium_headless_shell-9999/old.exe')
    assert [p.relative_to(tmp_path).as_posix() for p in package.selected_files('browser', tmp_path)] == [expected]


def test_model_component_uses_existing_verified_files_and_rejects_modified_weights(tmp_path):
    relative = '.runtime/models/local/abcd/model.onnx'
    put(tmp_path, relative, b'weights')
    put(tmp_path, '.runtime/models/local/abcd/secret.dpapi')
    manifest = '.runtime/models/local/abcd/verified-model.json'
    put(tmp_path, manifest, json.dumps({'files': {'model.onnx': hashlib.sha256(b'weights').hexdigest()}}).encode())
    assert {p.relative_to(tmp_path).as_posix() for p in package.selected_files('models', tmp_path)} == {relative, manifest}
    put(tmp_path, relative, b'corrupted')
    with pytest.raises(RuntimeError, match='SHA256'):
        list(package.selected_files('models', tmp_path))


def setup_root(tmp_path):
    put(tmp_path, 'frontend/package.json', b'{"version":"0.6.1"}')
    return tmp_path


def component(tmp_path, files, profile='models', version='0.6.1', replacements=None):
    path = tmp_path / 'component.zip'
    manifest = {'package_kind': 'component', 'version': version, 'profile': profile,
                'files': {n: hashlib.sha256(content).hexdigest() for n, content in files.items()}}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(installer.MANIFEST, json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, (replacements or {}).get(name, content))
    return path


def test_offline_component_verify_only_install_and_idempotent_retry(tmp_path):
    root = setup_root(tmp_path / 'app')
    files = {'.runtime/models/local/abcd/model.onnx': b'weights', '.runtime/models/local/abcd/verified-model.json': b'{}'}
    archive = component(tmp_path, files)
    assert not installer.install(archive, root, verify_only=True)['installed']
    assert not (root / next(iter(files))).exists()
    result = installer.install(archive, root)
    assert result['verified_files'] == 2 and result['installed'] and not result['project_data_changed']
    assert installer.install(archive, root)['installed']
    assert not list((root / '.runtime').glob('component-install-*'))


@pytest.mark.parametrize('name', ['../escape', '.runtime/models/local/abcd/../../../../escape', '.runtime/models/local/C:/escape',
                                '.runtime/models/local/abcd\\escape', 'data/workbench.sqlite3', '.runtime/browser/node.exe'])
def test_component_path_cannot_write_outside_its_resource_family(tmp_path, name):
    root = setup_root(tmp_path / 'app')
    archive = component(tmp_path, {name: b'bad'})
    with pytest.raises(ValueError, match='unsupported path|outside its manifest'):
        installer.install(archive, root)
    assert not (root / 'data').exists()


def test_corrupt_component_is_fully_checked_before_any_public_file_is_added(tmp_path):
    root = setup_root(tmp_path / 'app')
    names = ['.runtime/models/local/abcd/first', '.runtime/models/local/abcd/second']
    archive = component(tmp_path, dict.fromkeys(names, b'good'), replacements={names[1]: b'bad'})
    with pytest.raises(ValueError, match='integrity'):
        installer.install(archive, root)
    assert not (root / names[0]).exists()


def test_modified_existing_component_is_not_silently_overwritten(tmp_path):
    root = setup_root(tmp_path / 'app')
    name = '.runtime/models/local/abcd/model.onnx'
    archive = component(tmp_path, {name: b'new'})
    put(root, name, b'old')
    with pytest.raises(ValueError, match='different content'):
        installer.install(archive, root)
    assert (root / name).read_bytes() == b'old'


def test_component_version_and_browser_revision_must_match(tmp_path):
    root = setup_root(tmp_path / 'app')
    archive = component(tmp_path, {'.runtime/models/local/abcd/model.onnx': b'weights'}, version='0.1.0')
    with pytest.raises(ValueError, match='versions'):
        installer.install(archive, root)
    put(root, '.runtime/browser/node_modules/playwright-core/browsers.json', b'{"browsers":[{"name":"chromium-headless-shell","revision":"1234"}]}')
    archive = component(tmp_path, {'.runtime/browsers/chromium_headless_shell-9999/browser.exe': b'exe'}, profile='browser')
    with pytest.raises(ValueError, match='revision'):
        installer.install(archive, root)


def test_private_filter_does_not_confuse_public_session_documentation():
    assert package._private('.runtime/session-8765.json')
    assert package._private('data/workbench.sqlite3')
    assert package._private('.runtime/python-deps/credential.dpapi')
    assert not package._private('.runtime/browser/node_modules/playwright-core/session-management.md')


def test_missing_browser_is_not_reported_ready_just_because_node_exists(tmp_path, monkeypatch):
    from app import controlled_execution as controlled
    monkeypatch.setattr(controlled, 'ROOT', tmp_path)
    monkeypatch.delenv('PLAYWRIGHT_BROWSERS_PATH', raising=False)
    module = tmp_path / '.runtime/browser/node_modules/playwright'
    put(tmp_path, '.runtime/browser/node.exe')
    put(tmp_path, '.runtime/browser/node_modules/playwright/package.json', b'{}')
    put(tmp_path, '.runtime/browser/node_modules/playwright-core/browsers.json', b'{"browsers":[{"name":"chromium-headless-shell","revision":"1234"}]}')
    monkeypatch.setattr(controlled, 'node_runtime', lambda: (str(tmp_path / '.runtime/browser/node.exe'), str(module)))
    assert not controlled.browser_status()['ready']
    put(tmp_path, '.runtime/browsers/chromium_headless_shell-1234/chrome-headless-shell-win64/chrome-headless-shell.exe')
    assert controlled.browser_status()['ready']
