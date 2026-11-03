"""Application-level draft commits shared by assistant, notes and editor operations."""
from . import db, documents, security, resource_refs
from fastapi import HTTPException

@db.atomic
def commit(pid, section_id, document, base_revision_id, source_refs=(), origin='assistant_draft', task_id=None, key=None):
    from .writing_agent.adoption import commit_document, receipt, save_receipt
    request_hash=resource_refs.digest([section_id,document,base_revision_id,list(source_refs),origin])
    if key:
        previous=receipt(pid,key,request_hash)
        if previous:return previous
    section=security.require_section(section_id)
    if section['project_id']!=pid:raise HTTPException(404,'章节不属于当前项目')
    if section['current_revision_id']!=base_revision_id:raise HTTPException(409,'正文已更新，请重新读取')
    resource_refs.validate(pid,list(source_refs))
    saved,analysis,_=commit_document(pid,section,document,{}, {'base_revision_id':base_revision_id,'accept_policy':'draft'},kind=origin,task_id=task_id)
    for ref in source_refs:
        db.execute('INSERT INTO dependencies VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,'knowledge:'+ref['type'],ref['id'],ref['version'],section_id,saved['id']))
    result={**saved,'analysis_id':analysis['id'],'review_state':'unverified'}
    if key:save_receipt(pid,key,request_hash,origin,task_id or saved['id'],result)
    return result


def editor_context(pid, section_id):
    from .assistant_context import section
    row,current,document,paragraphs,permission=section(pid,section_id)
    return {'project_id':pid,'section_id':row['id'],'revision_id':current['id'],'document':document,'paragraphs':paragraphs,'permission_stamp':permission}
