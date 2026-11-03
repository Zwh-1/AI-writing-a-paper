"""Explicit owner foreign keys and versioned links alongside legacy JSON histories."""
from fastapi import HTTPException
from . import db, resource_refs as refs

def bind(pid, record, legacy=False):
    kind=record['kind'];data=record['data'];owner=record['owner_id'];identity=record['id']
    source_refs=list(data.get('source_refs',[]));unknown=False;column=None
    db.execute('DELETE FROM research_record_owners WHERE record_id=?',(identity,))
    if owner:
        if kind in ('message',):column='conversation_id';table='studio_records'
        elif kind in ('comment','proposal'):column='section_id';table='sections'
        elif kind=='translation' and data.get('reference_id'):column='reference_id';table='bibliography'
        elif kind in ('translation','annotation','guide','webpage','parse_result'):column='file_id';table='files'
        else:raise HTTPException(422,'该类型不能使用未分类的所属对象')
        row=db.one('SELECT * FROM '+table+' WHERE id=? AND project_id=?',(owner,pid))
        if not row or column=='conversation_id' and row['kind']!='conversation':raise HTTPException(422,'所属对象不存在、类型错误或跨项目')
        db.execute('INSERT OR REPLACE INTO research_record_owners(record_id,project_id,'+column+') VALUES(?,?,?)',(identity,pid,owner))
        if not legacy and column in ('file_id','reference_id'):
            source_refs.append(refs.make(pid,'file' if column=='file_id' else 'reference',owner))
    if not legacy:
        for field,typ in (('unit_id','unit'),('message_id','message'),('annotation_id','annotation'),('base_revision_id','revision'),('source_revision_id','revision'),('revision_id','revision'),('parent_id','comment')):
            if data.get(field):
                try:source_refs.append(refs.make(pid,typ,data[field]))
                except HTTPException:
                    if field!='unit_id':raise
                    unknown=True
        for citation in data.get('citations',[]):
            if citation.get('source_ref'):source_refs.append(citation['source_ref'])
            else:unknown=True
    if data.get('section_id'):
        section=db.one('SELECT id FROM sections WHERE id=? AND project_id=?',(data['section_id'],pid))
        if not section:raise HTTPException(422,'笔记关联章节不属于当前项目')
        if kind=='note' and not owner:
            db.execute('INSERT INTO research_record_owners(record_id,project_id,section_id) VALUES(?,?,?)',(identity,pid,data['section_id']))
    current=refs.validate(pid,source_refs,allow_stale=True) if source_refs else True
    db.execute('DELETE FROM research_links WHERE record_id=? AND record_revision=?',(identity,record['revision']))
    for ref in source_refs:
        db.execute('INSERT OR IGNORE INTO research_links VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),pid,identity,record['revision'],'derived_from',ref['type'],ref['id'],ref['version'],db.dumps(ref)))
    state='unknown' if legacy or unknown else 'current' if current else 'stale'
    db.execute('INSERT INTO research_record_states VALUES(?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET state=excluded.state,note=excluded.note',(identity,pid,state,'旧记录未推断来源' if legacy else ''))
    return source_refs


def hydrate(pid, record):
    state=db.one('SELECT state,note FROM research_record_states WHERE record_id=?',(record['id'],))
    links=db.rows('SELECT source_json FROM research_links WHERE record_id=? AND record_revision=?',(record['id'],record['revision']))
    source_refs=[db.loads(row['source_json'],{}) for row in links]
    effective_state=state['state'] if state else 'unknown'
    if effective_state=='current' and source_refs:
        try:
            if not refs.validate(pid,source_refs,allow_stale=True):effective_state='stale'
        except HTTPException:effective_state='stale'
    return {**record,'resource_type':'research_record','schema_version':record.get('schema_version',1),
        'classification':'personal_note' if record['kind']=='note' else 'unverified_derived' if record['kind'] in ('message','translation','guide','webpage','proposal','parse_result') else 'user_authored',
        'source_refs':source_refs,'source_state':effective_state}


@db.atomic
def backfill(pid):
    migrated=0
    for row in db.rows('SELECT r.* FROM studio_records r LEFT JOIN research_record_states s ON s.record_id=r.id WHERE r.project_id=? AND s.record_id IS NULL',(pid,)):
        row={**row,'data':db.loads(row['data'],{})}
        try:bind(pid,row,legacy=True)
        except HTTPException:
            db.execute("INSERT OR IGNORE INTO research_record_states VALUES(?,?,'unknown','旧所属关系无法核实')",(row['id'],pid))
        migrated+=1
    return {'migrated':migrated,'identities_preserved':True}
