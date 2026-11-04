"""Verify and export an unadopted whole-paper candidate without database writes.

Run with the application's Python runtime. Reports never claim human approval.
The optional native exporter receives an in-memory candidate snapshot; its one
export-registration INSERT is captured locally and never reaches the database.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import runner  # noqa: E402,F401 - selects this application's dependency runtime.
from app import db, documents, paper_writing as paper  # noqa: E402


@contextmanager
def read_only_database():
    connect, execute = db.connect, db.execute

    def readonly_connect():
        path = Path(db.DB_PATH).resolve().as_posix()
        connection = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        return connection

    def capture_export_registration(sql, args=()):
        if sql.strip().lower().startswith('insert into exports '):
            return None
        if sql.strip().lower().startswith('insert or ignore into writing_project_state('):
            if db.one('SELECT project_id FROM writing_project_state WHERE project_id=?', args):
                return None
        raise RuntimeError('Candidate verification attempted a database write: ' + sql[:80])

    db.connect, db.execute = readonly_connect, capture_export_registration
    try:
        yield
    finally:
        db.connect, db.execute = connect, execute


def verify(task):
    payload = db.loads(task['payload'], {})
    output = db.loads(task.get('result'), {}) or {}
    plan = payload.get('snapshot', {}).get('sections', [])
    expected = {section['id']: section for section in plan}
    candidates = output.get('paper_sections', [])
    invalid = paper.invalid_checkpoint_candidates(output, expected)
    local_review = paper.review(output, payload.get('snapshot', {}))
    errors = []
    if task['status'] != 'succeeded':
        errors.append('Task has not succeeded: ' + task['status'])
    if output.get('complete') is not True:
        errors.append('The complete checkpoint is absent')
    if len(candidates) != len(payload.get('generation_order', [])):
        errors.append('Candidate count does not match the planned generation order')
    if len({item['section_id'] for item in candidates}) != len(candidates):
        errors.append('Candidate section IDs are duplicated')
    if not output.get('paper_hash') or paper.paper_hash(output) != output.get('paper_hash'):
        errors.append('The final paper hash is absent or does not match')
    errors.extend(finding['message'] for finding in invalid)
    errors.extend(finding['message'] for finding in local_review['findings']
                  if finding.get('severity') == 'error')
    if not errors:
        try:
            paper.validate_request(task)
        except Exception as exc:
            errors.append(str(getattr(exc, 'detail', exc)))
    metrics = []
    for item in candidates:
        section = expected.get(item['section_id'], {})
        check = paper.section_check(section, item)
        nodes = list(documents.nodes(item['document']))
        check.update(quality_status=item.get('writing_quality', {}).get('status'),
                     referenced_books=sorted({node.get('attrs', {}).get('citationId')
                         for node in nodes if node['type'] == 'citation'}),
                     source_evidence_nodes=sum(bool(node.get('attrs', {}).get('sourceIds') or
                         node.get('attrs', {}).get('knowledgeUnitIds') or
                         node.get('attrs', {}).get('evidenceIds')) for node in nodes))
        metrics.append(check)
    runs = db.rows('SELECT id,status,cost,reserved,model,created_at FROM generation_runs WHERE task_id=? ORDER BY created_at',
                   (task['id'],))
    return {'task_id': task['id'], 'project_id': task['project_id'], 'checked_at': db.now(),
            'task_status': task['status'], 'candidate_ready': not errors,
            'errors': errors, 'expected_sections': len(plan), 'completed_sections': len(candidates),
            'paper_hash': output.get('paper_hash'), 'chapter_metrics': metrics,
            'total_words': sum(row['words'] for row in metrics),
            'total_citations': sum(row['citations'] for row in metrics),
            'total_visuals': sum(row['visuals'] for row in metrics),
            'total_placeholders': sum(row['placeholders'] for row in metrics),
            'review': local_review, 'requests': runs,
            'human_review': 'pending', 'adopted': bool(output.get('adoption')),
            'scope': '候选完整性与当前来源版本检查，不代替人工事实、引用支持性或学校格式验收。'}


def candidate_snapshot(task, report):
    from app.export_checks import freeze, freeze_knowledge
    from app.writing_agent.common import unblob
    frozen = freeze(task['project_id'])
    output = db.loads(task['result'], {})
    by_id = {item['section_id']: item for item in output['paper_sections']}
    frozen['sections'] = [dict(section) for section in frozen['sections'] if section['id'] in by_id]
    frozen['revisions'], frozen['citations'] = {}, []
    evidence_ids = set()
    for section in frozen['sections']:
        item = by_id[section['id']]
        revision_id = 'candidate-' + task['id'] + '-' + section['id']
        section['current_revision_id'] = revision_id
        revision = {'id': revision_id, 'section_id': section['id'], 'task_id': task['id'],
                    'document': db.dumps(item['document']), 'text': item['text'],
                    'created_at': task['updated_at'], 'kind': 'unadopted_candidate'}
        frozen['revisions'][section['id']] = revision
        for node in documents.nodes(item['document']):
            attrs = node.get('attrs', {})
            evidence_ids.update(attrs.get('evidenceIds', []))
            if node['type'] == 'citation':
                reference_id = attrs.get('citationId')
                reference = frozen['references'].get(reference_id)
                if not reference:
                    raise ValueError('Candidate contains a dangling bibliographic reference')
                frozen['citations'].append({'revision_id': revision_id,
                    'reference_id': reference_id, 'node_id': attrs.get('nodeId'),
                    'reference_snapshot': reference['metadata'], 'support': 'unknown'})
    frozen['project_evidence'] = {}
    for identity in evidence_ids:
        record = db.one('SELECT * FROM evidence_items WHERE id=? AND project_id=?',
                        (identity, task['project_id']))
        if record is None:
            raise ValueError('Candidate references project evidence that no longer exists')
        frozen['project_evidence'][identity] = record
    for evidence in frozen['project_evidence'].values():
        if not isinstance(evidence.get('payload_hash'), str):
            raise ValueError('Candidate project evidence is missing its frozen payload')
        unblob(evidence['payload_hash'])
    # Adopted citation links and old human verdicts must not describe new drafts.
    for key in ('knowledge_links', 'citation_reading_links', 'reading_citation_reviews'):
        frozen[key] = []
    with db.connect() as connection:
        frozen['graph_wiki'] = freeze_knowledge(connection, task['project_id'], frozen['revisions'])
    mapping = [{'section_id': item['section_id'], 'document': item['document'],
                'manifest': item.get('manifest', {}), 'writing_quality': item.get('writing_quality', {})}
               for item in output['paper_sections']]
    return {'snapshot': frozen, 'mode': 'draft', 'snapshot_id': report['paper_hash'],
            'writing_report': {'candidate_task_id': task['id'], 'candidate_review': report,
                               'sentence_mapping': mapping, 'human_review': 'pending'}}


def export_candidate(task, report, directory, pdf=False, title=None):
    if not report['candidate_ready']:
        raise ValueError('Incomplete or invalid candidates cannot be exported as a final whole-paper draft')
    from app import exports
    prepared = candidate_snapshot(task, report)
    if title is not None:
        title = title.strip()
        if not title or len(title) > 140:
            raise ValueError('The export title must contain 1 to 140 characters')
        original_title = prepared['snapshot']['project']['title']
        prepared['snapshot']['project'] = {**prepared['snapshot']['project'], 'title': title}
        prepared['writing_report']['export_title'] = {
            'title': title, 'original_project_title': original_title,
            'scope': 'Export cover and headers only; saved project and candidate sources are unchanged'}
    destination = directory.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError('Refusing to overwrite an existing candidate delivery directory')
    destination.mkdir(parents=True, exist_ok=True)
    delivery_paths = []
    previous_data = exports.DATA
    try:
        with tempfile.TemporaryDirectory(prefix='candidate-export-', dir=destination.parent) as temporary:
            scratch = Path(temporary)
            (scratch / 'exports').mkdir()
            (scratch / 'tmp').mkdir()
            exports.DATA = scratch
            result = exports.export_project(task['project_id'], {'mode': 'draft', 'pdf': pdf}, prepared=prepared)
            for path in (scratch / 'exports' / result['export_id']).iterdir():
                if path.name == '论文交付包.zip' or path.name.endswith('.pending.pdf'):
                    continue
                if path.is_file():
                    if path.is_symlink():
                        raise ValueError('Candidate delivery files must not be symbolic links')
                    copied = destination / path.name
                    shutil.copyfile(path, copied)
                    delivery_paths.append(copied)
    finally:
        exports.DATA = previous_data
    (destination / '候选验收.json').write_text(db.dumps(report), encoding='utf-8')
    (destination / '审阅说明.md').write_text(
        '# 整篇论文候选审阅说明\n\n'
        '本交付包含完整生成候选，尚未代替作者人工审阅，也未覆盖项目当前正文。'
        '候选的规则、证据版本、缺口与检查记录随包保留。请先核对引用支持性、'
        '研究成果和待补事项，再决定是否统一写入正文。\n', encoding='utf-8')
    delivery_paths.extend(destination / name for name in ('候选验收.json', '审阅说明.md'))
    # Package the copied exporter files and final candidate notes only. Do not
    # sweep unrelated files that might have appeared in the destination.
    with zipfile.ZipFile(destination / '论文交付包.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(delivery_paths, key=lambda item: item.name):
            if path.is_symlink() or path.resolve().parent != destination:
                raise ValueError('Candidate delivery file escaped its output directory')
            archive.write(path, path.name)
    return {'directory': str(destination), 'files': sorted(path.name for path in destination.iterdir())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--export-dir', type=Path)
    parser.add_argument('--pdf', action='store_true')
    parser.add_argument('--title', help='Cover and header title for this independent export')
    args = parser.parse_args()
    with read_only_database():
        task = db.one('SELECT * FROM tasks WHERE id=? AND kind=\'write_paper\'', (args.task_id,))
        if not task:
            raise ValueError('Whole-paper task does not exist')
        report = verify(task)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        summary = {key: report[key] for key in ('task_id', 'task_status', 'candidate_ready',
                   'expected_sections', 'completed_sections', 'total_words', 'total_citations',
                   'total_visuals', 'total_placeholders', 'human_review')}
        summary['report'] = str(args.report.resolve())
        if args.export_dir:
            summary['delivery'] = export_candidate(task, report, args.export_dir, args.pdf, args.title)
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if report['candidate_ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
