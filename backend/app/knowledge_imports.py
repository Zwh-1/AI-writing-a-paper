"""Two-phase ordinary-file import; previews are editable and commits idempotent."""
from pathlib import Path
from fastapi import APIRouter, File, UploadFile, HTTPException
from . import db, parsers, security, tasks
from .config import MAX_FILE, MAX_UNPACKED

router = APIRouter(prefix='/projects/{pid}/evidence-knowledge/imports')


@router.post('/preview')
async def preview(pid: str, files: list[UploadFile] = File(...)):
    security.require_project(pid)
    if not 1 <= len(files) <= 100:
        raise HTTPException(422, '每批支持1至100个文件')
    items = []; total = 0
    for upload in files:
        data = await upload.read(MAX_FILE + 1); total += len(data)
        if total > MAX_UNPACKED:
            raise HTTPException(413, '本批文件总量超过限制，请分批导入')
        name = Path((upload.filename or 'file.txt').replace('\\', '/')).name
        ext = Path(name).suffix.lower(); error = None
        if ext not in parsers.SUPPORTED or ext in ('.bib', '.ris', '.json'):
            error = '请使用文献原件；BibTeX/RIS请通过书目导入'
        if not data or len(data) > MAX_FILE:
            error = '文件为空或超过120MB'
        sha = security.put_blob(data) if not error else None
        duplicate = db.one('SELECT id,name FROM files WHERE project_id=? AND sha256=? AND role=\'research\'', (pid, sha)) if sha else None
        items.append({'index': len(items), 'name': name, 'size': len(data), 'sha256': sha, 'extension': ext, 'error': error,
                      'duplicate': duplicate, 'metadata': {'title': Path(name).stem, 'authors': [], 'year': '', 'scope': 'full_text', 'metadata_source': '文件名待核对'}})
    identity = db.uid()
    db.execute('INSERT INTO knowledge_import_previews(id,project_id,manifest,created_at) VALUES(?,?,?,?)', (identity, pid, db.dumps({'items': items}), db.now()))
    return {'id': identity, 'items': items, 'external_requests': False}


@router.post('/{identity}/commit')
@db.atomic
def commit(pid: str, identity: str, body: dict):
    security.require_project(pid)
    saved = db.one('SELECT * FROM knowledge_import_previews WHERE id=? AND project_id=?', (identity, pid))
    if not saved:
        raise HTTPException(404, '导入预览不存在')
    manifest = db.loads(saved['manifest'], {})
    if saved['state'] == 'committed':
        return manifest['result']
    edits = body.get('items', [])
    if not isinstance(edits, list) or len(edits) > 100 or any(not isinstance(r, dict) for r in edits):
        raise HTTPException(422, '导入清单无效')
    if any(type(r.get('index')) is not int or type(r.get('include',True)) is not bool or not isinstance(r.get('metadata',{}),dict) for r in edits):raise HTTPException(422,'导入编号、选择或书目格式无效')
    by_index = {r.get('index'): r for r in edits}
    if len(by_index) != len(edits) or any(not isinstance(i, int) or not 0 <= i < len(manifest['items']) for i in by_index):
        raise HTTPException(422, '导入清单编号重复或越界')
    egress = body.get('egress', 'selected_content')
    if egress not in ('local_only', 'selected_content', 'allowed'):
        raise HTTPException(422, '外发权限无效')
    results = []
    for entry in manifest['items']:
        edit = by_index.get(entry['index'], {})
        if entry['error'] or not edit.get('include', True):
            continue
        data = security.safe_object(entry['sha256']).read_bytes()
        if __import__('hashlib').sha256(data).hexdigest() != entry['sha256']:
            raise HTTPException(409, '预览原件发生变化，请重新上传')
        metadata = {**entry['metadata'], **edit.get('metadata', {})}
        if set(metadata) - {'title', 'authors', 'year', 'doi', 'language', 'category', 'scope', 'metadata_source'}:
            raise HTTPException(422, '书目字段无效')
        if not isinstance(metadata.get('title'), str) or not metadata['title'].strip() or not isinstance(metadata.get('authors'), list) or any(not isinstance(a, str) for a in metadata['authors']) or len(db.dumps(metadata)) > 20000:
            raise HTTPException(422, '题名或作者格式无效')
        original, new = parsers.register_file(pid, entry['name'], data, 'research', egress)
        reference = db.one('SELECT * FROM bibliography WHERE project_id=? AND file_id=? ORDER BY created_at LIMIT 1', (pid, original['id']))
        if not reference:
            rid = db.uid()
            db.execute('INSERT INTO bibliography(id,project_id,metadata,file_id,created_at) VALUES(?,?,?,?,?)', (rid, pid, db.dumps(metadata), original['id'], db.now()))
            db.execute('INSERT INTO bibliography_revisions VALUES(?,?,?,?)', (db.uid(), rid, db.dumps(metadata), db.now()))
        else:
            rid = reference['id']
        job = tasks.enqueue(pid, 'parse', {'file_id': original['id']}) if new or original['status'] != 'ready' else None
        results.append({'file_id': original['id'], 'reference_id': rid, 'name': original['name'], 'new': new, 'task_id': job['id'] if job else None, 'existing_metadata_preserved': bool(reference)})
    result = {'files': results, 'external_requests': False}
    manifest['result'] = result
    db.execute("UPDATE knowledge_import_previews SET state='committed',manifest=? WHERE id=?", (db.dumps(manifest), identity))
    db.audit(pid, 'ordinary_literature_imported', {'preview_id': identity, 'files': len(results)})
    return result
