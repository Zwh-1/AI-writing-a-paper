"""Verify a matching offline component before adding public runtime files."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = '便携包清单.json'


def _allowed(name, profile):
    path = PurePosixPath(name)
    if not name or '\\' in name or path.is_absolute() or path.as_posix() != name:
        return False
    if any(part in ('.', '..') or ':' in part for part in path.parts):
        return False
    prefix = '.runtime/models/local/' if profile == 'models' else '.runtime/graphrag-deps/' if profile=='graphrag' else '.runtime/browsers/'
    return name.startswith(prefix) and len(path.parts) > (4 if profile == 'models' else 2 if profile=='graphrag' else 3)


def _destination(root, name):
    target = root.joinpath(*PurePosixPath(name).parts)
    if not target.resolve().is_relative_to(root / '.runtime'):
        raise ValueError('Component destination was redirected outside the runtime directory')
    return target


def _compatibility(root, manifest, names):
    profile = manifest['profile']
    if profile == 'browser':
        metadata = root / '.runtime/browser/node_modules/playwright-core/browsers.json'
        if not metadata.is_file():
            raise ValueError('The application Playwright runtime is missing')
        browser = next(e for e in json.loads(metadata.read_text(encoding='utf-8'))['browsers'] if e['name'] == 'chromium-headless-shell')
        prefix = '.runtime/browsers/chromium_headless_shell-' + browser['revision'] + '/'
        if any(not name.startswith(prefix) for name in names):
            raise ValueError('Screenshot browser revision does not match this application')
    elif profile=='graphrag':
        if '.runtime/graphrag-deps/graphrag-3.3.0.dist-info/METADATA' not in names:raise ValueError('GraphRAG component version does not match the pinned adapter')


def install(package, root=None, verify_only=False):
    root = Path(root or ROOT).resolve()
    version = json.loads((root / 'frontend/package.json').read_text(encoding='utf-8'))['version']
    runtime = root / '.runtime'
    runtime.mkdir(exist_ok=True)
    if not runtime.resolve().is_relative_to(root):
        raise ValueError('Application runtime directory is redirected outside this application')
    # Stage only below the verified runtime. No project data or keys are touched.
    with tempfile.TemporaryDirectory(prefix='component-install-', dir=runtime) as temporary:
        stage = Path(temporary).resolve()
        if not stage.is_relative_to(runtime.resolve()):
            raise ValueError('Invalid component staging directory')
        with zipfile.ZipFile(package) as archive:
            entries = archive.infolist()
            if len(entries) > 30000 or sum(e.file_size for e in entries) > 2 * 1024**3:
                raise ValueError('Component archive exceeds the supported size')
            manifest = json.loads(archive.read(MANIFEST))
            profile = manifest.get('profile')
            if manifest.get('package_kind') != 'component' or profile not in ('browser', 'models','graphrag'):
                raise ValueError('Select a browser, model or GraphRAG component, not an application ZIP')
            if manifest.get('version') != version:
                raise ValueError('Component and application versions do not match')
            files = manifest.get('files')
            if not isinstance(files, dict) or not files:
                raise ValueError('Component manifest is empty')
            names = [e.filename for e in entries]
            if len(names) != len(set(n.casefold() for n in names)):
                raise ValueError('Component archive contains duplicate paths')
            if set(names) != {MANIFEST, *files}:
                raise ValueError('Component archive contains files outside its manifest')
            _compatibility(root, manifest, files)
            for entry in entries:
                name = entry.filename
                if name == MANIFEST:
                    continue
                if not _allowed(name, profile) or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError('Component contains an unsupported path: ' + name)
                expected = files[name]
                if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                    raise ValueError('Invalid component file hash')
                target = _destination(root, name)
                if target.exists():
                    if not target.is_file():
                        raise ValueError('Component path conflicts with an existing directory')
                    with target.open('rb') as source:
                        if hashlib.file_digest(source, 'sha256').hexdigest() != expected:
                            raise ValueError('An existing component file has different content; use a clean application folder: ' + name)
                pending = stage.joinpath(*PurePosixPath(name).parts)
                pending.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(entry) as source, pending.open('wb') as out:
                    for data in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(data)
                        out.write(data)
                if digest.hexdigest() != expected:
                    raise ValueError('Component integrity verification failed: ' + name)
        if not verify_only:
            for name in sorted(files, key=lambda n: (n.endswith('verified-model.json'), n)):
                target = _destination(root, name)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage.joinpath(*PurePosixPath(name).parts), target)
    return {'component': profile, 'version': version, 'verified_files': len(files), 'installed': not verify_only,
            'project_data_changed': False, 'api_key_used': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(install(args.package, verify_only=args.verify_only), ensure_ascii=False, indent=2))
