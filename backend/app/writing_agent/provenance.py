"""Check original permissions before sending derived text."""
from .. import db,security,documents
from .common import digest,fail

def revision_permissions(pid,rid,_visited=None):
    visited=set() if _visited is None else _visited
    if rid in visited:return digest({'previously_checked_revision':rid})
    visited.add(rid)
    from .evidence import allowed_remote,permission_stamp
    r=db.one('SELECT r.* FROM revisions r JOIN sections s ON s.id=r.section_id WHERE r.id=? AND s.project_id=?',(rid,pid))
    if not r:fail('SOURCE_ACCESS_REVOKED','派生正文版本不存在',403)
    files=set();stamps=[]
    def source(sid):
        row=db.one('SELECT file_id FROM sources WHERE id=?',(sid,))
        if not row or not security.file_allows_remote(row['file_id'],pid):fail('SOURCE_ACCESS_REVOKED','正文的原始资料禁止外发',403)
        files.add(row['file_id'])
    def artifact(aid):
        a=db.one('SELECT * FROM artifacts WHERE id=? AND project_id=?',(aid,pid))
        if not a:fail('SOURCE_ACCESS_REVOKED','正文中的研究产物不存在',403)
        meta=db.loads(a['metadata'],{})
        from ..resource_refs import validate
        validate(pid,meta.get('source_refs',[]),remote=True)
        if meta.get('egress','local_only')=='local_only':fail('SOURCE_ACCESS_REVOKED','正文中的研究产物禁止外发',403)
        if meta.get('source_file_id'):
            if not security.file_allows_remote(meta['source_file_id'],pid):fail('SOURCE_ACCESS_REVOKED','研究产物原件禁止外发',403)
            files.add(meta['source_file_id'])
        for sid in meta.get('source_ids',[]):source(sid)
        for eid in meta.get('evidence_ids',[]):
            if not allowed_remote(pid,eid):fail('SOURCE_ACCESS_REVOKED','图表的项目证据来源禁止外发',403)
            stamps.append(permission_stamp(pid,eid))
        for source_revision in meta.get('source_revision_ids',[]):
            stamps.append(revision_permissions(pid,source_revision,visited))
    for dep in db.rows('SELECT * FROM dependencies WHERE revision_id=?',(rid,)):
        if dep['upstream_type'].startswith('knowledge:'):
            from ..resource_refs import validate
            ref={'type':dep['upstream_type'][len('knowledge:'):],'id':dep['upstream_id'],'version':dep['upstream_version']}
            validate(pid,[ref],remote=True);stamps.append(ref)
        if dep['upstream_type']=='document_reading':
            from ..document_reading import context as reading_context
            reading=reading_context(pid,dep['upstream_id'],remote=True);files.add(reading['file_id']);stamps.append(reading['version'])
        if dep['upstream_type']=='source':source(dep['upstream_id'])
        if dep['upstream_type']=='knowledge_unit':
            from ..knowledge_units import get as get_unit
            unit=get_unit(pid,dep['upstream_id'],remote=True);files.add(unit['file_id']);stamps.append(digest([unit['parse_id'],unit['content_hash'],unit['current']]))
        if dep['upstream_type']=='fact':
            f=db.one('SELECT * FROM facts WHERE id=? AND project_id=?',(dep['upstream_id'],pid));version=str(dep['upstream_version'])
            if not f:fail('SOURCE_ACCESS_REVOKED','正文依据事实不存在',403)
            if str(f['revision'])!=version:
                history=[db.loads(x['content'],{}) for x in db.rows('SELECT content FROM fact_revisions WHERE fact_id=?',(f['id'],))]
                f=next((x for x in history if str(x.get('revision'))==version),None)
                if not f:fail('SOURCE_ACCESS_REVOKED','无法确认历史事实的原始权限',403)
            ids=db.loads(f.get('source_ids'),[]) if isinstance(f.get('source_ids'),str) else f.get('source_ids',[])
            for sid in ids:source(sid)
            if f.get('artifact_id'):artifact(f['artifact_id'])
    for cite in db.rows('SELECT source_ids FROM citations WHERE revision_id=?',(rid,)):
        for sid in db.loads(cite['source_ids'],[]):source(sid)
    for n in documents.nodes(db.loads(r['document'],{})):
        if n.get('type')=='figure' or n.get('type')=='table' and n.get('attrs',{}).get('artifactId'):artifact(n.get('attrs',{}).get('artifactId'))
    for d in db.rows("SELECT upstream_id FROM object_dependencies WHERE project_id=? AND downstream_type='revision' AND downstream_id=? AND upstream_type='evidence'",(pid,rid)):
        if not allowed_remote(pid,d['upstream_id']):fail('SOURCE_ACCESS_REVOKED','正文的代码/证据派生来源禁止外发',403)
        stamps.append(permission_stamp(pid,d['upstream_id']))
    for fid in sorted(files):stamps.append(digest(db.one('SELECT sha256,active,egress FROM files WHERE id=?',(fid,))))
    return digest(stamps)
