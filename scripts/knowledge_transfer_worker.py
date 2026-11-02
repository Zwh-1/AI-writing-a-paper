"""Validate and restore one complete knowledge package in an isolated directory."""
import hashlib,json,os,runpy,sqlite3,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];runpy.run_path(str(ROOT/'runner.py'),run_name='runtime_setup')
from app import db,security,config,knowledge_lineage as lineage,knowledge_units as units
from app.knowledge_transfer import decode,SETTING_PREFIXES

def remap(value,mapping):
    if not mapping:return value
    if isinstance(value,str):
        if value in mapping:return mapping[value]
        if value[:1] in ('{','['):
            try:return db.dumps(remap(json.loads(value),mapping))
            except ValueError:pass
        return value
    if isinstance(value,list):return [remap(v,mapping) for v in value]
    if isinstance(value,dict):return {mapping.get(k,k):remap(v,mapping) for k,v in value.items()}
    return value

def rebase(pid):
    """Recompute identity-sensitive current versions; preserve original archive.

    Old parses, excluded objects and stale knowledge remain historical. Copied
    assessments retain their reviewers; this operation never creates a review.
    """
    from app.writing_agent import evidence
    for head in db.rows('SELECT p.*,f.id file FROM knowledge_heads h JOIN knowledge_parses p ON p.id=h.parse_id JOIN files f ON f.id=p.file_id WHERE p.project_id=?',(pid,)):
        original=db.one('SELECT * FROM files WHERE id=?',(head['file'],))
        db.execute('UPDATE knowledge_parses SET input_hash=? WHERE id=?',(units.input_stamp(original),head['id']))
    from app.document_reading import task_hash
    for reading in db.rows('SELECT * FROM document_readings WHERE project_id=?',(pid,)):
        db.execute('UPDATE document_readings SET task_scope_hash=? WHERE id=?',(task_hash(pid,reading['section_id']),reading['id']))
    for item in db.rows('SELECT * FROM evidence_items WHERE project_id=?',(pid,)):
        if item['origin_type'] in ('knowledge_unit','document_reading','source','artifact','fact'):
            try:
                version=evidence.current_version(item)
                if version:db.execute('UPDATE evidence_items SET origin_version=? WHERE id=?',(version,item['id']))
            except Exception:pass
    done={};visiting=set()
    def version(kind,identity):
        key=(kind,identity)
        if key in done:return done[key]
        if key in visiting:raise ValueError('导入来源依赖有循环')
        visiting.add(key)
        try:
            if kind=='file':value=lineage.file_stamp(pid,identity)[2]
            elif kind=='unit':value=db.one('SELECT content_hash FROM knowledge_units WHERE id=?',(identity,))['content_hash']
            elif kind=='evidence':value=db.one('SELECT origin_version FROM evidence_items WHERE id=?',(identity,))['origin_version']
            else:
                row=lineage.owned(pid,kind,identity)
                refs=lineage.references(pid,kind,identity)
                for ref in refs:
                    fresh=version(ref['type'],ref['id'])
                    db.execute('UPDATE knowledge_dependencies SET upstream_version=? WHERE project_id=? AND downstream_type=? AND downstream_id=? AND upstream_type=? AND upstream_id=?',(fresh,pid,kind,identity,ref['type'],ref['id']))
                    if kind=='wiki_block':db.execute('UPDATE wiki_links SET target_version=? WHERE block_id=? AND target_type=? AND target_id=?',(fresh,identity,ref['type'],ref['id']))
                if kind=='observation':
                    meta=db.loads(row['metadata_json'],{});stamp=version('file',row['file_id']) if row['file_id'] else version('evidence',row['evidence_id'])
                    value=lineage.digest([stamp,row['unit_id'],meta.get('char_start'),meta.get('char_end'),row['text'],meta])
                    db.execute('UPDATE knowledge_observations SET source_version=?,content_hash=? WHERE id=?',(stamp,value,identity))
                elif kind=='entity':value=version('entity_version',row['current_version_id'])
                elif kind=='entity_version':
                    value=lineage.digest([row['name'],row['description'],lineage.references(pid,kind,identity)])
                    db.execute('UPDATE kg_entity_versions SET content_hash=? WHERE id=?',(value,identity))
                elif kind=='assertion':
                    for assessment in db.rows('SELECT * FROM kg_support_assessments WHERE claim_version_id=?',(identity,)):
                        typ,target=('observation',assessment['observation_id']) if assessment['observation_id'] else ('evidence',assessment['evidence_id'])
                        db.execute('UPDATE kg_support_assessments SET source_version=? WHERE id=?',(version(typ,target),assessment['id']))
                    content=lineage.digest([row['namespace'],row['subject_id'],row['predicate'],row['object_id'],db.loads(row['object_json'],{}),
                        db.loads(db.one('SELECT proposition_json FROM claim_versions WHERE id=?',(identity,))['proposition_json'],{}),lineage.references(pid,kind,identity)])
                    db.execute('UPDATE kg_assertions SET content_hash=? WHERE claim_version_id=?',(content,identity))
                    checks=db.rows('SELECT id,support,method,source_version FROM kg_support_assessments WHERE claim_version_id=? ORDER BY created_at,id',(identity,))
                    decisions=db.rows('SELECT id,assessment_fingerprint,support FROM kg_support_decisions WHERE claim_version_id=? ORDER BY created_at,id',(identity,))
                    # Imported adjudications remain in history but require a new
                    # explicit decision if the assessment fingerprint changed.
                    value=lineage.digest([content,checks,decisions]) if decisions else lineage.digest([content,checks]) if checks else content
                elif kind=='wiki_block':
                    for ref in lineage.references(pid,kind,identity):db.execute('UPDATE wiki_links SET target_version=? WHERE block_id=? AND target_type=? AND target_id=?',(version(ref['type'],ref['id']),identity,ref['type'],ref['id']))
                    value=lineage.block_hash(pid,row)
                elif kind=='wiki_page':value=version('wiki_version',row['current_version_id']) if row['current_version_id'] else ''
                elif kind=='wiki_version':
                    blocks=db.rows('SELECT * FROM wiki_blocks WHERE version_id=? ORDER BY position',(identity,));clean=[]
                    for b in blocks:
                        version('wiki_block',b['id'])
                        clean.append({'key':b['block_key'],'kind':b['kind'],'text':b['text'],'sources':lineage.references(pid,'wiki_block',b['id']),'author':b['author'],'metadata':db.loads(b['metadata_json'],{})})
                    value=lineage.digest(clean);db.execute('UPDATE wiki_versions SET content_hash=? WHERE id=?',(value,identity))
                else:value=row.get('content_hash',identity)
                db.execute('UPDATE knowledge_dependencies SET downstream_version=? WHERE project_id=? AND downstream_type=? AND downstream_id=?',(value,pid,kind,identity))
            done[key]=value;return value
        finally:visiting.remove(key)
    for kind,table,column in [('observation','knowledge_observations','id'),('entity_version','kg_entity_versions','id'),('assertion','kg_assertions','claim_version_id'),('wiki_version','wiki_versions','id')]:
        for row in db.rows('SELECT '+column+' AS identity FROM '+table+' WHERE project_id=?',(pid,)):
            try:version(kind,row['identity'])
            except Exception:continue
    # Physical Parquet/code index mappings belong to the original identity space.
    # Keep the bytes and original manifests, while forcing an explicit rebuild.
    db.execute("UPDATE knowledge_artifacts SET state='stale' WHERE project_id=? AND kind='graphrag_index'",(pid,))
    for key in ('knowledge_release:','knowledge_release_locked:'):db.set_setting(key+pid,None if key=='knowledge_release:' else False)

def main():
    archive=Path(sys.argv[1]);mode=sys.argv[2];title=sys.argv[3] if len(sys.argv)>3 else ''
    db.init()
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('manifest.json'));old_pid=manifest['project_id']
        tables={name:decode(json.loads(z.read('tables/'+name+'.json'))) for name in manifest['tables']}
        mapping={}
        if mode=='fork':
            for rows in tables.values():
                for row in rows:
                    for key in ('id','claim_version_id'):
                        if isinstance(row.get(key),str):mapping.setdefault(row[key],db.uid())
            mapping.setdefault(old_pid,db.uid())
        pid=mapping.get(old_pid,old_pid)
        for entry,expected in manifest['files'].items():
            if entry.startswith('objects/'):
                raw=z.read(entry)
                if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('原件哈希错误')
                security.put_blob(raw)
        # Rebase JSON payload blobs to the imported IDs; originals remain in ZIP.
        blobmap={}
        if mode=='fork':
            original_blobs={r['sha256'] for r in tables.get('files',[])}|{r['sha256'] for r in tables.get('artifacts',[]) if r.get('sha256')}
            for entry in manifest['files']:
                if not entry.startswith('objects/'):continue
                if entry.split('/')[-1] in original_blobs:continue
                raw=z.read(entry)
                try:
                    value=json.loads(raw);rewritten=remap(value,mapping)
                    if rewritten==value:continue
                    fresh=db.dumps(rewritten).encode()
                except (ValueError,UnicodeError):continue
                if fresh!=raw:blobmap[entry.split('/')[-1]]=security.put_blob(fresh)
        combined={**mapping,**blobmap}
        settings=remap(manifest.get('settings',{}),combined)
        for key,value in list(settings.items()):
            if old_pid in key:settings[key.replace(old_pid,pid)]=settings.pop(key)
        with db.transaction() as c:
            c.execute('PRAGMA defer_foreign_keys=ON')
            for table,rows in tables.items():
                for original in rows:
                    row=remap(original,combined)
                    if table in ('sources','pages','knowledge_units','knowledge_observations','wiki_blocks'):
                        for field in ('text','normalized','notes'):
                            if field in original:row[field]=original[field]
                    if table=='projects' and title:row['title']=title[:500]
                    if table=='repository_bindings':row.update(root_path_local='',egress_policy='local_only',scope_revision=row['scope_revision']+1)
                    if table=='tasks' and row['status'] in ('queued','running'):row['status']='incomplete';row['error']='从完整知识包恢复，未自动重复执行'
                    if table=='knowledge_maintenance_items' and row['status']=='running':row['status']='queued'
                    if table=='knowledge_artifacts':
                        info=manifest.get('artifacts',{}).get(original['id'],{});resource=config.DATA/'knowledge'/'imported'/row['id']
                        if info.get('available'):
                            kind=row['kind']
                            if kind=='graphrag_index':resource=config.DATA/'knowledge'/'graphrag'/row['id']
                            elif kind in ('knowledge_answer','semantic_maintenance'):resource=config.DATA/'knowledge'/('answers' if kind=='knowledge_answer' else 'maintenance')
                            elif kind in ('knowledge_export','knowledge_bundle'):resource=config.DATA/'exports'/'knowledge'
                            resource.mkdir(parents=True,exist_ok=True);saved=[]
                            for entry in info['entries']:
                                relative=Path(*Path(entry).parts[2:]);destination=resource/relative if info['directory'] else resource/(row['id']+Path(entry).suffix)
                                destination.parent.mkdir(parents=True,exist_ok=True);content=z.read(entry)
                                if mode=='fork' and destination.suffix=='.json':
                                    try:content=db.dumps(remap(json.loads(content),combined)).encode()
                                    except (ValueError,UnicodeError):pass
                                destination.write_bytes(content);saved.append(destination)
                            row['path']=str(resource if info['directory'] else saved[0])
                            meta=db.loads(row['manifest_json'],{});meta.get('packet',{}).update(folder=row['path']);row['manifest_json']=db.dumps(meta)
                        else:row['state']='stale';row['path']=str(resource)
                    columns=list(row);verb='INSERT OR REPLACE' if table=='project_change_clock' else 'INSERT'
                    c.execute(verb+' INTO "'+table+'"('+','.join('"'+key+'"' for key in columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row[k] for k in columns))
            for key,value in settings.items():db.set_setting(key,value)
            db.set_setting('sharing:'+pid,False)
            if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('知识包包含不完整或跨项目的来源引用')
        if mode=='fork':
            # Only this isolated staging DB is changed; original histories are
            # retained in the verified archive and the final import receipt.
            with db.transaction():rebase(pid)
        from app.parsers import tokens
        with db.transaction():
            for row in db.rows('SELECT id,text FROM sources'):db.execute('INSERT INTO source_fts VALUES(?,?)',(row['id'],tokens(row['text'])))
            for row in db.rows('SELECT id,text FROM knowledge_units'):db.execute('INSERT INTO knowledge_unit_fts VALUES(?,?)',(row['id'],tokens(row['text'])))
        result={'project_id':pid,'source_project_id':old_pid,'mapping':mapping,'mode':mode,'records':sum(len(r) for r in tables.values()),'foreign_key_errors':db.rows('PRAGMA foreign_key_check'),'human_approval_added':False,
                'fork_note':'原始档案及所有历史保留；派生ID已映射，GraphRAG物理索引需要重建' if mode=='fork' else '原始ID、版本与完整历史已恢复'}
        (config.DATA/'transfer-result.json').write_text(db.dumps(result),encoding='utf-8')
        print(db.dumps({'restored':True,'project_id':pid,'records':result['records']}))

if __name__=='__main__':main()
