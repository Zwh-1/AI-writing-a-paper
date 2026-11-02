"""Prepare public browser/model resources without blocking the application worker."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .config import ROOT

DEFAULT_MODELS = ('BAAI/bge-small-zh-v1.5', 'BAAI/bge-reranker-base')


def _save(value):
    folder = ROOT / '.runtime'
    folder.mkdir(exist_ok=True)
    value = {**value, 'updated_at': time.time(), 'api_key_used': False, 'paper_content_sent': False}
    temporary = folder / 'portable-resources.json.tmp'
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, folder / 'portable-resources.json')
    print(json.dumps(value, ensure_ascii=False), flush=True)
    return value


def status():
    try:
        return json.loads((ROOT / '.runtime/portable-resources.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'state': 'pending', 'message': '首次启动后自动准备截图浏览器和本地检索模型。'}


def start_background():
    from filelock import FileLock, Timeout
    folder = ROOT / '.runtime'
    folder.mkdir(exist_ok=True)
    try:
        with FileLock(folder / 'portable-resources.lock', timeout=0):
            pass
    except Timeout:
        return {'state': 'preparing', 'message': '资源正在后台准备。'}
    with (folder / 'portable-resources.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, '-I', '-X', 'utf8', str(ROOT / 'runner.py'), 'portable-resources'],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    return {'state': 'started', 'pid': process.pid,
            'message': '截图浏览器和本地模型在后台自动准备，进度见 .runtime/portable-resources.log。'}


def _model_verified(model):
    from . import model_install
    local = model_install.status(model)
    files = (local.get('manifest') or {}).get('files', {})
    if not local['ready'] or not files:
        return False
    folder = Path(local['path'])
    for name, expected in files.items():
        with (folder / name).open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                return False
    return True


def _browser():
    from .controlled_execution import browser_status
    if browser_status()['ready']:
        return
    environment = os.environ.copy()
    environment['PLAYWRIGHT_BROWSERS_PATH'] = str(ROOT / '.runtime/browsers')
    environment['PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT'] = '60000'
    node = ROOT / '.runtime/browser/node.exe'
    cli = ROOT / '.runtime/browser/node_modules/playwright-core/cli.js'
    subprocess.run([str(node), str(cli), 'install', 'chromium', '--only-shell', '--no-progress'],
                   cwd=ROOT, env=environment, check=True, timeout=1800, stdout=sys.stdout, stderr=sys.stderr,
                   **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    if not browser_status()['ready']:
        raise RuntimeError('截图浏览器下载后未通过可用性检查')


def prepare():
    from filelock import FileLock, Timeout
    from . import model_install
    folder = ROOT / '.runtime'
    folder.mkdir(exist_ok=True)
    try:
        with FileLock(folder / 'portable-resources.lock', timeout=0):
            errors = {}
            _save({'state': 'preparing', 'resource': 'browser', 'message': '正在准备截图浏览器；工作台其他功能可继续使用。'})
            try:
                _browser()
            except Exception as error:
                errors['browser'] = str(error)
            for model in DEFAULT_MODELS:
                _save({'state': 'preparing', 'resource': model, 'errors': errors,
                       'message': '正在准备本地检索模型：' + model})
                try:
                    if _model_verified(model):
                        continue
                    # The downloader verifies Git/LFS hashes and supports interrupted transfers.
                    # It shares a per-model lock with downloads requested from the existing UI.
                    try:
                        model_install.download(model)
                    except Exception:
                        model_install.download(model, endpoint='https://huggingface.co')
                    if not _model_verified(model):
                        raise RuntimeError('模型下载后未通过 SHA256 校验')
                except Exception as error:
                    errors[model] = str(error)
            return _save({'state': 'failed' if errors else 'ready', 'errors': errors,
                          'message': '部分资源准备失败；下次启动会重试，也可双击“准备便携资源.cmd”。' if errors
                                     else '截图浏览器、中文向量模型和重排模型已就绪，后续可离线使用。'})
    except Timeout:
        return {'state': 'preparing', 'message': '已有资源准备进程正在运行。'}
