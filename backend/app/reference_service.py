"""The single mutation boundary for manual and connector bibliography changes."""
from fastapi import HTTPException
from . import db, security, documents


def get(pid, identity, decoded=True):
    security.require_project(pid)
    row = db.one('SELECT * FROM bibliography WHERE id=? AND project_id=?', (identity, pid))
    if not row:
        raise HTTPException(404, '文献不属于当前项目')
    return {**row, 'metadata': db.loads(row['metadata'], {})} if decoded else row


def changed(pid, identity):
    from .knowledge_lineage import changed as invalidate
    invalidate(pid, 'reference', identity, '书目信息已更新，关联内容需复查')
    documents.fact_changed(pid, identity)
    row=get(pid,identity)
    if db.setting('graph_wiki_enabled:' + pid, False) and not row['metadata'].get('_studio_deleted'):
        from .knowledge_metadata import enrich
        enrich(pid, identity)


@db.atomic
def save(pid, metadata, fid=None, identity=None, verification='unverified', expected_revision=None):
    security.require_project(pid)
    if not isinstance(metadata, dict) or len(db.dumps(metadata).encode()) > 2_000_000:
        raise HTTPException(422, '书目信息无效')
    if fid and not db.one('SELECT id FROM files WHERE id=? AND project_id=?', (fid, pid)):
        raise HTTPException(422, '文献原件不属于当前项目')
    if identity:
        old = get(pid, identity, False)
        if expected_revision is not None and old['revision'] != expected_revision:
            raise HTTPException(409, '书目版本已变化，请重新读取')
        if old['metadata'] == db.dumps(metadata) and old['file_id'] == fid and old['verification'] == verification:
            return get(pid, identity)
        from .writing_agent.impact import retain_existing
        for item in db.rows('SELECT DISTINCT s.id FROM sections s JOIN citations c ON c.revision_id=s.current_revision_id WHERE s.project_id=? AND c.reference_id=?', (pid, identity)):
            retain_existing(pid, item['id'])
        db.execute('INSERT INTO bibliography_revisions VALUES(?,?,?,?)', (db.uid(), identity, old['metadata'], db.now()))
        db.execute('UPDATE bibliography SET metadata=?,file_id=?,verification=?,revision=revision+1 WHERE id=?', (db.dumps(metadata), fid, verification, identity))
    else:
        identity = db.uid()
        db.execute('INSERT INTO bibliography(id,project_id,metadata,file_id,verification,created_at) VALUES(?,?,?,?,?,?)', (identity, pid, db.dumps(metadata), fid, verification, db.now()))
    changed(pid, identity)
    db.audit(pid, 'reference_saved', {'reference_id': identity})
    return get(pid, identity)


@db.atomic
def update(identity, body):
    row = db.one('SELECT * FROM bibliography WHERE id=?', (identity,))
    if not row:
        raise HTTPException(404, '文献不存在')
    return save(row['project_id'], body.get('metadata', db.loads(row['metadata'], {})), body.get('file_id', row['file_id']), identity,
                body.get('verification', row['verification']), body.get('revision'))


@db.atomic
def upsert_external(pid, metadata, fid=None, provider=None, library='', external='', remote_version=''):
    security.require_project(pid)
    if fid:
        if not db.one('SELECT id FROM files WHERE id=? AND project_id=?', (fid, pid)):
            raise HTTPException(404, '文献原件不属于当前项目')
        db.execute('UPDATE files SET active=1 WHERE id=?', (fid,))
    old = None
    if provider:
        old = db.one('SELECT b.* FROM studio_external_links l JOIN bibliography b ON b.id=l.reference_id WHERE l.project_id=? AND l.provider=? AND l.library_id=? AND l.external_id=?', (pid, provider, library, external))
    if not old:
        doi = str(metadata.get('doi', '')).lower().removeprefix('https://doi.org/').strip()
        if doi:
            old = next((row for row in db.rows('SELECT * FROM bibliography WHERE project_id=?', (pid,)) if str(db.loads(row['metadata'], {}).get('doi', '')).lower().removeprefix('https://doi.org/').strip() == doi), None)
        if not old and fid:
            old = db.one('SELECT * FROM bibliography WHERE project_id=? AND file_id=? ORDER BY created_at LIMIT 1', (pid, fid))
    previous = db.loads(old['metadata'], {}) if old else {}
    value = {**previous, **metadata, 'tags': list(dict.fromkeys([*previous.get('tags', []), *metadata.get('tags', [])]))}
    value.pop('_studio_deleted', None); value.pop('deleted_at', None)
    row = save(pid, value, fid or (old['file_id'] if old else None), old['id'] if old else None, old['verification'] if old else 'unverified')
    if provider:
        db.execute('INSERT INTO studio_external_links VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,provider,library_id,external_id) DO UPDATE SET reference_id=excluded.reference_id,remote_version=excluded.remote_version,data=excluded.data,updated_at=excluded.updated_at',
            (pid, provider, library, external, row['id'], str(remote_version), db.dumps({'source': provider}), db.now()))
    return row


@db.atomic
def set_tags(pid, identities, tags, mode='replace'):
    if not isinstance(identities, list) or not 1 <= len(identities) <= 500 or not isinstance(tags, list) or len(tags) > 80:
        raise HTTPException(422, '文献或标签数量无效')
    clean = list(dict.fromkeys(tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()))
    if any(len(tag) > 80 for tag in clean) or mode not in ('replace', 'add', 'remove'):
        raise HTTPException(422, '标签或修改模式无效')
    for identity in identities:
        row = get(pid, identity); old = row['metadata'].get('tags', [])
        value = clean if mode == 'replace' else list(dict.fromkeys(old + clean)) if mode == 'add' else [tag for tag in old if tag not in clean]
        save(pid, {**row['metadata'], 'tags': value}, row['file_id'], identity, row['verification'], row['revision'])
    return {'updated': len(identities), 'tags': clean}
