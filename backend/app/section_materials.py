"""Chapter input collection shared by editors, both writers and adoption guards."""
from fastapi import HTTPException
from . import db, security, resource_refs as refs, research_records as records


def rows(pid, sid):
    security.require_project(pid)
    section=security.require_section(sid)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于当前项目')
    return db.rows("SELECT * FROM studio_records WHERE project_id=? AND kind='note' AND deleted_at IS NULL AND json_extract(data,'$.section_id')=? AND json_extract(data,'$.use_for_writing')=1 ORDER BY created_at,id",(pid,sid))


def list_records(pid,sid,query=''):
    return [records.unpack(row) for row in rows(pid,sid) if not query or query.casefold() in row['data'].casefold()]


def snapshot(pid,sid,remote=False):
    from .product_service import selection
    current=rows(pid,sid)
    sources=[refs.make(pid,'note',row['id']) for row in current]
    inputs=selection(pid,sources,remote) if sources else []
    value={'project_id':pid,'section_id':sid,'record_heads':[[row['id'],row['revision']] for row in current],'source_refs':sources}
    return inputs,{**value,'fingerprint':refs.digest(value)}


def validate(pid,snapshot,remote=False):
    # Absence keeps historical candidates and old clients compatible.
    if snapshot is None:return
    if not isinstance(snapshot,dict) or snapshot.get('project_id')!=pid or not isinstance(snapshot.get('section_id'),str):
        raise HTTPException(422,'章节材料快照无效')
    current=rows(pid,snapshot['section_id'])
    if [[row['id'],row['revision']] for row in current]!=snapshot.get('record_heads'):
        raise HTTPException(409,'章节写作材料已新增、移除或修改，请重新生成候选')
    refs.validate(pid,snapshot.get('source_refs',[]),remote)
