"""Shared export policy, immutable input snapshot and explicit output artifacts."""
import hashlib
import mimetypes
from urllib.parse import quote
from fastapi import HTTPException
from . import db, security


def prepare(pid, options):
    from . import reviews
    from .export_checks import freeze
    mode = options.get('mode', options.get('writing_policy', 'draft'))
    if mode not in ('draft', 'strict'):
        raise HTTPException(422, '导出模式无效')
    security.require_project(pid)
    reviews.run(pid)
    with db.transaction():
        frozen = freeze(pid)
        from .academic_standards import report as bibliography_report
        standards=frozen['settings']['academic_standards:'+pid]
        cited_ids={row['reference_id'] for row in frozen['citations']}
        reference_report=bibliography_report([row for key,row in frozen['references'].items() if key in cited_ids],standards)
        if mode=='strict' and (reference_report['unresolved_conflicts'] or standards['bibliography_version']!='legacy' and reference_report['errors']):
            raise HTTPException(409,{'code':'ACADEMIC_STANDARD_ISSUES','message':'正式导出前请处理规范冲突和已引用书目缺项','report':reference_report})
        report = None
        if mode == 'strict' or db.one('SELECT id FROM thesis_core_versions WHERE project_id=? LIMIT 1', (pid,)):
            from .writing_agent.paper_review import export_report
            report = export_report(pid, mode == 'strict')
        from .paper_format import resolve
        formatting = resolve(pid, package=frozen['package'], settings=frozen['settings'].get('format:' + pid) or {}, project=frozen['project'])
        if mode == 'strict' and formatting['conflicts']:
            raise HTTPException(409, '已发布排版规则存在冲突，请先处理后正式导出')
    identity = hashlib.sha256(db.dumps({'project': pid, 'revisions': frozen['revisions'], 'references': frozen['references'],
        'artifacts': frozen['artifacts'], 'rules': frozen['package'], 'settings': frozen['settings'],
        'file_versions':frozen['file_versions'],'research_trace':frozen.get('research_trace',{})}).encode()).hexdigest()
    return {'snapshot': frozen, 'snapshot_id': identity, 'mode': mode, 'writing_report': report,
            'checks': {'writing': report, 'bibliography':reference_report, 'format_conflicts': formatting['conflicts'], 'visual_verification': 'not_run'}}


def artifacts(identity):
    from .config import DATA
    folder = DATA / 'exports' / identity
    if not folder.is_dir():
        return []
    result = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.endswith('.pending.pdf'):
            continue
        role = 'archive' if path.suffix == '.zip' else 'report' if any(word in path.name for word in ('报告', '清单', 'warnings', 'manifest', '映射', 'trace')) else 'source' if path.suffix in ('.tex', '.bib') else 'document'
        result.append({'name': path.name, 'role': role, 'media_type': mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
            'download_url': f'/api/v1/exports/{identity}/files/{quote(path.name)}', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def describe(row):
    manifest = db.loads(row['manifest'], {})
    format_ = manifest.get('export_format') or ('latex' if row['filename'] == 'latex-project.zip' else 'markdown' if row['filename'] == 'document.md' else 'docx')
    return {**row, 'format': format_, 'mode': manifest.get('mode', 'draft'), 'snapshot_id': manifest.get('snapshot_id'),
            'checks': manifest.get('checks', {'visual_verification': manifest.get('visual_check', 'not_run')}), 'artifacts': artifacts(row['id'])}


def history(pid):
    security.require_project(pid)
    return [describe(row) for row in db.rows('SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC,id', (pid,))]


def generate(pid, options, task_id=None):
    format_ = options.get('format', 'docx')
    if format_ not in ('docx', 'latex', 'markdown'):
        raise HTTPException(422, '导出格式无效')
    prepared = prepare(pid, options)
    if format_ == 'docx':
        from .exports import export_project
        result = export_project(pid, options, task_id, prepared=prepared)
    elif format_ == 'latex':
        from .research_delivery import latex_project
        result = latex_project(pid, options, prepared=prepared)
    else:
        result = markdown(pid, prepared)
    row = db.one('SELECT * FROM exports WHERE id=?', (result['export_id'],))
    manifest = db.loads(row['manifest'], {})
    manifest.update(export_format=format_, mode=prepared['mode'], snapshot_id=prepared['snapshot_id'], checks=prepared['checks'])
    db.execute('UPDATE exports SET manifest=? WHERE id=?', (db.dumps(manifest), row['id']))
    return {**result, **{key: value for key, value in describe({**row, 'manifest': db.dumps(manifest)}).items() if key in ('format', 'mode', 'snapshot_id', 'checks', 'artifacts')}}


def markdown(pid, prepared):
    from .config import DATA
    from .export_checks import markdown_document
    from .documents import nodes
    frozen = prepared['snapshot']; identity = db.uid(); folder = DATA / 'exports' / identity
    folder.mkdir(parents=True, exist_ok=True)
    parts = ['# ' + frozen['project']['title']]
    for section in frozen['sections']:
        revision = frozen['revisions'].get(section['id'])
        if revision:
            parts.append(markdown_document(db.loads(revision['document'], {})))
    from .bibliography_format import render as render_reference
    used_refs=list(dict.fromkeys(row['reference_id'] for row in frozen['citations']))
    for index,identity in enumerate(used_refs,1):
        parts=[part.replace('[cite:'+identity+']','['+str(index)+']') for part in parts]
    if used_refs:
        parts.append('## 参考文献')
        parts.extend('['+str(index)+'] '+render_reference(db.loads(frozen['references'][identity]['metadata'],{}),
            frozen['settings']['academic_standards:'+pid]['bibliography_version'])['text'] for index,identity in enumerate(used_refs,1))
    (folder / 'document.md').write_text('\n\n'.join(parts), encoding='utf-8')
    from PIL import Image
    used={node['attrs'].get('assetVersionId') or node['attrs'].get('artifactId')
        for revision in frozen['revisions'].values() for node in nodes(db.loads(revision['document'],{}))
        if node.get('type')=='figure' and node.get('attrs',{}).get('artifactId')}
    for resource_id in used:
        artifact=frozen['artifacts'].get(resource_id)
        if not artifact or not artifact['sha256']:raise HTTPException(409,'正文图片版本缺少资源')
        with Image.open(security.safe_object(artifact['sha256'])) as picture:
            picture.convert('RGB').save(folder/('图-'+resource_id+'.png'),format='PNG')
    (folder / 'knowledge-trace.json').write_text(db.dumps({'knowledge':frozen.get('graph_wiki', {}),'research':frozen.get('research_trace',{})}), encoding='utf-8')
    (folder / 'academic-standards.json').write_text(db.dumps(prepared['checks']['bibliography']),encoding='utf-8')
    db.execute('INSERT INTO exports VALUES(?,?,?,?,?,?)', (identity, pid, db.dumps({'mode': prepared['mode']}), 'document.md', 'ready', db.now()))
    return {'export_id': identity, 'download': f'/api/v1/exports/{identity}/files/document.md'}
