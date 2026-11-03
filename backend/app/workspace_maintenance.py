"""Conservative resource lifecycle: reference removal, cache and orphan collection."""
import hashlib,os,re,shutil,time,json
from pathlib import Path
from fastapi import HTTPException
from . import db,security,research_records as store

def deletion_preview(pid,rids):
    if not isinstance(rids,list) or not 1<=len(rids)<=500:raise HTTPException(422,'请选择要删除的文献')
    rids=list(dict.fromkeys(rids));selected=set(rids)
    items=[]
    for rid in rids:
        row=store.require_reference(pid,rid);citations=db.one('SELECT count(*) AS n FROM citations WHERE reference_id=?',(rid,))['n']
        file_id=row['file_id'];others=sum(reference['id'] not in selected for reference in db.rows("SELECT id FROM bibliography WHERE file_id=? AND COALESCE(json_extract(metadata,'$._studio_deleted'),0)=0",(file_id,))) if file_id else 0
        items.append({'reference_id':rid,'revision':row['revision'],'title':row['metadata'].get('title',''),'file_id':file_id,'historical_citations':citations,'other_references':others,
                      'original_policy':'保留历史与共享原件；移除当前检索入口，未引用的孤立资源可单独回收'})
    return {'items':items,'fingerprint':store.digest(items),'permanent_original_deletion':False}


def delete_references(pid,body):
    preview=deletion_preview(pid,body.get('reference_ids'))
    if body.get('fingerprint')!=preview['fingerprint']:raise HTTPException(409,'删除影响已变化，请重新预览')
    with db.transaction():
        for item in preview['items']:
            row=store.require_reference(pid,item['reference_id'])
            from .reference_service import save as save_reference
            save_reference(pid,{**row['metadata'],'_studio_deleted':True,'deleted_at':db.now()},row['file_id'],row['id'],row['verification'])
            if item['file_id'] and not item['other_references']:
                fid=item['file_id'];db.execute('UPDATE files SET active=0 WHERE id=?',(fid,))
                db.execute('DELETE FROM neural_vectors WHERE source_id IN (SELECT id FROM sources WHERE file_id=?)',(fid,))
                db.execute('DELETE FROM source_fts WHERE source_id IN (SELECT id FROM sources WHERE file_id=?)',(fid,))
                from .reference_knowledge import sync_document
                from .knowledge_lineage import changed
                sync_document(fid);changed(pid,'file',fid,'文献已从当前知识库删除')
        receipt=store.save(pid,'deletion',{'items':preview['items'],'fingerprint':preview['fingerprint']})
    return {'deleted':len(preview['items']),'receipt_id':receipt['id'],'original_histories_preserved':True}


def cache_roots():
    from .config import DATA,ROOT
    return {'previews':DATA/'previews','temporary':DATA/'tmp','ocr':DATA/'ocr-cache','models':ROOT/'.runtime'/'models'}


def cache_status():
    result=[]
    for category,base in cache_roots().items():
        entries=[item for item in base.rglob('*') if item.is_file() and not item.is_symlink() and item.resolve().is_relative_to(base.resolve())] if base.exists() else []
        result.append({'id':category,'bytes':sum(item.stat().st_size for item in entries),'files':len(entries),'rebuildable':True})
    return {'categories':result,'total_bytes':sum(item['bytes'] for item in result),'originals_are_cache':False}


def clear_cache(body):
    category=body.get('category');roots=cache_roots()
    if category not in roots:raise HTTPException(422,'缓存类型无效')
    if db.one("SELECT id FROM tasks WHERE status IN ('running','queued') LIMIT 1"):raise HTTPException(409,'请等待任务完成后再清理缓存')
    current=cache_status();item=next(row for row in current['categories'] if row['id']==category)
    if body.get('expected_bytes')!=item['bytes']:raise HTTPException(409,'缓存占用已变化，请重新查看')
    base=roots[category].resolve();released=0
    if base.exists():
        for target in sorted(base.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if target.is_symlink() or not target.resolve().is_relative_to(base):continue
            if target.is_file():released+=target.stat().st_size;target.unlink()
            elif target.is_dir():
                try:target.rmdir()
                except OSError:pass
    if category=='models':
        from . import retrieval
        retrieval._MODELS.clear();retrieval._QUERY_VECTORS.clear();retrieval._RERANK_SCORES.clear()
    return {'category':category,'released_bytes':released,'originals_deleted':False}


def orphan_objects(body=None):
    from .config import DATA
    referenced=set()
    # Inspect every persistent table, including immutable histories and generic records.
    for spec in db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%' AND name NOT IN ('settings','studio_oauth')"):
        name=spec['name']
        if not re.fullmatch('[a-zA-Z0-9_]+',name):continue
        for row in db.rows('SELECT * FROM "'+name+'"'):
            for value in row.values():
                if isinstance(value,str):referenced.update(re.findall(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])',value))
    for row in db.rows('SELECT value FROM settings'):
        referenced.update(re.findall('[0-9a-f]{64}',row['value']))
    cutoff=time.time()-86400
    files=[item for item in (DATA/'objects').iterdir() if item.is_file() and not item.is_symlink() and item.stat().st_mtime<cutoff and re.fullmatch('[0-9a-f]{64}',item.name) and item.name not in referenced]
    manifest=[{'sha256':item.name,'bytes':item.stat().st_size} for item in sorted(files)]
    result={'items':manifest,'bytes':sum(item['bytes'] for item in manifest),'fingerprint':store.digest(manifest),'minimum_age_hours':24}
    if body is not None:
        if db.one("SELECT id FROM tasks WHERE status IN ('running','queued') LIMIT 1"):raise HTTPException(409,'任务进行中，请稍后回收孤立资源')
        if body.get('fingerprint')!=result['fingerprint']:raise HTTPException(409,'资源引用已变化，请重新预览')
        for item in files:
            if not item.resolve().is_relative_to((DATA/'objects').resolve()):raise ValueError('资源路径无效')
            item.unlink()
        return {'deleted':len(files),'released_bytes':result['bytes']}
    return result
