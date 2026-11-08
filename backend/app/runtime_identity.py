"""Frozen process identities and read-only runtime evidence.

The loaded snapshot is captured once per process. Rehashing the checkout is a
separate observation and never changes the identity of already running code.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(sys._MEIPASS).resolve() if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]

SCHEMA = 'runtime.build.v1'
_LOADED = {}
_GUARD = threading.RLock()
CAPABILITIES = {
    'project_memory_v1':'backend/app/project_memory.py',
    'hierarchical_context_v1':'backend/app/writing_continuity.py',
    'progressive_skills_v1':'backend/app/skill_runtime.py',
    'vector_figure_delivery_v1':'backend/app/visual_delivery.py',
    'langchain_metered_gateway_v1': 'backend/app/ai_runtime/gateway.py',
    'langchain_scoped_tools_v1': 'backend/app/ai_runtime/tools.py',
    'langchain_source_retriever_v1': 'backend/app/ai_runtime/retrievers.py',
    'langgraph_chapter_v1': 'backend/app/ai_graphs/chapter.py',
    'task_obligations_v1': 'backend/app/ai_contracts/tasks.py',
    'fenced_node_receipts_v1': 'backend/app/ai_runtime/receipts.py',
    'runtime_diagnostics_v1': 'backend/app/runtime_api.py',
    'graceful_drain_v1': 'backend/app/runtime_identity.py',
    'cache_policy_v2': 'backend/app/ai_cache_policy.py',
    'prompt_layout_cache_v2': 'backend/app/prompt_layout.py',
    'official_api_manual_permission': 'backend/app/official_api_policy.py',
    'worker_supervision_v1': 'backend/app/worker_supervisor.py',
    'request_execution_attempts_v1': 'backend/app/request_execution.py',
    'actual_provider_identity_v1': 'backend/app/provider_identity.py',
    'fenced_task_transitions_v1': 'backend/app/task_transitions.py',
    'claim_window_verification_v2': 'backend/app/writing_quality_protocols.py',
    'restricted_revision_patch_v1': 'backend/app/writing_revision_patch.py',
    'read_only_receipt_lookup_v1': 'backend/app/request_receipts.py',
}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _source_files(root):
    patterns = ('backend/**/*.py', 'backend/**/*.sql', 'frontend/src/**/*.ts', 'frontend/src/**/*.tsx',
                'frontend/src/**/*.css', 'frontend/src/**/*.html', 'prompts/**/*.txt','skills/**/*.md','skills/**/*.json','skills/**/*.txt',
                'scripts/*.py', 'scripts/*.mjs', 'scripts/*.ps1',
                '.github/workflows/*.yml', 'deployment/**/*.py', 'deployment/**/*.spec',
                'deployment/**/*.nsi', 'deployment/*.Dockerfile', 'deployment/*.txt')
    paths = {path for pattern in patterns for path in root.glob(pattern) if path.is_file()}
    paths.update(root / name for name in ('runner.py', 'frontend/package.json',
        'frontend/package-lock.json', 'frontend/vite.config.ts', 'requirements.lock.txt')
        if (root / name).is_file())
    paths.update(path for pattern in ('requirements*.txt', '*.ps1', '*.cmd') for path in root.glob(pattern) if path.is_file())
    for path in sorted(paths, key=lambda value:value.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if any(part in ('private', 'data', '.runtime', '__pycache__', 'node_modules') for part in relative.parts):
            continue
        # A symlink outside the declared source root must never disclose data.
        if not path.resolve().is_relative_to(root.resolve()):continue
        yield path, relative.as_posix()


def build_manifest(root=ROOT, include_frontend=True):
    """Read public build inputs only; no imports, database or network writes."""
    root = Path(root).resolve()
    source = [(relative, hashlib.sha256(path.read_bytes()).hexdigest())
              for path, relative in _source_files(root)]
    frontend = []
    if include_frontend:
        for path in sorted((root / 'frontend/dist').rglob('*'),key=lambda value:value.relative_to(root).as_posix()):
            if path.is_file() and path.name != 'build-info.json' and path.resolve().is_relative_to(root):
                frontend.append((path.relative_to(root / 'frontend/dist').as_posix(),
                                 hashlib.sha256(path.read_bytes()).hexdigest()))
    version = 'unknown'
    try:version = json.loads((root / 'frontend/package.json').read_text(encoding='utf-8'))['version']
    except (OSError, ValueError, KeyError):
        import ast
        try:
            for node in ast.parse((root/'backend/app/config.py').read_text(encoding='utf-8')).body:
                if isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id=='APP_VERSION' for name in node.targets):
                    if isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):version=node.value.value
        except (OSError,SyntaxError):pass
    commit = dirty = None
    try:
        def git(*args):
            result = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                text=True, encoding='utf-8', errors='replace', timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:raise ValueError('Git identity unavailable')
            return result.stdout.rstrip('\r\n')
        if Path(git('rev-parse', '--show-toplevel')).resolve() == root:
            commit = git('rev-parse', 'HEAD')
            generated = {'build-info.json', 'frontend/public/build-info.json'}
            dirty = any(line[3:].replace('\\', '/') not in generated for line in
                        git('status', '--porcelain', '--untracked-files=all').splitlines())
    except (OSError, ValueError, subprocess.SubprocessError):pass
    contract = root / 'docs/api-contract-v2.json'
    value = {'schema': SCHEMA, 'version': version, 'source_commit': commit,
        'source_dirty': dirty, 'source_fingerprint': _digest(source),
        'source_files': len(source),
        'api_contract_hash': hashlib.sha256(contract.read_bytes()).hexdigest() if contract.is_file() else None,
        'frontend_fingerprint': _digest(frontend) if frontend else None,
        'capabilities': sorted(name for name, file in CAPABILITIES.items() if (root / file).is_file())}
    migration = root / 'backend/app/writing_agent/migrations.py'
    if migration.is_file():
        import ast
        for node in ast.parse(migration.read_text(encoding='utf-8')).body:
            if isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id == 'CURRENT' for name in node.targets):
                if isinstance(node.value, ast.Constant) and type(node.value.value) is int:
                    value['database_schema_version'] = node.value.value
    value['build_id'] = _digest({key: value[key] for key in
        ('schema', 'version', 'source_fingerprint', 'api_contract_hash', 'frontend_fingerprint')})[:20]
    value['generated_at'] = stamp()
    return value


def loaded(role='server'):
    with _GUARD:
        if role not in _LOADED:
            current = build_manifest()
            # Packaged source may be embedded in an archive. Use the immutable
            # build manifest captured by the release pipeline when available.
            packaged = packaged_manifest()
            if packaged is not None:current = packaged
            _LOADED[role] = {**current, 'role': role, 'pid': os.getpid(), 'loaded_at': stamp()}
        return deepcopy(_LOADED[role])


def packaged_manifest(root=ROOT):
    """Use package identity only when its shipped source still matches hashes."""
    root = Path(root).resolve()
    path = root / 'build-info.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema') != SCHEMA:return None
        if getattr(sys, 'frozen', False) or os.environ.get('PAPER_PACKAGED_BUILD') == '1':return value
        package = json.loads((root / '便携包清单.json').read_text(encoding='utf-8'))
        files = package['files']
        if files.get('build-info.json') != hashlib.sha256(path.read_bytes()).hexdigest():return None
        for name, expected in files.items():
            if not name.startswith(('backend/', 'prompts/', 'frontend/dist/')) and name != 'runner.py':continue
            candidate = (root / name).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():return None
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:return None
        return value
    except (OSError, ValueError, KeyError, TypeError):return None


def is_draining():
    from . import db
    return db.setting('runtime_drain', False) is True


def drain_status():
    from . import db
    counts = {row['status']: row['n'] for row in db.rows(
        "SELECT status,COUNT(*) n FROM tasks WHERE status IN ('queued','running') GROUP BY status")}
    unresolved = db.one("SELECT COUNT(*) n FROM generation_runs WHERE status IN ('pending','sent','outcome_unknown')")['n']
    active = counts.get('running', 0)
    return {'enabled': is_draining(), 'active_tasks': active, 'queued_tasks': counts.get('queued', 0),
            'unresolved_requests': unresolved, 'can_restart': active == 0 and unresolved == 0,
            'scope': '仅控制新任务与队列领取；在途请求和已保存检查点继续保留。'}


def heartbeat_directory():
    from . import db
    return Path(db.DB_PATH).resolve().parent / 'tmp' / 'runtime-workers'


@contextmanager
def worker_heartbeat(interval=10):
    """Publish the worker process snapshot atomically; clean up on normal exit."""
    identity = loaded('worker')
    folder = heartbeat_directory();folder.mkdir(parents=True, exist_ok=True)
    target = folder / (str(os.getpid()) + '.json')
    stop = threading.Event()
    def write():
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps({**identity, 'heartbeat_at': stamp()},
                                       ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, target)
    def beat():
        while not stop.wait(interval):
            try:write()
            except OSError:pass
    write()
    thread = threading.Thread(target=beat, name='worker-runtime-heartbeat', daemon=True)
    thread.start()
    try:yield identity
    finally:
        stop.set();thread.join(timeout=1)
        target.unlink(missing_ok=True)


def workers(now=None):
    current = now or datetime.now(timezone.utc)
    result = []
    for path in sorted(heartbeat_directory().glob('*.json')):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            when = datetime.fromisoformat(value['heartbeat_at'])
            age = (current - when.astimezone(timezone.utc)).total_seconds()
            if not 0 <= age < 86400:continue
            public = {key: value.get(key) for key in ('version', 'build_id', 'source_fingerprint',
                'source_commit', 'source_dirty', 'loaded_at', 'heartbeat_at', 'pid', 'capabilities')}
            result.append({**public, 'age_seconds': round(age, 1),
                           'status': 'healthy' if age <= 35 else 'stale'})
        except (OSError, ValueError, KeyError, TypeError):continue
    return result


def status():
    from . import db
    server = loaded('server');worker_rows = workers();issues = []
    source = packaged_manifest() or build_manifest(include_frontend=False)
    if source['source_fingerprint'] != server['source_fingerprint']:issues.append('source_changed_since_start')
    healthy = [value for value in worker_rows if value['status'] == 'healthy']
    if not healthy:issues.append('worker_unavailable')
    if any(row.get('source_fingerprint') != server['source_fingerprint'] for row in healthy):
        issues.append('server_worker_build_mismatch')
    database_ready = db.one('SELECT 1 AS ready')['ready'] == 1
    drain = drain_status()
    return {'server': server, 'source': source, 'workers': worker_rows, 'drain': drain,
        'readiness': {'ready': database_ready and bool(healthy) and not drain['enabled'] and not issues,
                      'database_ready': database_ready, 'worker_ready': bool(healthy), 'issues': issues},
        'checked_at': stamp()}
