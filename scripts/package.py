"""Build one integrated application ZIP, including GraphRAG and public resources."""
from pathlib import Path, PurePosixPath
from collections import defaultdict
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / 'frontend/package.json').read_text(encoding='utf-8'))['version']
PROFILES = {'integrated': '完整整合便携版', 'portable': '精简便携版', 'full': '完整便携版', 'offline': '完整离线版',
            'browser': '截图浏览器组件', 'models': '本地检索模型组件', 'graphrag': '知识图谱计算组件'}
MAX_PACKAGE_BYTES = 500_000_000
OUTPUT = ROOT.parent / f'砚台论文工作台_{VERSION}_{PROFILES["integrated"]}.zip'
EXCLUDED = {'__pycache__', '.pytest_cache', 'node_modules', 'tests', 'test', 'browser-data', '.git'}
SUFFIXES = {'.pyc', '.pyo', '.log', '.part', '.tmp'}
DEV_DISTRIBUTIONS = {'pytest', 'pluggy', 'iniconfig'}
TYPESCRIPT_FILES = {'package.json', 'LICENSE.txt', 'ThirdPartyNoticeText.txt', 'lib/typescript.js'}
PLAYWRIGHT_API_FILES = {'index.js', 'index.mjs', 'package.json', 'LICENSE', 'NOTICE', 'ThirdPartyNotices.txt'}
RUNTIME_ASSETS = {'frontend/src/collaboration.html': 'frontend/collaboration.html'}
SCRIPTS = ('scripts/capture-system.cjs', 'scripts/office-render.ps1', 'scripts/prepare_browser.py',
           'scripts/writing-agent/parse-typescript.mjs', 'scripts/install_portable_component.py', 'scripts/portable-start.ps1',
           'scripts/graphrag_worker.py', 'scripts/initialize_graph_wiki.py','scripts/knowledge_transfer_worker.py','scripts/knowledge_load_acceptance.py')
GUIDES = ('README.md', 'docs/便携版体积与可选组件.md', 'docs/单文件便携版使用说明.md', 'docs/从源码运行.md',
          'docs/论文图表与三线表.md', 'docs/GPT生图独立接口配置.md',
          'docs/论文配图规范与章节关联_2026-10-26.md', 'docs/生图提示词与段落语义对齐_2026-10-26.md',
          'docs/逐章写作与上下文衔接.md', 'docs/独立学术润写.md', 'docs/文献知识库存储与检索.md')
GUIDES=(*GUIDES,'docs/0.7.0图谱与Wiki知识库使用说明.md')
GUIDES=(*GUIDES,'docs/0.7.1知识库功能补齐与验收说明_2026-10-28.md')
GUIDES=(*GUIDES,'docs/0.7.2资料规则文献页面重构与整合_2026-10-28.md')
GUIDES=(*GUIDES,'docs/0.7.3完整整合便携版使用说明.md','docs/0.7.3首屏加载性能修复_2026-10-31.md','docs/六页文献工作台布局优化_2026-10-31.md','docs/文献页面功能核查与修复_2026-10-31.md')
GUIDES=(*GUIDES,'docs/API接口整合方案_2026-11-02.md','docs/API接口整合实施记录_2026-11-02.md','docs/DeepSeek接口整合与切换_2026-11-02.md')
GUIDES=(*GUIDES,'deployment/STUDIO.md','docs/PaperSpark功能补齐实施记录_2026-11-02.md')
GUIDES=(*GUIDES,'docs/PaperSpark全部功能复查与补齐_2026-11-02.md')
GUIDES=(*GUIDES,'docs/架构整合实施记录_2026-11-02.md','docs/新增功能架构集成评估与改进方案_2026-11-02.md')
GUIDES=(*GUIDES,'docs/功能完整性复查与补齐记录_2026-11-02.md','docs/功能完整性复核_2026-11-02/README.md')
GUIDES=(*GUIDES,'docs/核心写作与素材闭环实施记录_2026-11-03.md','docs/0.10.0完整整合便携版使用说明.md')
GUIDES=(*GUIDES,'docs/1.0.0完整整合便携版使用说明.md')


def _walk(folder, allow_modules=False):
    excluded = EXCLUDED - {'node_modules'} if allow_modules else EXCLUDED
    for path in sorted(folder.rglob('*')):
        relative = path.relative_to(folder)
        if path.is_file() and not any(p in excluded for p in relative.parts) and path.suffix not in SUFFIXES:
            if path.is_symlink():
                raise RuntimeError('Package input must be a regular file: ' + str(path))
            yield path


def _distribution(name):
    return re.sub(r'[-_.]+', '-', name.rsplit('-', 1)[0]).lower()


def _development_file(relative):
    top = relative.parts[0]
    return (top in DEV_DISTRIBUTIONS | {'_pytest'}
            or top.endswith('.dist-info') and _distribution(top[:-10]) in DEV_DISTRIBUTIONS
            or relative.suffix in ('.pyi', '.map')
            or top == 'fontTools' and relative.suffix in ('.c', '.h'))


def archive_name(path, root=ROOT):
    relative = path.relative_to(root).as_posix()
    return RUNTIME_ASSETS.get(relative, relative)


def _overridden_base_entries(root):
    """runner.py loads advanced-deps first. Keep that complete package/version."""
    base, advanced = root / '.runtime/python-deps', root / '.runtime/advanced-deps'
    if not advanced.is_dir():
        return set()
    names = set()
    for item in advanced.iterdir():
        if item.is_file() or (item / '__init__.py').is_file() or item.name.endswith('.libs'):
            names.add(item.name)
    distributions = {_distribution(p.name[:-10]) for p in advanced.glob('*.dist-info')}
    names.update(p.name for p in base.glob('*.dist-info') if _distribution(p.name[:-10]) in distributions)
    return names


def _browser_files(root):
    metadata = root / '.runtime/browser/node_modules/playwright-core/browsers.json'
    if not metadata.is_file():
        raise RuntimeError('Prepare the public Playwright runtime before packaging the browser component')
    entries = json.loads(metadata.read_text(encoding='utf-8'))['browsers']
    item = next(e for e in entries if e['name'] == 'chromium-headless-shell')
    if not re.fullmatch(r'\d+', item['revision']):
        raise RuntimeError('Invalid Playwright browser revision')
    folder = root / '.runtime/browsers' / ('chromium_headless_shell-' + item['revision'])
    if not folder.is_dir():
        raise RuntimeError('Matching Chromium headless shell is missing: ' + str(folder))
    yield from _walk(folder)


def _model_files(root):
    manifests = sorted((root / '.runtime/models/local').glob('*/verified-model.json'))
    if not manifests:
        raise RuntimeError('No verified local model is installed; download/verify models before making the offline component')
    for manifest in manifests:
        content = json.loads(manifest.read_text(encoding='utf-8'))
        files = content.get('files')
        if not isinstance(files, dict) or not files:
            raise RuntimeError('Invalid verified model manifest')
        yield manifest
        for relative, expected in files.items():
            name = PurePosixPath(relative)
            if name.is_absolute() or any(part in ('..', '.') or ':' in part for part in name.parts) or '\\' in relative:
                raise RuntimeError('Invalid model file path: ' + relative)
            file = manifest.parent.joinpath(*name.parts)
            if not file.is_file() or file.is_symlink() or file.resolve().parent != file.parent.resolve():
                raise RuntimeError('Missing or redirected model file: ' + relative)
            with file.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                    raise RuntimeError('Model failed its existing SHA256 manifest: ' + relative)
            yield file


def selected_files(profile='full', root=None):
    root = Path(root or ROOT)
    if profile not in PROFILES:
        raise ValueError('Unknown package profile')
    if profile in ('browser', 'models','graphrag'):
        yield from (_browser_files(root) if profile == 'browser' else _model_files(root) if profile=='models' else _walk(root/'.runtime/graphrag-deps'))
        return
    for name in ('backend', 'frontend/dist', 'prompts', '.runtime/python', '.runtime/python-deps',
                 '.runtime/advanced-deps', '.runtime/browser', '.runtime/writing-agent/typescript'):
        overridden = _overridden_base_entries(root) if name == '.runtime/python-deps' else set()
        for path in _walk(root / name, allow_modules=name == '.runtime/browser'):
            relative = path.relative_to(root / name)
            if relative.parts[0] in overridden:
                continue
            if name in ('.runtime/python-deps', '.runtime/advanced-deps') and relative.parts[0] in ('bin', 'Scripts', 'share'):
                continue
            if name in ('.runtime/python-deps', '.runtime/advanced-deps') and _development_file(relative):
                continue
            if name == '.runtime/writing-agent/typescript' and relative.as_posix() not in TYPESCRIPT_FILES:
                continue
            if name == '.runtime/browser' and path.suffix in ('.ts', '.map'):
                continue
            if (name == '.runtime/browser' and relative.parts[:2] == ('node_modules', 'playwright')
                    and relative.relative_to('node_modules/playwright').as_posix() not in PLAYWRIGHT_API_FILES):
                continue
            # Paddle LAC is not used by the application's jieba.cut tokenizer.
            if name == '.runtime/python-deps' and relative.parts[:2] == ('jieba', 'lac_small'):
                continue
            yield path
    for name in ('runner.py', '启动论文工作台.ps1', '停止论文工作台.ps1', '安装便携组件.ps1',
                 'scripts/yantai-data.mjs','scripts/yantai_data_offline.py','scripts/yantai_query_projection.py','deployment/execution.Dockerfile',
                 '准备截图浏览器.ps1', '准备隔离执行环境.ps1', 'frontend/package.json',
                 'requirements.lock.txt', 'requirements-advanced.lock.txt','requirements-graphrag.lock.txt', *SCRIPTS, *GUIDES, *RUNTIME_ASSETS):
        path = root / name
        if path.is_file():
            yield path
    if profile in ('full', 'offline', 'integrated'):
        yield from _walk(root / '.runtime/powershell')
        for name in ('启动工作台.cmd', '停止工作台.cmd', '准备便携资源.cmd'):
            if (root / name).is_file():yield root / name
    if profile in ('offline', 'integrated'):
        yield from _browser_files(root)
        yield from _model_files(root)
    if profile == 'integrated':
        yield from _walk(root / '.runtime/graphrag-deps')


def _validate_version(root):
    version = json.loads((root / 'frontend/package.json').read_text(encoding='utf-8'))['version']
    backend = re.search(r"APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", (root / 'backend/app/config.py').read_text(encoding='utf-8'))
    lock = json.loads((root / 'frontend/package-lock.json').read_text(encoding='utf-8'))
    if not backend or {version, backend[1], lock['version'], lock['packages']['']['version']} != {version}:
        raise RuntimeError('Frontend, backend and lock file versions do not match')
    return version


def _private(relative):
    path = PurePosixPath(relative)
    return (relative.startswith(('data/', '.experimental/', 'private/'))
            or path.suffix in ('.dpapi', '.sqlite', '.sqlite3', '.db')
            or path.name.endswith(('.sqlite3-wal', '.sqlite3-shm', '.db-wal', '.db-shm'))
            or path.suffix == '.json' and path.name.startswith(('session-', 'processes-'))
            or path.name == 'session.json'
            or path.name.startswith('.env') and path.name != '.env.example')


def source_revision(root):
    """Record the actual checkout identity without requiring Git on end users."""
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('Not a Git checkout')
        return result.stdout.strip()
    try:
        if Path(git('rev-parse', '--show-toplevel')).resolve() != Path(root).resolve():
            return {'commit': None, 'dirty': None}
        return {'commit': git('rev-parse', 'HEAD'),
                'dirty': bool(git('status', '--porcelain', '--untracked-files=all'))}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'commit': None, 'dirty': None}


def plan(profile='full', root=None):
    root = Path(root or ROOT)
    paths = sorted(set(selected_files(profile, root)), key=lambda p: archive_name(p, root))
    if profile in ('portable', 'full', 'offline', 'integrated'):
        required = ('runner.py', 'frontend/dist/index.html', '.runtime/python/python.exe',
                    '.runtime/browser/node.exe', '.runtime/browser/node_modules/playwright/package.json',
                    '.runtime/browser/node_modules/playwright/index.js', '.runtime/browser/node_modules/playwright-core/cli.js',
                    '.runtime/writing-agent/typescript/lib/typescript.js', 'scripts/install_portable_component.py',
                    '启动论文工作台.ps1', '安装便携组件.ps1', 'frontend/collaboration.html')
        names = {archive_name(p, root) for p in paths}
        if profile in ('full', 'offline', 'integrated'):
            required += ('.runtime/powershell/pwsh.exe', '.runtime/powershell/LICENSE.txt',
                         '.runtime/powershell/ThirdPartyNotices.txt', '启动工作台.cmd', '停止工作台.cmd',
                         '准备便携资源.cmd', 'scripts/portable-start.ps1', 'backend/app/portable_resources.py')
        if profile == 'integrated':
            required += ('.runtime/graphrag-deps/graphrag-3.3.0.dist-info/METADATA',
                         '.runtime/graphrag-deps/graphrag/api/__init__.py',
                         'scripts/graphrag_worker.py',
                         'backend/app/knowledge_api.py', 'backend/app/knowledge_wiki.py')
        missing = set(required) - names
        if missing:
            raise RuntimeError('Portable runtime is incomplete: ' + ', '.join(sorted(missing)))
    return paths


def build(profile='full', root=None, output=None, audit=False, max_bytes=None):
    root = Path(root or ROOT).resolve()
    version = _validate_version(root)
    paths = plan(profile, root)
    release_source = source_revision(root)
    build_identity = None
    if profile not in ('browser', 'models', 'graphrag'):
        # The identity describes this exact checkout and built frontend, even
        # when packaging a deliberately dirty development build. It never
        # reads user data or trusts a stale previous build-info.json.
        sys.path.insert(0, str(ROOT / 'backend'))
        from app.runtime_identity import build_manifest
        build_identity = build_manifest(root, include_frontend=True)
    groups = defaultdict(int)
    for path in paths:
        relative = archive_name(path, root)
        if _private(relative):
            raise RuntimeError('Private data was selected for packaging: ' + relative)
        group = '/'.join(relative.split('/')[:2]) if relative.startswith(('.runtime/', 'frontend/')) else relative.split('/')[0]
        groups[group] += path.stat().st_size
    limit = max_bytes
    if profile == 'full':
        limit = min(MAX_PACKAGE_BYTES, max_bytes) if max_bytes is not None else MAX_PACKAGE_BYTES
    report = {'version': version, 'profile': profile, 'files': len(paths), 'size_limit_bytes': limit,
              'source_commit': release_source['commit'], 'source_dirty': release_source['dirty'],
              'uncompressed_bytes': sum(groups.values()), 'groups_raw_bytes': dict(groups)}
    if build_identity:
        report.update({key: build_identity.get(key) for key in
                       ('build_id', 'source_fingerprint', 'api_contract_hash', 'frontend_fingerprint')})
    if audit:
        return report
    output = Path(output or root.parent / f'砚台论文工作台_{version}_{PROFILES[profile]}.zip').resolve()
    if output.suffix.lower() != '.zip' or not output.parent.is_dir():
        raise RuntimeError('The ZIP output parent directory must already exist')
    if output in {p.resolve() for p in paths}:
        raise RuntimeError('Output cannot replace a package input')
    partial = output.with_suffix('.partial.zip')
    hashes = {}
    compressed_groups = defaultdict(int)
    last_notice = time.monotonic()
    with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for i, path in enumerate(paths, 1):
            relative = archive_name(path, root)
            with path.open('rb') as source:
                hashes[relative] = hashlib.file_digest(source, 'sha256').hexdigest()
            archive.write(path, relative)
            group = '/'.join(relative.split('/')[:2]) if relative.startswith(('.runtime/', 'frontend/')) else relative.split('/')[0]
            compressed_groups[group] += archive.getinfo(relative).compress_size
            if time.monotonic() - last_notice > 20:
                print(json.dumps({'phase': 'compressing', 'profile': profile, 'done': i, 'total': len(paths)}), flush=True)
                last_notice = time.monotonic()
        if build_identity:
            identity_raw=json.dumps(build_identity,ensure_ascii=False,sort_keys=True,indent=2).encode('utf-8')
            archive.writestr('build-info.json',identity_raw)
            hashes['build-info.json']=hashlib.sha256(identity_raw).hexdigest()
        manifest = {'version': version, 'profile': profile, 'package_kind': 'component' if profile in ('browser', 'models','graphrag') else 'application',
                    'source_commit': release_source['commit'], 'source_dirty': release_source['dirty'],
                    'files': hashes, 'user_data_included': False, 'api_key_included': False,
                    'optional_components': ['browser', 'models','graphrag'] if profile == 'portable' else ['graphrag'] if profile in ('full','offline') else [],
                    'automatic_resource_download': profile == 'full',
                    'download_models': ['BAAI/bge-small-zh-v1.5', 'BAAI/bge-reranker-base'] if profile == 'full' else [],
                    'bundled_powershell': '7.6.5 Core' if profile in ('full', 'offline', 'integrated') else None,
                    'bundled_models_and_browser': profile in ('offline', 'integrated'),
                    'bundled_graphrag': profile == 'integrated',
                    'deduplication': 'advanced-deps precedence retained; one shared Node runtime',
                    'browser': 'headless shell only' if profile in ('offline', 'browser', 'integrated') else 'automatic download' if profile == 'full' else 'optional offline component'}
        if build_identity:
            manifest['build_identity']={key:build_identity.get(key) for key in
                ('schema','build_id','source_fingerprint','api_contract_hash','frontend_fingerprint','capabilities')}
        archive.writestr('便携包清单.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    if limit is not None and partial.stat().st_size >= limit:
        size = partial.stat().st_size
        partial.unlink()
        raise RuntimeError(f'Package size {size} bytes must be below {limit} bytes; existing release was retained')
    with zipfile.ZipFile(partial) as archive:
        for relative, expected in hashes.items():
            with archive.open(relative) as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                    raise RuntimeError('Package integrity verification failed: ' + relative)
    os.replace(partial, output)
    with output.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    return {**report, 'package': str(output), 'package_bytes': output.stat().st_size,
            'package_mb': round(output.stat().st_size / 1_000_000, 2),
            'package_mib': round(output.stat().st_size / 2**20, 2),
            'groups_compressed_bytes': dict(compressed_groups), 'sha256': digest,
            'manifest_hashes_verified': True, 'user_data_included': False, 'api_key_included': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=(*PROFILES, 'all'), default='integrated', help='Default: one ZIP containing the application, GraphRAG, models, browser and PowerShell. Legacy smaller profiles remain explicit options.')
    parser.add_argument('--audit', action='store_true', help='Report selected files and raw size without writing a ZIP')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--max-mb', type=float, help='Reject packages at or above this decimal MB limit (default full: 500 MB)')
    args = parser.parse_args()
    if args.profile == 'all' and args.output:
        parser.error('--output requires one profile')
    if args.max_mb is not None and (not 0 < args.max_mb < float('inf')):
        parser.error('--max-mb must be a positive finite number')
    for profile in ('portable', 'browser', 'models') if args.profile == 'all' else (args.profile,):
        print(json.dumps(build(profile, output=args.output, audit=args.audit,
                               max_bytes=int(args.max_mb * 1_000_000) if args.max_mb is not None else None), ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
