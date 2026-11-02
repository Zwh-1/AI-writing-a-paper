"""First-launch downloads stay independent, resumable and integrity checked."""
import hashlib
import json
from pathlib import Path

from filelock import FileLock
import httpx

from app import model_install, portable_resources as resources


def test_failed_resource_does_not_block_others_and_retry_reuses_verified_models(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, 'ROOT', tmp_path)
    monkeypatch.setattr(model_install, 'ROOT', tmp_path)
    attempts = []
    installed = set()
    browser_attempts = []

    def browser():
        browser_attempts.append(True)
        if len(browser_attempts) == 1:
            raise OSError('offline')

    def download(model, endpoint='https://hf-mirror.com'):
        attempts.append(model)
        installed.add(model)

    monkeypatch.setattr(resources, '_browser', browser)
    monkeypatch.setattr(resources, '_model_verified', lambda model: model in installed)
    monkeypatch.setattr(model_install, 'download', download)
    assert resources.prepare()['state'] == 'failed'
    assert attempts == list(resources.DEFAULT_MODELS)
    assert resources.status()['errors'] == {'browser': 'offline'}
    assert resources.prepare()['state'] == 'ready'
    assert attempts == list(resources.DEFAULT_MODELS)
    assert not resources.status()['api_key_used'] and not resources.status()['paper_content_sent']


def test_existing_model_files_are_hashed_before_being_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(model_install, 'ROOT', tmp_path)
    folder = model_install.directory(resources.DEFAULT_MODELS[0])
    folder.mkdir(parents=True)
    (folder / 'model.onnx').write_bytes(b'corrupted')
    (folder / 'verified-model.json').write_text(json.dumps({'files': {'model.onnx': hashlib.sha256(b'good').hexdigest()}}))
    assert model_install.status(resources.DEFAULT_MODELS[0])['ready']
    assert not resources._model_verified(resources.DEFAULT_MODELS[0])
    (folder / 'model.onnx').write_bytes(b'good')
    assert resources._model_verified(resources.DEFAULT_MODELS[0])


def test_duplicate_preparation_does_not_overwrite_active_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, 'ROOT', tmp_path)
    monkeypatch.setattr(model_install, 'ROOT', tmp_path)
    (tmp_path / '.runtime').mkdir()
    resources._save({'state': 'preparing', 'resource': 'browser'})
    with FileLock(tmp_path / '.runtime/portable-resources.lock'):
        assert resources.prepare()['state'] == 'preparing'
        assert resources.start_background()['state'] == 'preparing'
    assert resources.status()['resource'] == 'browser'


def test_browser_installer_uses_the_bundled_node_and_downloads_headless_only(tmp_path, monkeypatch):
    from app import controlled_execution
    monkeypatch.setattr(resources, 'ROOT', tmp_path)
    ready = []
    monkeypatch.setattr(controlled_execution, 'browser_status', lambda: {'ready': bool(ready)})

    def run(command, **kwargs):
        assert Path(command[0]) == tmp_path / '.runtime/browser/node.exe'
        assert Path(command[1]) == tmp_path / '.runtime/browser/node_modules/playwright-core/cli.js'
        assert command[-3:] == ['chromium', '--only-shell', '--no-progress']
        assert kwargs['env']['PLAYWRIGHT_BROWSERS_PATH'] == str(tmp_path / '.runtime/browsers')
        assert kwargs['check']
        ready.append(True)

    monkeypatch.setattr(resources.subprocess, 'run', run)
    resources._browser()
    resources._browser()
    assert len(ready) == 1


def test_invalid_complete_partial_is_removed_and_successful_transfer_is_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(model_install, 'ROOT', tmp_path)
    model = resources.DEFAULT_MODELS[0]
    filename = model_install.SPECS[model][1]
    content = b'verified model'
    digest = hashlib.sha256(content).hexdigest()
    folder = model_install.directory(model)
    folder.mkdir(parents=True)
    partial = (folder / filename).with_suffix('.onnx.part')
    partial.write_bytes(b'x' * len(content))
    requests = []

    def handler(request):
        requests.append(request)
        if '/api/models/' in str(request.url):
            return httpx.Response(200, json={'sha': 'pinned-revision', 'siblings': [
                {'rfilename': filename, 'size': len(content), 'lfs': {'sha256': digest}}]})
        return httpx.Response(200, stream=httpx.ByteStream(content))

    original = httpx.Client
    monkeypatch.setattr(model_install.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    result = model_install.download(model)
    assert result['files'][filename] == digest
    assert (folder / filename).read_bytes() == content
    assert not partial.exists()
    assert resources._model_verified(model)
    assert len(requests) == 2


def test_interrupted_model_download_resumes_from_the_existing_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(model_install, 'ROOT', tmp_path)
    model = resources.DEFAULT_MODELS[0]
    filename = model_install.SPECS[model][1]
    content = b'verified model download'
    folder = model_install.directory(model)
    folder.mkdir(parents=True)
    partial = (folder / filename).with_suffix('.onnx.part')
    partial.write_bytes(content[:8])
    ranges = []

    def handler(request):
        if '/api/models/' in str(request.url):
            return httpx.Response(200, json={'sha': 'revision', 'siblings': [
                {'rfilename': filename, 'size': len(content), 'lfs': {'sha256': hashlib.sha256(content).hexdigest()}}]})
        ranges.append(request.headers['range'])
        return httpx.Response(206, headers={'content-range': f'bytes 8-{len(content)-1}/{len(content)}'},
                              stream=httpx.ByteStream(content[8:]))

    original = httpx.Client
    monkeypatch.setattr(model_install.httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handler)))
    model_install.download(model)
    assert ranges == [f'bytes=8-{len(content)-1}']
    assert (folder / filename).read_bytes() == content
