"""Manual review of extracted originals, scoped to a file or active project files."""
import hashlib

from fastapi import HTTPException

from . import db, security


def _load_file(connection, file_id):
    row = connection.execute('SELECT * FROM files WHERE id=?', (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, '文件不存在')
    file = dict(row)
    pages = [dict(p) for p in connection.execute(
        'SELECT id,page_number,text,notes,review_state FROM pages WHERE file_id=? ORDER BY page_number',
        (file_id,),
    )]
    pending = [dict(t) for t in connection.execute(
        "SELECT id,status FROM tasks WHERE kind='parse' AND json_extract(payload,'$.file_id')=? AND status IN ('queued','running') ORDER BY id",
        (file_id,),
    )]
    return {'file': file, 'pages': pages, 'pending': pending}


def _issue(record):
    file, pages = record['file'], record['pages']
    if not file['active']:
        return '已停用资料不能确认核对'
    if file['status'] != 'ready':
        return '请先完成资料解析，再确认原文已核对'
    if record['pending']:
        return '正在等待或执行重新解析，请完成后再核对'
    if not pages:
        return '文档还没有可核对的页面'
    if len(pages) != file['pages']:
        return '页面数量与解析信息不一致，请先重新解析后核对'
    if any(p['review_state'] not in ('unreviewed', 'reviewed', 'not_applicable') for p in pages):
        return '存在无效的原文核对状态，请刷新后检查'
    return None


def _apply(connection, record):
    file_id = record['file']['id']
    targets = [p['id'] for p in record['pages'] if p['review_state'] == 'unreviewed']
    if targets:
        connection.execute(
            "UPDATE sources SET verified=1 WHERE file_id=? AND page_id IN (SELECT id FROM pages WHERE file_id=? AND review_state='unreviewed')",
            (file_id, file_id),
        )
        connection.execute(
            "UPDATE pages SET review_state='reviewed' WHERE file_id=? AND review_state='unreviewed'",
            (file_id,),
        )
    return targets


def review_file(file_id, body):
    if body.get('confirm') is not True:
        raise HTTPException(422, '请明确确认已核对整份文档的原文')
    expected = body.get('page_ids')
    if not isinstance(expected, list) or not expected or any(not isinstance(x, str) for x in expected) or len(set(expected)) != len(expected):
        raise HTTPException(422, '请提供当前文档的完整页码列表')
    with db.transaction() as connection:
        record = _load_file(connection, file_id)
        issue = _issue(record)
        if issue:
            empty_ready = not record['pages'] and record['file']['active'] and record['file']['status'] == 'ready' and not record['pending']
            raise HTTPException(422 if empty_ready else 409, issue)
        if {p['id'] for p in record['pages']} != set(expected):
            raise HTTPException(409, '文档页面已变化，请刷新后重新核对')
        targets = _apply(connection, record)
        if targets:
            db.audit(record['file']['project_id'], 'document_original_reviewed', {
                'file_id': file_id, 'reviewed_page_ids': targets,
                'scope': 'original_extraction', 'summary_reviewed': False,
            })
    return {'file_id': file_id, 'updated_pages': len(targets), 'summary_reviewed': False}


def _project_state(connection, project_id):
    file_ids = [r['id'] for r in connection.execute(
        'SELECT id FROM files WHERE project_id=? AND active=1 ORDER BY id', (project_id,),
    )]
    records = [_load_file(connection, file_id) for file_id in file_ids]
    blocked = []
    reviewed = excluded = unreviewed = total = pending_files = 0
    for record in records:
        issue = _issue(record)
        if issue:
            blocked.append({'file_id': record['file']['id'], 'name': record['file']['name'], 'reason': issue})
        states = [p['review_state'] for p in record['pages']]
        reviewed += states.count('reviewed')
        excluded += states.count('not_applicable')
        pending = states.count('unreviewed')
        unreviewed += pending
        pending_files += int(pending > 0)
        total += len(states)
    # Include text and notes: reused page IDs must not let changed originals pass.
    fingerprint = hashlib.sha256(db.dumps(records).encode()).hexdigest()
    state = {
        'project_id': project_id, 'file_count': len(records), 'total_pages': total,
        'pending_files': pending_files, 'pending_pages': unreviewed,
        'reviewed_pages': reviewed, 'excluded_pages': excluded,
        'blocked_files': blocked, 'snapshot': fingerprint,
        'complete': bool(records) and total > 0 and not blocked and unreviewed == 0,
        'can_confirm': bool(records) and not blocked and unreviewed > 0,
        'summary_reviewed': False, 'external_requests': False,
    }
    return records, state


def project_state(project_id):
    security.require_project(project_id)
    connection = db.connect()
    try:
        connection.execute('BEGIN')
        return _project_state(connection, project_id)[1]
    finally:
        connection.close()


def review_project(project_id, body):
    security.require_project(project_id)
    if body.get('confirm') is not True:
        raise HTTPException(422, '请明确确认当前项目全部在用文件的原文已核对')
    snapshot = body.get('snapshot')
    if not isinstance(snapshot, str) or not snapshot:
        raise HTTPException(422, '请先读取当前项目的文件和核对范围')
    with db.transaction() as connection:
        records, state = _project_state(connection, project_id)
        if snapshot != state['snapshot']:
            raise HTTPException(409, '资料内容、文件列表或核对状态已变化，请刷新范围后重新确认')
        if not records:
            raise HTTPException(422, '当前项目没有可核对的在用资料')
        if state['blocked_files']:
            first = state['blocked_files'][0]
            raise HTTPException(409, first['name'] + '：' + first['reason'] + '；全部文件均就绪后才能统一确认')
        updates = []
        for record in records:
            targets = _apply(connection, record)
            if targets:
                updates.append({'file_id': record['file']['id'], 'reviewed_page_ids': targets})
        if updates:
            db.audit(project_id, 'project_originals_reviewed', {
                'scope': 'active_project_original_extraction', 'files': updates,
                'summary_reviewed': False,
            })
        result = _project_state(connection, project_id)[1]
        result.update(updated_files=len(updates), updated_pages=sum(len(u['reviewed_page_ids']) for u in updates))
    return result
