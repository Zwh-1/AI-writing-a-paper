"""Targeted, version-checked cross-page insertions with durable consumption receipts."""
from fastapi import HTTPException
from . import db, security, documents, asset_service

@db.atomic
def create(pid, body):
    section=security.require_section(body.get('section_id',''))
    if section['project_id']!=pid:raise HTTPException(404,'目标章节不属于项目')
    asset=asset_service.get(pid,body.get('artifact_id',''))
    expected=body.get('base_revision_id',section['current_revision_id'])
    if expected!=section['current_revision_id']:raise HTTPException(409,'目标正文已更新')
    previous=db.one("SELECT * FROM workspace_intents WHERE project_id=? AND section_id=? AND artifact_id=? AND asset_version_id=? AND state='pending'",(pid,section['id'],asset['id'],asset['asset_version_id']))
    if previous:return previous
    identity=db.uid();stamp=db.now()
    db.execute('INSERT INTO workspace_intents VALUES(?,?,?,?,?,?,?,?,?,?)',(identity,pid,section['id'],expected,asset['id'],asset['asset_version_id'],'pending',None,stamp,stamp))
    return db.one('SELECT * FROM workspace_intents WHERE id=?',(identity,))


def pending(pid, section_id):
    section=security.require_section(section_id)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于项目')
    return [{**row,'artifact':asset_service.describe(pid,row['artifact_id'],asset_service.version(pid,row['artifact_id'],row['asset_version_id'])),'current':row['base_revision_id']==section['current_revision_id']} for row in db.rows("SELECT * FROM workspace_intents WHERE project_id=? AND section_id=? AND state='pending' ORDER BY created_at,id",(pid,section_id))]


@db.atomic
def finish(pid, identity, body):
    security.require_project(pid)
    row=db.one('SELECT * FROM workspace_intents WHERE id=? AND project_id=?',(identity,pid))
    if not row:raise HTTPException(404,'插入意图不存在')
    if row['state']!='pending':return row
    state='dismissed' if body.get('dismiss') else 'consumed';revision=None
    if state=='consumed':
        section=security.require_section(row['section_id']);revision=documents.current(section)
        if not revision or revision['id']!=body.get('revision_id'):raise HTTPException(409,'请先保存目标正文')
        if not any(n.get('attrs',{}).get('artifactId')==row['artifact_id'] and n.get('attrs',{}).get('assetVersionId')==row['asset_version_id'] for n in documents.nodes(db.loads(revision['document'],{}))):raise HTTPException(409,'目标正文尚未包含该资产版本')
    db.execute('UPDATE workspace_intents SET state=?,consumed_revision_id=?,updated_at=? WHERE id=?',(state,revision['id'] if revision else None,db.now(),identity))
    return db.one('SELECT * FROM workspace_intents WHERE id=?',(identity,))
