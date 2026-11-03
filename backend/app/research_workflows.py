"""Cross-feature commands preserve provenance without promoting notes to evidence."""
import copy
from . import db, security, documents, research_records as records, resource_refs as refs
from fastapi import HTTPException

@db.atomic
def annotation_note(pid, identity, section_id=None):
    annotation=records.get(pid,identity,'annotation');ref=refs.make(pid,'annotation',identity)
    previous=db.one("SELECT id FROM studio_records WHERE project_id=? AND kind='note' AND json_extract(data,'$.annotation_id')=? AND json_extract(data,'$.annotation_revision')=? AND deleted_at IS NULL",(pid,identity,annotation['revision']))
    if previous:return records.get(pid,previous['id'],'note')
    data={'title':annotation['data']['quote'][:60],'text':annotation['data'].get('note') or annotation['data']['quote'],
        'annotation_id':identity,'annotation_revision':annotation['revision'],'source_refs':[ref]}
    if section_id:data['section_id']=section_id
    return records.notes.save(pid,data)


def note_section(pid, identity, section_id, expected_revision):
    note=records.notes.get(pid,identity)
    return records.notes.save(pid,{**note['data'],'section_id':section_id},identity,expected_revision=expected_revision)


@db.atomic
def note_proposal(pid, identity, section_id, base_revision_id):
    records.notes.get(pid,identity)
    from .product_service import reuse
    return reuse(pid,{'sources':[refs.make(pid,'note',identity)],'target':'draft','section_id':section_id,'revision_id':base_revision_id})['record']


def search_derived(pid, query, limit=20):
    security.require_project(pid)
    if not isinstance(query,str) or not query.strip() or len(query)>5000:raise HTTPException(422,'搜索词无效')
    result=[]
    for row in db.rows("SELECT * FROM studio_records WHERE project_id=? AND kind IN ('note','translation','annotation','guide','message','webpage') AND deleted_at IS NULL ORDER BY updated_at DESC",(pid,)):
        data=db.loads(row['data'],{})
        if query.casefold() not in db.dumps(data).casefold():continue
        value=records.get(pid,row['id'],row['kind'])
        result.append({**value,'search_scope':'derived','semantic_support':'not_checked'})
        if len(result)>=min(100,max(1,limit)):break
    return {'items':result,'scope':'derived','evidence_promoted':False}


def selection_evidence(pid, fid, body):
    from .content_view import file_units
    _,parse_id,units=file_units(pid,fid)
    unit=next((u for u in units if u['id']==body.get('unit_id')),None)
    start,end=body.get('start'),body.get('end')
    if not unit or parse_id!=body.get('parse_id') or type(start)is not int or type(end)is not int or not 0<=start<end<=len(unit['text']) or unit['text'][start:end]!=body.get('quote'):
        raise HTTPException(409,'原文版本或选区已变化')
    from .writing_agent.evidence import create
    return create(pid,{'kind':'literature','origin_type':'knowledge_unit','origin_id':unit['id'],'locator':{'start':start,'end':end,'before':0,'after':0},'title':body.get('title') or unit['text'][start:end][:80]})
