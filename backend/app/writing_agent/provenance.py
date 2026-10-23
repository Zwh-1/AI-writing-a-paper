"""Check original permissions before sending derived text."""
from .. import db,security,documents
from .common import digest,fail

def revision_permissions(pid,rid):
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
        if meta.get('egress','local_only')=='local_only':fail('SOURCE_ACCESS_REVOKED','正文中的研究产物禁止外发',403)
        if meta.get('source_file_id'):
            if not security.file_allows_remote(meta['source_file_id'],pid):fail('SOURCE_ACCESS_REVOKED','研究产物原件禁止外发',403)
            files.add(meta['source_file_id'])
        for sid in meta.get('source_ids',[]):source(sid)
    for dep in db.rows('SELECT * FROM dependencies WHERE revision_id=?',(rid,)):
        if dep['upstream_type']=='source':source(dep['upstream_id'])
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
        if n.get('type')=='figure':artifact(n.get('attrs',{}).get('artifactId'))
    for d in db.rows("SELECT upstream_id FROM object_dependencies WHERE project_id=? AND downstream_type='revision' AND downstream_id=? AND upstream_type='evidence'",(pid,rid)):
        if not allowed_remote(pid,d['upstream_id']):fail('SOURCE_ACCESS_REVOKED','正文的代码/证据派生来源禁止外发',403)
        stamps.append(permission_stamp(pid,d['upstream_id']))
    for fid in sorted(files):stamps.append(digest(db.one('SELECT sha256,active,egress FROM files WHERE id=?',(fid,))))
    return digest(stamps)
