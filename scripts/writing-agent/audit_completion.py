"""Read-only completion audit. Never initializes app state or calls a model."""
import argparse
import hashlib
import json
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / 'docs/writing-agent'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def database_metadata(folder):
    path = folder / 'workbench.sqlite3'
    if not path.exists():
        return {'present': False}
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        queries = {
            'diagnostic_samples': 'SELECT status,COUNT(*) n,SUM(human_review_json IS NOT NULL) human_reviewed FROM diagnostic_items GROUP BY status',
            'recorded_profile_qualifications': 'SELECT role,qualification,COUNT(*) n FROM writing_profiles GROUP BY role,qualification',
            'sessions': 'SELECT operation,status,COUNT(*) n FROM writing_sessions GROUP BY operation,status',
            'analyses': 'SELECT state,COUNT(*) n FROM document_analyses GROUP BY state',
            'verification_runs': 'SELECT stage,status,method,COUNT(*) n FROM writing_verification_runs GROUP BY stage,status,method',
        }
        report = {
            'present': True,
            'connection_mode': 'read_only',
            'user_version': con.execute('PRAGMA user_version').fetchone()[0],
            'quick_check': [r[0] for r in con.execute('PRAGMA quick_check')],
            'foreign_key_violations': len(con.execute('PRAGMA foreign_key_check').fetchall()),
            **{name: [dict(row) for row in con.execute(query)] for name, query in queries.items()},
        }
        report['adoption_policies'] = []
        for row in con.execute('SELECT verification_manifest_blob FROM writing_adoptions'):
            payload = read_json(folder / 'objects' / row[0])
            report['adoption_policies'].append({'policy': payload.get('policy'), 'partial': payload.get('partial')})
        report['selected_snapshots'] = []
        for row in con.execute('SELECT s.* FROM repository_snapshots s JOIN writing_project_state p ON p.target_snapshot_id=s.id'):
            manifest = read_json(folder / 'objects' / row['manifest_blob'])
            report['selected_snapshots'].append({
                'id': row['id'], 'state': row['state'],
                'included_files': con.execute('SELECT COUNT(*) FROM repository_snapshot_files WHERE snapshot_id=?', (row['id'],)).fetchone()[0],
                'symbols': con.execute('SELECT COUNT(*) FROM code_units WHERE snapshot_id=?', (row['id'],)).fetchone()[0],
                'manifest_coverage': manifest.get('coverage'),
                'gap_count': len(manifest.get('gaps', [])),
            })
        return report
    finally:
        con.close()


def package_metadata(check_crc):
    path = ROOT.parent / '砚台论文工作台_0.4.0_便携版.zip'
    report = {'present': path.exists(), 'path': str(path)}
    if not path.exists():
        return report
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('便携包清单.json').decode('utf-8-sig'))
        report.update(bytes=path.stat().st_size, entries=len(archive.infolist()),
                      files_listed_in_manifest=len(manifest['files']),
                      user_data_included=manifest.get('user_data_included'), api_key_included=manifest.get('api_key_included'))
        expected = read_json(REPORTS / 'release-validation.json')['application_source_files']
        checks = []
        differences = []
        for relative, claimed in manifest['files'].items():
            if not (relative.startswith(('backend/', 'frontend/src/', 'tests/', 'scripts/', 'prompts/')) or relative == 'runner.py'):
                continue
            archived = hashlib.sha256(archive.read(relative)).hexdigest()
            current = file_hash(ROOT / relative) if (ROOT / relative).is_file() else None
            checks.append(relative)
            if archived != claimed or current != archived:
                differences.append({'path': relative, 'archive_matches_manifest': archived == claimed,
                                    'current_matches_archive': current == archived, 'owned_backend_module': relative in expected})
        report['program_and_test_files_checked'] = len(checks)
        report['differences'] = differences
        report['current_writing_modules_missing_from_archive_manifest'] = sorted(set(expected) - set(manifest['files']))
        if check_crc:
            bad = archive.testzip()
            report['crc'] = {'checked': True, 'first_bad_member': bad, 'passed': bad is None}
        else:
            report['crc'] = {'checked': False}
    return report


def junit_metadata(path):
    if not path.exists():
        return {'present': False}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
    return {'present': True, **{key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')},
            'seconds': sum(float(s.get('time', 0)) for s in suites)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--crc', action='store_true')
    args = parser.parse_args()
    recorded = read_json(REPORTS / 'release-validation.json')
    task_list = read_json(ROOT.parent / '论文写作工作台_实施任务清单_V4.0_2026-10-23.json')
    source_checks = []
    for relative, expected in recorded['application_source_files'].items():
        path = ROOT / relative
        actual = file_hash(path) if path.is_file() else None
        source_checks.append({'path': relative, 'expected': expected, 'actual': actual, 'matches': actual == expected})
    with urllib.request.urlopen('http://127.0.0.1:8765/api/v1/health', timeout=10) as response:
        health = json.load(response)
    report = {
        'audited_at_utc': datetime.now(timezone.utc).isoformat(),
        'overall': 'not_fully_complete',
        'scope': 'Code review, isolated regression/gap reproduction, read-only database metadata and package verification; no paid model requests or human approvals.',
        'current_service': health,
        'task_status_counts': task_list.get('completion_audit', {}).get('task_status_counts'),
        'task_assessments': [{'id': task['id'], 'status': task['status'], 'audit': task.get('audit')} for task in task_list['tasks']],
        'release_module_hashes': {'checked': len(source_checks), 'differences': [r for r in source_checks if not r['matches']], 'checks': source_checks},
        'backend_regression': junit_metadata(REPORTS / 'completion-audit-tests.xml'),
        'gap_reproduction': read_json(REPORTS / 'completion-audit-probes.json'),
        'live_database': database_metadata(ROOT / 'data'),
        'experiment_database': database_metadata(ROOT / '.runtime/writing-v4-live-data'),
        'package': package_metadata(args.crc),
        'historical_browser_report': read_json(REPORTS / 'browser-report.json'),
        'browser_rerun_in_this_audit': False,
        'frontend_audit_build_directory': '.runtime/writing-v4-audit-dist',
        'frontend_audit_build_present': (ROOT / '.runtime/writing-v4-audit-dist/index.html').exists(),
    }
    (REPORTS / 'completion-audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'overall': report['overall'], 'module_hashes_checked': len(source_checks),
        'module_differences': len(report['release_module_hashes']['differences']),
        'backend_regression': report['backend_regression'],
        'diagnostics': report['experiment_database']['diagnostic_samples'],
        'package_crc': report['package']['crc'],
        'package_source_differences': report['package']['differences'],
        'report': str(REPORTS / 'completion-audit.json'),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
