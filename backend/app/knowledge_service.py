"""Shared search/read service: an index hit is a location, never a support verdict."""
import math
import time
from . import db, security, knowledge_units as units


def source_is_excluded(pid,source_id):
    if source_id in db.setting('document_observation_supersessions',{}):return True
    source=db.one('SELECT s.file_id,s.method,p.page_number,p.review_state FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE s.id=? AND f.project_id=?',(source_id,pid))
    if not source:return False
    if source['review_state']=='not_applicable':return True
    saved=old=None
    if source['method']=='document_rule_context':
        saved=db.one('SELECT text,locator FROM sources WHERE id=?',(source_id,))
        locator=db.loads(saved['locator'],{})
        old=db.one('SELECT u.* FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id WHERE u.id=? AND p.project_id=? AND p.file_id=?',
                   (locator.get('unit_id'),pid,source['file_id']))
        if old and old['review_state']=='excluded':return True
    excluded=bool(db.one("SELECT u.id FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id WHERE p.project_id=? AND p.file_id=? AND u.review_state='excluded' AND (json_extract(u.locator,'$.page')=? OR EXISTS(SELECT 1 FROM json_each(u.locator,'$.segments') segment WHERE json_extract(segment.value,'$.locator.page')=?)) LIMIT 1",(pid,source['file_id'],source['page_number'],source['page_number'])))
    if not excluded or source['method']!='document_rule_context':return excluded
    # A rejected OCR observation must not invalidate a separately captured
    # native slide used by an immutable published rule. Resolve only an exact
    # whole-unit match in the current parse; legacy fragments keep the guard.
    if not old or old['review_state']=='excluded' or old['origin']!='pptx_slide' or old['content_hash']!=locator.get('unit_hash') or old['text']!=saved['text']:
        return True
    current=db.one("SELECT p.* FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id WHERE p.project_id=? AND p.file_id=? AND u.review_state!='excluded' AND u.origin=? AND u.content_hash=? AND u.text=? AND json_extract(u.locator,'$.page')=? LIMIT 1",
                   (pid,source['file_id'],old['origin'],old['content_hash'],saved['text'],source['page_number']))
    return not (current and units.parse_current(current))


def eligible(pid, remote=False, collection='all'):
    # Ranking needs the current unit and its locator, not every unit's complete
    # dispute history and claim reviews. Decorate only the originals actually read.
    from .material_policy import research_sql
    rows=db.rows("SELECT u.*,p.project_id,p.file_id,p.file_hash,p.input_hash,p.engine,p.strategy,f.name,f.extension,f.role,(SELECT COUNT(*) FROM knowledge_disputes d WHERE d.unit_id=u.id AND d.state='open') open_disputes FROM knowledge_units u JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id JOIN files f ON f.id=p.file_id WHERE p.project_id=? AND f.active=1 AND u.review_state!='excluded' AND "+research_sql()+" ORDER BY u.id",(pid,))
    current={};permitted={};excluded={};result=[]
    with units.cache_inputs():
        for row in rows:
            fid=row['file_id']
            if fid not in current:
                original=db.one('SELECT * FROM files WHERE id=?',(fid,));current[fid]=units.parse_current(row,original)
                permitted[fid]=not remote or security.file_allows_remote(fid,pid)
                excluded[fid]={r['page_number'] for r in db.rows("SELECT page_number FROM pages WHERE file_id=? AND review_state='not_applicable'",(fid,))}
            if not current[fid] or not permitted[fid] or collection=='reviewed' and (row['review_state']!='reviewed' or row['open_disputes']):continue
            locator=db.loads(row['locator'],{})
            pages={locator.get('page',1)}|{s.get('locator',{}).get('page') for s in locator.get('segments',[])}
            if pages&excluded[fid]:continue
            result.append({**row,'locator':locator,'current':True})
    return result


@db.read_session()
@units.cache_inputs()
def search(pid, query, limit=10, remote=False, collection='all', include_units=True,file_ids=None):
    started=time.perf_counter()
    from . import reference_knowledge, parsers, retrieval, model_install
    from fastapi import HTTPException
    if not isinstance(query,str) or not query.strip() or len(query)>5000 or type(limit) is not int or not 1<=limit<=30:raise HTTPException(422,'检索问题须为1至5000字，数量为1至30')
    if collection not in ('all', 'reviewed', 'originals'):
        from fastapi import HTTPException
        raise HTTPException(422, '知识库范围无效')
    available = eligible(pid, remote, collection)
    if file_ids:
        from .knowledge_lineage import descendants_of_files
        descendants_of_files(pid,file_ids)
        available=[u for u in available if u['file_id'] in file_ids]
    cfg = retrieval.config(pid)
    combined=bool(available and include_units and collection!='originals')
    original = reference_knowledge.search_legacy(pid, query, min(100,max(limit,int(cfg['candidate_k']))) if combined else limit, remote,**({'file_ids':file_ids} if file_ids else {}),**({'rerank_override':'fusion'} if combined else {}))
    by_id = {r['id']: r for r in available}
    raw = []
    for hit in original['items']:
        if file_ids and hit['file_id'] not in file_ids:continue
        if (remote or collection!='originals') and source_is_excluded(pid,hit['id']):continue
        related = [u for u in available if u['file_id'] == hit['file_id'] and u['locator'].get('page') == hit.get('page_number') and
                   (u['locator'].get('source_id') == hit['id'] or hit['text'].strip() in u['text'] or u['text'].strip() in hit['text'])]
        candidate = next((u for u in related if u['origin'] == hit.get('method')), related[0] if related else None)
        if collection!='originals' and candidate is None and any(u['file_id']==hit['file_id'] for u in available):
            continue
        if collection == 'reviewed' and candidate is None:
            continue
        raw.append({**hit, 'knowledge_unit_id': candidate['id'] if candidate else None, 'unit_hash': candidate['content_hash'] if candidate else None,
                    'unit_review_state': candidate['review_state'] if candidate else 'pending', 'semantic_support': 'not_checked'})
    if not include_units or collection == 'originals' or not available:
        return {**original, 'items': raw, 'collection': collection}
    terms = list(dict.fromkeys(parsers.tokens(query).split()))[:35]
    ranked = []
    if terms and by_id:
        match = ' OR '.join('"' + x.replace('"', '') + '"' for x in terms)
        ranked = [r for r in db.rows('SELECT ft.unit_id,bm25(knowledge_unit_fts) AS rank FROM knowledge_unit_fts ft JOIN knowledge_units u ON u.id=ft.unit_id JOIN knowledge_parses p ON p.id=u.parse_id JOIN knowledge_heads h ON h.parse_id=p.id WHERE knowledge_unit_fts MATCH ? AND p.project_id=? ORDER BY rank LIMIT 500', (match, pid)) if r['unit_id'] in by_id][:100]
    scores = {r['unit_id']: 1 / (61 + i) for i, r in enumerate(ranked)}
    cfg = retrieval.config(pid)
    dense_pipeline={'status':'skipped','matrix_cache_hit':False}
    if by_id and db.setting('reference_knowledge:'+pid,{}).get('semantic_enabled',True) and model_install.status(cfg['model'])['ready']:
        namespace = retrieval.model_key(cfg['model'])
        vector_rows = [r for r in db.rows('SELECT v.*,ch.text_hash AS current_hash,p.id AS parse_id,p.file_id,p.file_hash,p.input_hash FROM knowledge_unit_vectors v JOIN knowledge_unit_chunks ch ON ch.id=v.chunk_id JOIN knowledge_units u ON u.id=v.unit_id JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.project_id=? AND v.model=?', (pid, namespace)) if r['unit_id'] in by_id and r['text_hash'] == r['current_hash']]
        if vector_rows:
            try:
                from . import ai_cache_policy
                enabled=ai_cache_policy.settings()['unit_matrix_cache_enabled']
                q = retrieval.query_embedding(query, cfg['model'])
                dense,cache_hit=retrieval.unit_dense_scores(pid,namespace,vector_rows,q,cache_enabled=enabled)
                dense_pipeline={'status':'completed','matrix_cache_hit':cache_hit,'cache_enabled':enabled,
                                'scored_units':len(dense),'cache_scope':'project_model_chunk_versions_numeric_only'}
                for position, (uid, _) in enumerate(dense):
                    scores[uid] = scores.get(uid, 0) + 1 / (61 + position)
            except (ValueError, RuntimeError) as error:
                dense_pipeline={'status':'failed','matrix_cache_hit':False}
                original.setdefault('warnings', []).append('结构单元向量检索未完成：' + str(error))
    references = {}
    for ref in db.rows('SELECT * FROM bibliography WHERE project_id=? ORDER BY created_at,id', (pid,)):
        references.setdefault(ref['file_id'], []).append(ref)
    found = []
    for uid in sorted(scores, key=lambda x: (-scores[x], x)):
        u = by_id[uid]; refs = references.get(u['file_id'], []); meta = db.loads(refs[0]['metadata'], {}) if refs else {}
        found.append({**u, 'id': 'unit:' + uid, 'knowledge_unit_id': uid, 'unit_hash': u['content_hash'], 'page_number': u['locator'].get('page'),
                      'reference_ids': [r['id'] for r in refs], 'reference_title': meta.get('title', u['name']), 'reference_id': refs[0]['id'] if refs else None,
                      'text': u['text'], 'fusion_score': scores[uid], 'semantic_support': 'not_checked'})
    # Fuse ranks, preserving provenance instead of deduplicating similar-looking prose.
    merged={};fusion={}
    for position,hit in enumerate(found,1):
        key=hit['knowledge_unit_id'];merged[key]=hit;fusion[key]=hit['fusion_score']
    for position,hit in enumerate(raw,1):
        key=hit.get('knowledge_unit_id') or hit['id'];merged.setdefault(key,hit);fusion[key]=fusion.get(key,0)+1/(60+position)
    pool=min(100,max(limit,int(cfg['candidate_k'])))
    result=[{**merged[key],'fusion_rank':position} for position,key in enumerate(sorted(merged,key=lambda key:(-fusion[key],key))[:pool],1)]
    pipeline={'fusion_candidates':len(result),'dense':dense_pipeline,'rerank':{'status':'skipped','type':'cross_encoder','scored':0}}
    if result and db.setting('reference_knowledge:'+pid,{}).get('rerank_enabled',True):
        if model_install.status(cfg['cross_encoder'])['ready']:
            rank_inputs=[];by_identifier={r['id']:r for r in result};words=[w.casefold() for w in terms if len(w)>1]
            for hit in result:
                text=hit['text'];uid=hit.get('knowledge_unit_id')
                if uid:
                    chunks=db.rows('SELECT id,char_start,char_end,text FROM knowledge_unit_chunks WHERE unit_id=? ORDER BY char_start',(uid,))
                    if chunks:
                        chunk=max(chunks,key=lambda c:sum(c['text'].casefold().count(w) for w in words));text=chunk['text']
                        hit['ranking_locator']={'chunk_id':chunk['id'],'char_start':chunk['char_start'],'char_end':chunk['char_end'],'scope':'index_excerpt_only'}
                rank_inputs.append({**hit,'text':text})
            try:
                scored=retrieval.cross_encoder_rerank(query,rank_inputs,cfg['cross_encoder'])
                result=[{**by_identifier[r['id']],'rerank_score':r['rerank_score'],'rerank_rank':r['rerank_rank'],'rerank_cache_hit':r.get('rerank_cache_hit',False)} for r in scored]
                pipeline['rerank']={'status':'completed','type':'cross_encoder','model':cfg['cross_encoder'],'scored':len(scored),'cache_hits':sum(bool(r.get('rerank_cache_hit')) for r in scored),'input_scope':'index_excerpt_parent_evidence_read_separately'}
            except Exception as error:
                pipeline['rerank']['status']='failed';original.setdefault('warnings',[]).append('证据单元重排未完成，保留融合排序：'+str(error))
        else:pipeline['rerank']['status']='unavailable'
    if remote:
        permitted={fid:security.file_allows_remote(fid,pid) for fid in {r['file_id'] for r in result}}
        result=[r for r in result if permitted[r['file_id']]]
    return {**original, 'items': result[:limit], 'collection': collection, 'unit_candidates': len(found), 'unit_pipeline':pipeline,'unit_vector_index': 'local_versioned', 'semantic_support': 'not_checked','timings_ms':{**original.get('timings_ms',{}),'original_pipeline_total':original.get('timings_ms',{}).get('total'),'total':round((time.perf_counter()-started)*1000,3)}}


def read_source(pid, source_id, remote=False, max_chars=16000):
    from . import reference_knowledge
    if source_id.startswith('unit:'):
        uid = source_id[5:]; item = units.get(pid, uid, remote)
        return {**item, **units.read(pid, uid, remote=remote, max_chars=max_chars), 'id': source_id, 'knowledge_unit_id': uid}
    if remote and source_is_excluded(pid,source_id):
        from fastapi import HTTPException
        raise HTTPException(403,'该原始索引涉及已排除单元，请使用明确可用的结构单元，不能以旧片段绕过排除')
    item = reference_knowledge.get_chunk(pid, source_id, remote)
    candidates = [u for u in eligible(pid, remote) if u['file_id'] == item['file_id'] and u['locator'].get('page') == item.get('page_number') and
                  (u['locator'].get('source_id') == source_id or item['text'].strip() in u['text'] or u['text'].strip() in item['text'])]
    if candidates:
        preferred = next((u for u in candidates if u['origin'] == item.get('method')), candidates[0])
        reading=units.read(pid, preferred['id'], remote=remote, max_chars=max_chars)
        observed={p['id'] for p in reading.get('passages',[])}
        alternatives=[u['id'] for u in candidates]
        reading['mapping_candidates']=alternatives
        reading['mapping_ambiguous']=len(alternatives)>1
        reading['unread_candidate_ids']=[uid for uid in alternatives if uid not in observed]
        return {**item, **reading, 'id': source_id, 'knowledge_unit_id': preferred['id']}
    if len(item['text'])>max_chars:
        return {k:v for k,v in {**item,'status':'needs_narrower_range','complete':False,'knowledge_unit_id':None,'action':'旧版来源过长，请先建立结构化版本后分步读取'}.items() if k not in ('text','normalized')}
    return {**item, 'status': 'read', 'complete': False, 'scope': 'legacy_fragment_only', 'issues': ['structure_not_built'], 'semantic_support': 'not_checked', 'knowledge_unit_id': None}


def prepare_writing(pid, query, sid, limit=8, budget_bytes=None):
    from .knowledge_router import prepare_writing as prepare
    return prepare(pid,query,sid,limit,budget_bytes=budget_bytes)


def for_model(read):
    result={k:v for k,v in read.items() if k not in ('passages','disputes')}
    result['passage_locations']=[{k:v for k,v in passage.items() if k!='text'} for passage in read.get('passages',[])]
    result['disputes']=[{k:d[k] for k in ('kind','note','state','resolution') if k in d} for d in read.get('disputes',[])]
    return result


def build_project(pid, payload, progress=None):
    from .material_policy import research_sql
    files = [units.file_record(pid, payload['file_id'])] if payload.get('file_id') else db.rows("SELECT f.* FROM files f WHERE f.project_id=? AND "+research_sql()+" AND f.active=1 ORDER BY f.id", (pid,))
    results = []; failures = []
    for index, original in enumerate(files):
        if progress:
            progress(original['name'], int(5 + 75 * index / max(1, len(files))))
        try:
            head=db.one('SELECT p.* FROM knowledge_parses p JOIN knowledge_heads h ON h.parse_id=p.id WHERE h.file_id=?',(original['id'],))
            if payload.get('preserve_head') and head and units.parse_current(head,original):
                results.append({'file_id':original['id'],'parse_id':head['id'],'preserved_current_head':True});continue
            result = units.build(pid, original['id'], payload.get('strategy', 'paragraphs'), payload.get('engine', units.structure.ENGINE), payload.get('activate', True))
            results.append({'file_id': original['id'], 'parse_id': result['id'], 'units': len(result['units'])})
        except Exception as error:
            failures.append({'file_id': original['id'], 'name': original['name'], 'error': str(error)[:500]})
    index = index_units(pid, progress)
    return {'files': results, 'failures': failures, 'index': index, 'external_requests': False, 'semantic_quality_verified': False}


def index_units(pid, progress=None):
    from . import retrieval, model_install
    cfg = retrieval.config(pid)
    if not model_install.status(cfg['model'])['ready']:
        return {'indexed': 0, 'mode': 'full_text', 'warning': '本地模型未就绪；未下载模型，结构化全文检索可用'}
    namespace = retrieval.model_key(cfg['model']); all_units = eligible(pid)
    current = {r['chunk_id']: r['text_hash'] for r in db.rows('SELECT chunk_id,text_hash FROM knowledge_unit_vectors WHERE model=?', (namespace,))}
    active_ids={u['id'] for u in all_units}
    chunks=db.rows('SELECT ch.* FROM knowledge_unit_chunks ch JOIN knowledge_units u ON u.id=ch.unit_id JOIN knowledge_parses p ON p.id=u.parse_id WHERE p.project_id=?',(pid,))
    changed = [u for u in chunks if u['unit_id'] in active_ids and current.get(u['id']) != u['text_hash']]
    for start in range(0, len(changed), 16):
        if progress:
            progress('建立本地结构单元索引', 80 + int(18 * start / max(1, len(changed))))
        batch = changed[start:start + 16]
        vectors = retrieval.embed([u['text'] for u in batch], cfg['model'])
        with db.transaction():
            for u, vector in zip(batch, vectors):
                db.execute('INSERT OR REPLACE INTO knowledge_unit_vectors VALUES(?,?,?,?,?,?)', (u['unit_id'],u['id'], namespace, u['text_hash'], len(vector), vector.tobytes()))
    return {'indexed': len(changed), 'model': cfg['model'], 'mode': 'local_dense_and_full_text'}
