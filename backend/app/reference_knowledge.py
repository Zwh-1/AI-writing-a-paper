"""Project literature knowledge base over immutable originals and canonical source IDs."""
import hashlib

from fastapi import APIRouter, HTTPException

from . import db, security
from .material_policy import research_sql,is_research

router = APIRouter(prefix='/projects/{pid}/knowledge-base')


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def scope_sql():
    from .document_observations import current_source_sql
    return 'AND '+current_source_sql()+' AND '+research_sql()+"""
        AND (s.kind='evidence' OR (s.kind='example' AND f.role='example'))
        AND (p.review_state IS NULL OR p.review_state!='not_applicable')
        AND EXISTS (SELECT 1 FROM knowledge_chunks kc JOIN knowledge_documents kd ON kd.id=kc.document_id
            WHERE kc.source_id=s.id AND kd.file_id=f.id AND kd.project_id=f.project_id
            AND kd.state='ready' AND kd.blob_sha256=f.sha256)"""


@db.atomic
def register_document(file_id):
    original = db.one('SELECT * FROM files WHERE id=?', (file_id,))
    if not original:
        raise HTTPException(404, '知识库原文不存在')
    existing = db.one('SELECT * FROM knowledge_documents WHERE file_id=?', (file_id,))
    if not is_research(original) or not original['active']:
        if existing:
            db.execute("UPDATE knowledge_documents SET state='inactive' WHERE id=?", (existing['id'],))
        return None
    if not existing:
        identity = text_hash(original['project_id'] + ':' + file_id)[:32]
        db.execute('INSERT INTO knowledge_documents(id,project_id,file_id,blob_sha256,created_at) VALUES(?,?,?,?,?)', (identity, original['project_id'], file_id, original['sha256'], db.now()))
    return db.one('SELECT * FROM knowledge_documents WHERE file_id=?', (file_id,))


@db.atomic
def sync_document(file_id):
    from .parsers import tokens
    document = register_document(file_id)
    if not document:
        return None
    original = db.one('SELECT * FROM files WHERE id=?', (file_id,))
    sources = db.rows("SELECT s.* FROM sources s LEFT JOIN pages p ON p.id=s.page_id WHERE s.file_id=? AND (p.review_state IS NULL OR p.review_state!='not_applicable') ORDER BY s.id", (file_id,))
    sources = [source for source in sources if source['text'].strip()]
    previous = {item['source_id']: item['source_hash'] for item in db.rows('SELECT * FROM knowledge_chunks WHERE document_id=?', (document['id'],))}
    expected={source['id']:text_hash(source['text']) for source in sources}
    expected_state='ready' if sources else 'empty' if original['status']=='ready' else 'pending'
    expected_digest=text_hash(db.dumps({'blob':original['sha256'],'chunks':expected}))
    if previous==expected and document['content_hash']==expected_digest and document['state']==expected_state:
        present=db.one('SELECT COUNT(DISTINCT ft.source_id) AS n FROM source_fts ft JOIN knowledge_chunks kc ON kc.source_id=ft.source_id WHERE kc.document_id=?',(document['id'],))['n']
        if present==len(expected):return document
    current = {}
    for source in sources:
        digest = text_hash(source['text'])
        current[source['id']] = digest
        if source['id'] in previous and previous[source['id']] != digest:
            db.execute('DELETE FROM source_fts WHERE source_id=?', (source['id'],))
        if not db.one('SELECT source_id FROM source_fts WHERE source_id=? LIMIT 1', (source['id'],)):
            db.execute('INSERT INTO source_fts VALUES(?,?)', (source['id'], tokens(source['text'])))
        db.execute('INSERT INTO knowledge_chunks VALUES(?,?,?) ON CONFLICT(document_id,source_id) DO UPDATE SET source_hash=excluded.source_hash', (document['id'], source['id'], digest))
    for removed in set(previous) - set(current):
        db.execute('DELETE FROM knowledge_chunks WHERE document_id=? AND source_id=?', (document['id'], removed))
    digest = text_hash(db.dumps({'blob': original['sha256'], 'chunks': current}))
    state = 'ready' if sources else 'empty' if original['status'] == 'ready' else 'pending'
    if digest != document['content_hash'] or state != document['state']:
        db.execute('UPDATE knowledge_documents SET blob_sha256=?,content_hash=?,chunk_count=?,state=?,indexed_at=? WHERE id=?', (original['sha256'], digest, len(sources), state, db.now(), document['id']))
        if document['content_hash'] and digest!=document['content_hash']:
            from .knowledge_units import invalidate_file
            invalidate_file(original['project_id'],file_id,'文献原文或页面适用范围变化，原有证据与引用需复查')
    return db.one('SELECT * FROM knowledge_documents WHERE id=?', (document['id'],))


@db.atomic
def sync_project(pid):
    security.require_project(pid)
    files = db.rows("SELECT f.id FROM files f WHERE f.project_id=? AND "+research_sql()+" AND f.active=1 ORDER BY f.created_at,f.id", (pid,))
    documents = [sync_document(original['id']) for original in files]
    db.execute("UPDATE knowledge_documents SET state='inactive' WHERE project_id=? AND file_id IN (SELECT f.id FROM files f WHERE NOT "+research_sql()+" OR f.active=0)", (pid,))
    return documents


def current_sources(pid):
    return db.rows("SELECT s.*,f.name,f.extension,f.role,f.egress,f.active,f.sha256 AS file_hash,p.page_number,p.title AS page_title,p.review_state,kd.id AS knowledge_document_id,kd.content_hash AS knowledge_document_version,kc.source_hash AS knowledge_chunk_hash FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id JOIN knowledge_chunks kc ON kc.source_id=s.id JOIN knowledge_documents kd ON kd.id=kc.document_id WHERE f.project_id=? AND f.active=1 " + scope_sql(), (pid,))


def decorate(pid, items):
    metadata = {}
    for reference in db.rows('SELECT * FROM bibliography WHERE project_id=? ORDER BY created_at,id', (pid,)):
        if reference['file_id']:
            metadata.setdefault(reference['file_id'], []).append({**reference, 'metadata': db.loads(reference['metadata'], {})})
    memberships = {item['id']: item for item in current_sources(pid)}
    result = []
    for item in items:
        member = memberships.get(item['id'])
        if not member or text_hash(item['text']) != member['knowledge_chunk_hash']:
            continue
        references = metadata.get(item['file_id'], [])
        meta = references[0]['metadata'] if references else {}
        result.append({
            **item,
            **{key: member[key] for key in ('knowledge_document_id', 'knowledge_document_version', 'knowledge_chunk_hash')},
            'reference_ids': [reference['id'] for reference in references],
            'reference_id': references[0]['id'] if references else None,
            'reference_title': meta.get('title', item.get('name', '')),
            'reference_authors': meta.get('authors', []), 'reference_year': meta.get('year', ''),
        })
    return result


def search(pid, query, limit=10, remote=False, rerank_override=None,file_ids=None):
    from . import parsers, retrieval, model_install
    security.require_project(pid)
    source_options={'file_ids':file_ids} if file_ids else {}
    query = str(query).strip()
    if not query or len(query) > 5000:
        raise HTTPException(422, '请输入1至5000字的文献检索问题')
    limit = max(1, min(100 if rerank_override is not None else 30, int(limit)))
    sync_project(pid)
    cfg = retrieval.config(pid)
    enabled = db.setting('reference_knowledge:' + pid, {}).get('semantic_enabled', True)
    ready = model_install.status(cfg['model'])['ready']
    warning = []
    vectors = db.one('SELECT v.source_id FROM neural_vectors v JOIN knowledge_chunks kc ON kc.source_id=v.source_id JOIN knowledge_documents kd ON kd.id=kc.document_id WHERE kd.project_id=? AND v.model=? LIMIT 1', (pid, retrieval.model_key(cfg['model']))) if ready else None
    if enabled and ready and vectors:
        try:
            result = retrieval.hybrid(pid, query, 'research', limit, remote=remote, force=True, knowledge=True,**source_options,**({'rerank_override':rerank_override} if rerank_override is not None else {}))
        except ValueError as error:
            warning.append(str(error))
            result = {'items': parsers.keyword_search(pid, query, 'research', limit, remote, knowledge=True,**source_options), 'mode': 'keyword'}
    else:
        result = {'items': parsers.keyword_search(pid, query, 'research', limit, remote, knowledge=True,**source_options), 'mode': 'keyword'}
        if enabled and not ready:
            warning.append('本地向量模型尚未就绪，当前使用知识库全文检索')
        elif enabled and not vectors:
            warning.append('向量索引尚未建立，当前使用知识库全文检索')
    items = decorate(pid, result['items'])
    if remote:
        items = [item for item in items if security.file_allows_remote(item['file_id'], pid)]
    return {**result, 'items': items, 'warnings': [*result.get('warnings', []), *warning], 'collection_id': 'literature:' + pid, 'external_requests': False}


def get_chunk(pid, source_id, remote=False):
    security.require_project(pid)
    source = db.one('SELECT s.*,f.name,f.extension,f.project_id,f.active,f.role,p.page_number,p.title AS page_title,p.review_state FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=? AND f.project_id=?', (source_id, pid))
    if not source or not source['active'] or not is_research({'id':source['file_id'],'role':source['role']}) or source['review_state'] == 'not_applicable':
        raise HTTPException(404, '知识片段不存在于当前文献知识库')
    if remote and not security.file_allows_remote(source['file_id'], pid):
        raise HTTPException(403, '该文献或其来源禁止外发')
    sync_document(source['file_id'])
    items = decorate(pid, [source])
    if not items:
        raise HTTPException(404, '知识片段已更新，请重新检索')
    return items[0]


def permission_epoch(pid):
    # Includes content and permissions so cached observations never bypass revocation.
    files = db.rows("SELECT f.id,f.sha256,f.egress,f.active,f.role FROM files f WHERE f.project_id=? AND "+research_sql()+" ORDER BY f.id", (pid,))
    for original in files:
        original['effective_remote'] = security.file_allows_remote(original['id'], pid)
    sources = db.rows("SELECT s.id,s.text,p.review_state FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND "+research_sql()+" ORDER BY s.id", (pid,))
    terms = db.rows('SELECT zh,en FROM terms WHERE project_id=? ORDER BY zh', (pid,))
    return text_hash(db.dumps({'files': files, 'sources': [(item['id'], text_hash(item['text']), item['review_state']) for item in sources], 'terms': terms,'usages':db.rows('SELECT fu.* FROM file_usage_additions fu JOIN files f ON f.id=fu.file_id WHERE f.project_id=? ORDER BY fu.file_id,fu.usage',(pid,))}))


def status(pid):
    from . import retrieval, model_install
    security.require_project(pid)
    originals = db.rows("SELECT f.id,f.name,f.pages,f.status,kd.id AS document_id,kd.state,kd.chunk_count,kd.indexed_at FROM files f LEFT JOIN knowledge_documents kd ON kd.file_id=f.id WHERE f.project_id=? AND "+research_sql()+" AND f.active=1 ORDER BY f.created_at,f.id", (pid,))
    sources = current_sources(pid)
    cfg = retrieval.config(pid)
    model = model_install.status(cfg['model'])
    rerank_enabled = db.setting('reference_knowledge:' + pid, {}).get('rerank_enabled', True)
    rerank_model = model_install.status(cfg['cross_encoder'])
    indexed = 0
    if model['ready']:
        namespace = retrieval.model_key(cfg['model'])
        vectors = {row['source_id']: row['text_hash'] for row in db.rows('SELECT v.source_id,v.text_hash FROM neural_vectors v JOIN sources s ON s.id=v.source_id JOIN files f ON f.id=s.file_id WHERE f.project_id=? AND v.model=?', (pid, namespace))}
        indexed = sum(vectors.get(source['id']) == text_hash(retrieval.embedding_text(source)) and source['knowledge_chunk_hash'] == text_hash(source['text']) for source in sources)
    active = db.one("SELECT id,status,phase,progress FROM tasks WHERE project_id=? AND kind='knowledge_index' AND status IN ('queued','running') ORDER BY created_at LIMIT 1", (pid,))
    return {
        'collection_id': 'literature:' + pid, 'name': '文献知识库',
        'documents': len(originals), 'ready_documents': sum(item['state'] == 'ready' for item in originals),
        'pages': sum(item['pages'] for item in originals), 'chunks': len(sources),
        'vectors': indexed, 'model': cfg['model'], 'model_ready': model['ready'],
        'rerank_enabled': rerank_enabled, 'reranker': 'cross_encoder' if rerank_enabled else 'fusion_only',
        'reranker_model': cfg['cross_encoder'], 'reranker_ready': rerank_model['ready'],
        'mode': 'hybrid' if indexed else 'keyword', 'task': active, 'files': originals,
        'storage': 'local_originals_sqlite_fts_vectors', 'external_requests': False,
    }


def schedule_index(pid):
    from . import tasks
    return tasks.enqueue(pid, 'knowledge_index', {})


def build_index(pid, progress=None):
    from . import retrieval, model_install
    sync_project(pid)
    cfg = retrieval.config(pid)
    if model_install.status(cfg['model'])['ready']:
        result = retrieval.build_index(pid, progress, knowledge=True)
    else:
        result = {'indexed': 0, 'warnings': ['本地模型未安装，已建立全文知识库索引；向量索引可在模型就绪后更新']}
    return {**result, 'knowledge_base': status(pid), 'external_requests': False}


@router.get('')
def knowledge_status(pid: str):
    return status(pid)


@router.post('/index')
def knowledge_index(pid: str):
    security.require_project(pid)
    return schedule_index(pid)


@router.post('/search')
def knowledge_search(pid: str, body: dict):
    from .knowledge_service import search as unified_search
    return unified_search(pid, body.get('query', ''), body.get('limit', 10),collection=body.get('collection','originals'),file_ids=body.get('file_ids'))


@router.get('/chunks/{source_id}')
def knowledge_chunk(pid: str, source_id: str):
    return get_chunk(pid, source_id)


@router.get('/documents/{file_id}/chunks')
def document_chunks(pid: str, file_id: str, offset: int = 0, limit: int = 20):
    security.require_project(pid)
    original = db.one("SELECT f.id FROM files f WHERE f.id=? AND f.project_id=? AND "+research_sql()+" AND f.active=1", (file_id, pid))
    if not original:
        raise HTTPException(404, '文献不存在于当前知识库')
    if offset < 0 or not 1 <= limit <= 50:
        raise HTTPException(422, '知识片段分页参数无效')
    sources = [source for source in current_sources(pid) if source['file_id'] == file_id]
    sources.sort(key=lambda item: (item['page_number'] or 0, db.loads(item['locator'], {}).get('char_start', 0), item['id']))
    return {'items': decorate(pid, sources[offset:offset + limit]), 'total': len(sources), 'offset': offset, 'limit': limit}
