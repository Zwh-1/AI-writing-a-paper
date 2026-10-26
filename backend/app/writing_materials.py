"""Local original material for the ordinary chapter editor."""
from . import db, documents, parsers, security


def load(pid, sid, query=''):
    security.require_project(pid)
    section = security.require_section(sid)
    if section['project_id'] != pid:
        from fastapi import HTTPException
        raise HTTPException(404, '章节不属于当前项目')
    source_ids = set()
    revision = documents.current(section)
    if revision:
        for node in documents.nodes(db.loads(revision['document'], {})):
            source_ids.update(node.get('attrs', {}).get('sourceIds', []))
    head = db.one('SELECT contract_heads_json FROM writing_project_state WHERE project_id=?', (pid,))
    contract_id = db.loads(head['contract_heads_json'], {}).get(sid) if head else None
    contract = db.one('SELECT content_json FROM section_contract_versions WHERE id=? AND section_id=? AND project_id=?', (contract_id, sid, pid)) if contract_id else None
    if contract:
        # Contract evidence IDs belong to evidence_items, not to source units.
        for eid in db.loads(contract['content_json'], {}).get('evidence_ids', []):
            item = db.one("SELECT origin_id FROM evidence_items WHERE id=? AND project_id=? AND origin_type='source'", (eid, pid))
            if item:
                source_ids.add(item['origin_id'])
    eligible = "FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1 AND TRIM(s.text)!='' AND (p.review_state IS NULL OR p.review_state!='not_applicable')"
    fields = 'SELECT s.*,f.name,f.role,f.extension,f.egress,p.page_number,p.review_state '
    query = query.strip()
    if query:
        # Include rules, examples and research data as well as literature.
        hits = parsers.keyword_search(pid, query, kind='all', limit=100)
        items = [item for item in hits if item.get('review_state') != 'not_applicable' and item['text'].strip()]
        mode = 'search'
    elif source_ids:
        marks = ','.join('?' for _ in source_ids)
        items = db.rows(fields + eligible + f' AND s.id IN ({marks}) ORDER BY f.name,p.page_number,s.id', (pid, *sorted(source_ids)))
        mode = 'linked'
    else:
        items = db.rows(fields + eligible + ' ORDER BY f.created_at DESC,f.id,p.page_number,s.id LIMIT 20', (pid,))
        mode = 'recent'
    for item in items:
        item['linked'] = item['id'] in source_ids
    return {'items': items, 'mode': mode}
