"""Bounded, project-scoped direct dataflow over existing provenance records."""
from fastapi import HTTPException
from . import db,security,resource_refs as refs


def lookup(pid,source,limit=60):
    security.require_project(pid)
    if type(limit)is not int or not 1<=limit<=200:raise HTTPException(422,'关系查询上限须为1至200')
    current=refs.validate(pid,[source],allow_stale=True)
    kind,identity=source['type'],source['id'];edges=[];seen=set();truncated=False
    def add(direction,relation,typ,uid,expected=None,**details):
        nonlocal truncated
        key=(direction,relation,typ,uid,details.get('intent_id'))
        if key in seen:return
        seen.add(key)
        if len(edges)>=limit:truncated=True;return
        try:
            from .product_service import item
            value=item(pid,typ,uid)
            edge={key:value[key] for key in ('title','source_ref','state','reason','open_location') if key in value}
        except HTTPException:
            edge={'title':typ+' · '+uid,'source_ref':None,'state':'unavailable','reason':'历史对象、未就绪对象或已归档内容'}
        edges.append({**edge,'direction':direction,'relation':relation,'type':typ,'id':uid,
                      'expected_version':expected,**details})
    def origin(ref):add('upstream','derived_from',ref['type'],ref['id'],ref.get('version'))
    def live_origin(typ,uid):
        try:origin(refs.make(pid,typ,uid))
        except HTTPException:add('upstream','derived_from',typ,uid)
    if kind in refs.RECORD_TYPES:
        row=db.one('SELECT revision FROM studio_records WHERE id=? AND project_id=?',(identity,pid))
        for link in db.rows('SELECT * FROM research_links WHERE project_id=? AND record_id=? AND record_revision=? ORDER BY id LIMIT ?',(pid,identity,row['revision'],limit+1)):
            origin(db.loads(link['source_json'],{}))
    elif kind=='asset':
        row=db.one('SELECT metadata FROM artifacts WHERE id=? AND project_id=?',(identity,pid));meta=db.loads(row['metadata'],{})
        for ref in meta.get('source_refs',[]):origin(ref)
        for fid in meta.get('source_file_ids',[]):live_origin('file',fid)
        if meta.get('source_file_id'):live_origin('file',meta['source_file_id'])
        if meta.get('studio_canvas_id'):live_origin('canvas',meta['studio_canvas_id'])
        if meta.get('parent_artifact_id'):live_origin('asset',meta['parent_artifact_id'])
    elif kind=='revision':
        for dep in db.rows("SELECT * FROM dependencies WHERE project_id=? AND revision_id=? AND upstream_type LIKE 'knowledge:%' ORDER BY id LIMIT ?",(pid,identity,limit+1)):
            add('upstream','derived_from',dep['upstream_type'][10:],dep['upstream_id'],dep['upstream_version'])
    else:
        from .product_sources import TABLES,owned,origins
        if kind in TABLES:
            for typ,uid in origins(pid,kind,owned(pid,kind,identity)):live_origin(typ,uid)
        elif kind=='source':
            row=db.one('SELECT file_id FROM sources WHERE id=?',(identity,));live_origin('file',row['file_id'])
        elif kind=='unit':
            row=db.one('SELECT p.file_id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=?',(identity,));live_origin('file',row['file_id'])
        else:
            from .knowledge_lineage import references
            for ref in references(pid,kind,identity):origin(ref)
    for link in db.rows('SELECT l.*,r.kind FROM research_links l JOIN studio_records r ON r.id=l.record_id AND r.revision=l.record_revision WHERE l.project_id=? AND l.target_type=? AND l.target_id=? AND r.deleted_at IS NULL ORDER BY l.id LIMIT ?',(pid,kind,identity,limit+1)):
        add('downstream','derived_from',link['kind'],link['record_id'],link['target_version'])
    for row in db.rows("SELECT DISTINCT a.id FROM artifacts a,json_each(a.metadata,'$.source_refs') r WHERE a.project_id=? AND json_extract(r.value,'$.type')=? AND json_extract(r.value,'$.id')=? ORDER BY a.created_at,a.id LIMIT ?",(pid,kind,identity,limit+1)):
        add('downstream','derived_from','asset',row['id'])
    for row in db.rows("SELECT DISTINCT t.id FROM tasks t,json_each(t.payload,'$.product_refs') r WHERE t.project_id=? AND t.kind='studio' AND t.status='succeeded' AND json_extract(t.payload,'$.action')='execute' AND json_extract(r.value,'$.type')=? AND json_extract(r.value,'$.id')=? ORDER BY t.created_at,t.id LIMIT ?",(pid,kind,identity,limit+1)):
        add('downstream','execution_input','execution',row['id'])
    if kind=='canvas':
        for row in db.rows("SELECT id FROM artifacts WHERE project_id=? AND json_extract(metadata,'$.studio_canvas_id')=? ORDER BY created_at,id LIMIT ?",(pid,identity,limit+1)):
            add('downstream','rendered_from','asset',row['id'])
    for row in db.rows("SELECT DISTINCT d.revision_id FROM dependencies d JOIN sections s ON s.id=d.section_id AND s.current_revision_id=d.revision_id WHERE d.project_id=? AND d.upstream_type=? AND d.upstream_id=? AND s.state!='archived' ORDER BY d.revision_id LIMIT ?",(pid,'knowledge:'+kind,identity,limit+1)):
        add('downstream','used_in_current_document','revision',row['revision_id'])
    if kind=='asset':
        for row in db.rows("SELECT DISTINCT r.id FROM sections s JOIN revisions r ON r.id=s.current_revision_id,json_tree(r.document,'$.content') n WHERE s.project_id=? AND s.state!='archived' AND n.type='object' AND json_extract(CASE WHEN n.type='object' THEN n.value ELSE '{}' END,'$.attrs.artifactId')=? ORDER BY r.id LIMIT ?",(pid,identity,limit+1)):
            add('downstream','inserted_in_current_document','revision',row['id'])
        for row in db.rows("SELECT id,section_id,state FROM workspace_intents WHERE project_id=? AND artifact_id=? ORDER BY created_at,id LIMIT ?",(pid,identity,limit+1)):
            add('downstream','insertion_'+row['state'],'outline',row['section_id'],intent_id=row['id'])
    return {'source':source,'selected_version_current':current,'scope':'current_head_direct_relations','items':edges,'truncated':truncated,'limit':limit}
