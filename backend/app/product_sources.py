"""Adapters for legacy outputs participating in versioned resource references."""
from fastapi import HTTPException
from . import db, security, resource_refs as refs
from . import product_adapters as adapters

TABLES = {'summary': ('material_summaries', 'file_id'), 'fact': ('facts', 'id'),
          'analysis': ('advanced_runs', 'id'), **{key:(value,'id') for key,value in adapters.EXTRA_TABLES.items()}}


def owned(pid, kind, identity):
    if kind in adapters.EXTRA_TABLES:return adapters.owned(pid,kind,identity)
    security.require_project(pid)
    if kind == 'summary':
        row = db.one('SELECT m.*,f.project_id FROM material_summaries m JOIN files f ON f.id=m.file_id WHERE m.file_id=? AND f.project_id=?', (identity, pid))
    else:
        table, key = TABLES[kind]
        row = db.one('SELECT * FROM ' + table + ' WHERE ' + key + '=? AND project_id=?', (identity, pid))
    if not row:
        raise HTTPException(404, '产物不存在或不属于当前项目')
    return row


def origins(pid, kind, row):
    if kind in adapters.EXTRA_TABLES:return adapters.origins(pid,kind,row)
    if kind == 'summary':
        return [('file', row['file_id'])]
    if kind == 'analysis':
        parameters = db.loads(row['parameters'], {})
        return [('file', parameters['file_id'])] if parameters.get('file_id') else []
    result = [('source', sid) for sid in db.loads(row['source_ids'], [])]
    if row.get('artifact_id'):
        result.append(('asset', row['artifact_id']))
    return result


def dependents(pid, kind, identity):
    """Reverse legacy provenance so edits also invalidate downstream research drafts."""
    result=adapters.dependents(pid,kind,identity)
    if kind=='file':
        if db.one('SELECT file_id FROM material_summaries WHERE file_id=?',(identity,)):
            result.append(('summary',identity))
        result.extend(('analysis',row['id']) for row in db.rows("SELECT id FROM advanced_runs WHERE project_id=? AND kind='advanced_statistics' AND json_extract(parameters,'$.file_id')=?",(pid,identity)))
        result.extend(('fact',row['id']) for row in db.rows('SELECT DISTINCT f.id FROM facts f,json_each(f.source_ids) r JOIN sources s ON s.id=r.value WHERE f.project_id=? AND s.file_id=?',(pid,identity)))
        result.extend(('source',row['id']) for row in db.rows("SELECT DISTINCT s.id FROM sources s JOIN research_links l ON l.target_type='source' AND l.target_id=s.id WHERE l.project_id=? AND s.file_id=?",(pid,identity)))
        result.extend(('source',row['id']) for row in db.rows("SELECT DISTINCT s.id FROM artifacts a,json_each(a.metadata,'$.source_refs') r JOIN sources s ON s.id=json_extract(r.value,'$.id') WHERE a.project_id=? AND json_extract(r.value,'$.type')='source' AND s.file_id=?",(pid,identity)))
    if kind=='source':
        result.extend(('fact',row['id']) for row in db.rows('SELECT DISTINCT f.id FROM facts f,json_each(f.source_ids) s WHERE f.project_id=? AND s.value=?',(pid,identity)))
    if kind=='asset':
        result.extend(('fact',row['id']) for row in db.rows('SELECT id FROM facts WHERE project_id=? AND artifact_id=?',(pid,identity)))
    result.extend(('asset',row['id']) for row in db.rows("SELECT DISTINCT a.id FROM artifacts a,json_each(a.metadata,'$.source_refs') r WHERE a.project_id=? AND json_extract(r.value,'$.type')=? AND json_extract(r.value,'$.id')=?",(pid,kind,identity)))
    return result


def version(pid, kind, identity, remote=False, seen=None):
    if kind in adapters.EXTRA_TABLES:return adapters.version(pid,kind,identity,remote,seen)
    row = owned(pid, kind, identity)
    upstream=[]
    for typ, uid in origins(pid, kind, row):
        upstream.append([typ,uid,refs.version_of(pid, typ, uid, remote, seen)])
    if kind == 'summary':
        from .material_summary import source
        if source(row['file_id'])[2] != row['input_hash']:
            raise HTTPException(409, '资料总结的原文已变化，请重新生成总结')
    elif kind == 'analysis':
        if row['kind'] != 'advanced_statistics':
            raise HTTPException(422, '仅支持复用已执行的统计分析')
        parameters = db.loads(row['parameters'], {})
        result = db.loads(row['result'], {})
        original = db.one('SELECT * FROM files WHERE id=? AND project_id=? AND active=1', (parameters.get('file_id'), pid))
        if not original or result.get('source_sha256') != original['sha256']:
            raise HTTPException(409, '统计结果与当前原始数据不匹配')
    return refs.digest([row,upstream])


def text(pid, kind, identity):
    if kind in adapters.EXTRA_TABLES:return adapters.text(pid,kind,identity)
    row = owned(pid, kind, identity)
    if kind == 'fact':
        return row['label'], row['statement'] + '\n研究状态：' + row['status'] + '\n核验状态：' + row['verification']
    if kind == 'analysis':
        return '统计分析 · ' + db.loads(row['parameters'], {}).get('method', ''), db.dumps(db.loads(row['result'], {}))
    data = db.loads(row['data'], {})
    original = db.one('SELECT name FROM files WHERE id=?', (identity,))
    lines = [data.get('overview', '')]
    for point in data.get('key_points', []):
        if isinstance(point, dict):
            lines.append(point.get('text', '') + ('（页码：' + ', '.join(map(str, point['pages'])) + '）' if point.get('pages') else ''))
    for topic in data.get('topics', []):
        if isinstance(topic, dict):
            lines.append(topic.get('title', ''))
            lines.extend(point.get('text', '') for point in topic.get('points', []) if isinstance(point, dict))
    return (original['name'] if original else '资料') + ' · 总结', '\n'.join(lines)
