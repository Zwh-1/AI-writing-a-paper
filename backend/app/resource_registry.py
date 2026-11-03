"""Declared resource ownership and relationship validation for transfer adapters."""
from . import db
from .product_adapters import EXTRA_TABLES

RESOURCE_TABLES={'file':'files','source':'sources','unit':'knowledge_units','reference':'bibliography','revision':'revisions','asset':'artifacts',
    'entity':'kg_entities','entity_version':'kg_entity_versions','assertion':'kg_assertions','wiki_page':'wiki_pages','wiki_version':'wiki_versions','wiki_block':'wiki_blocks',
    'evidence':'evidence_items','observation':'knowledge_observations','summary':'material_summaries','fact':'facts','analysis':'advanced_runs',**EXTRA_TABLES}
RELATION_TABLES=('research_record_owners','research_links','research_record_states','asset_versions','workspace_intents','research_sync_history')

def validate_relations(pid,tables):
    from .resource_refs import RECORD_TYPES
    projects={r['id']:r for r in tables.get('projects',[])}
    if pid not in projects:raise ValueError('资源包缺少项目')
    records={r['id']:r for r in tables.get('studio_records',[])}
    ids={name:{r.get('id',r.get('claim_version_id',r.get('file_id'))) for r in rows} for name,rows in tables.items()}
    for link in tables.get('research_links',[]):
        if link['record_id'] not in records:raise ValueError('派生关系缺少研究记录')
        table='studio_records' if link['target_type'] in RECORD_TYPES else RESOURCE_TABLES.get(link['target_type'])
        if not table or link['target_id'] not in ids.get(table,set()):raise ValueError('派生关系引用了包外对象')
        if link['target_type'] in RECORD_TYPES and records[link['target_id']]['kind']!=link['target_type']:raise ValueError('派生关系类型不匹配')
    for owner in tables.get('research_record_owners',[]):
        if owner['record_id'] not in records:raise ValueError('所属关系缺少记录')
        for column,table in (('file_id','files'),('reference_id','bibliography'),('section_id','sections'),('conversation_id','studio_records')):
            if owner.get(column) and owner[column] not in ids.get(table,set()):raise ValueError('记录所属关系不完整')
    for row in tables.get('workspace_intents',[]):
        if row['section_id'] not in ids.get('sections',set()) or row['artifact_id'] not in ids.get('artifacts',set()) or row['asset_version_id'] not in ids.get('asset_versions',set()):raise ValueError('跨页面意图缺少目标资源')
    return {'research_links':len(tables.get('research_links',[])),'asset_versions':len(tables.get('asset_versions',[])),'relations_verified':True}


def rebase_research(pid):
    """Rebase identity-sensitive active links in an isolated fork; leave historical data intact."""
    from . import resource_refs as refs
    rows=db.rows("SELECT * FROM studio_records WHERE project_id=? AND deleted_at IS NULL ORDER BY created_at,id",(pid,))
    pending={r['id']:r for r in rows};done=set()
    for _ in range(len(rows)+1):
        progressed=False
        for identity,row in list(pending.items()):
            links=db.rows('SELECT * FROM research_links WHERE record_id=? AND record_revision=?',(identity,row['revision']))
            if any(link['target_type'] in refs.RECORD_TYPES and link['target_id'] in pending for link in links):continue
            rebased=[];stale=False
            for link in links:
                source=db.loads(link['source_json'],{})
                try:version=refs.version_of(pid,link['target_type'],link['target_id'])
                except Exception:stale=True;continue
                source.update(version=version,project_id=pid)
                db.execute('UPDATE research_links SET target_version=?,source_json=? WHERE id=?',(version,db.dumps(source),link['id']))
                rebased.append(source)
            data=db.loads(row['data'],{})
            if 'source_refs' in data:data['source_refs']=rebased
            for citation in data.get('citations',[]):
                if citation.get('source_ref'):
                    ref=citation['source_ref']
                    try:ref.update(version=refs.version_of(pid,ref['type'],ref['id']),project_id=pid)
                    except Exception:stale=True
            if row['kind']=='proposal':stale=True
            db.execute('UPDATE studio_records SET data=? WHERE id=?',(db.dumps(data),identity))
            old=db.one('SELECT state FROM research_record_states WHERE record_id=?',(identity,))
            if stale:db.execute("UPDATE research_record_states SET state='stale',note='分叉后需重新生成或核对来源' WHERE record_id=?",(identity,))
            # A fork does not turn unknown/stale imports into reviewed or current evidence.
            done.add(identity);del pending[identity];progressed=True
        if not progressed:break
    for identity in pending:db.execute("UPDATE research_record_states SET state='stale',note='导入来源有循环或依赖未就绪' WHERE record_id=?",(identity,))
    return {'rebased':len(done),'unresolved':len(pending)}
