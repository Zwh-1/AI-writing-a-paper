"""Local neural/keyword retrieval with explicit candidate and rerank evaluation."""
import hashlib, math, os, threading, time
from collections import OrderedDict
from . import db
from .config import ROOT
_LOCK=threading.RLock();_MODELS={}
_QUERY_VECTORS=OrderedDict()
_RERANK_SCORES=OrderedDict()
_DENSE_MATRICES=OrderedDict()
_MODEL_LOCKS={}
_INFERENCE_LOCKS={}
_QUERY_LOCKS=[threading.RLock() for _ in range(32)]
_MATRIX_CACHE_BYTES=96*1024*1024
DEFAULT_MODEL='BAAI/bge-small-zh-v1.5'

def config(pid):return {'enabled':False,'model':DEFAULT_MODEL,'candidate_k':40,'rerank':'fusion','cross_encoder':'BAAI/bge-reranker-base',**db.setting('retrieval:'+pid,{})}
def model_instance(name,download=False,cross=False):
    from . import model_install
    initial=model_install.status(name)
    key=(name,cross,initial['fingerprint'])
    with _LOCK:
        cached=_MODELS.get(key)
        guard=_MODEL_LOCKS.setdefault((name,cross),threading.RLock())
    if cached is not None:return cached
    with guard:
        with _LOCK:cached=_MODELS.get(key)
        if cached is None:
            from fastembed import TextEmbedding
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            cls=TextCrossEncoder if cross else TextEmbedding
            from . import model_install
            local=model_install.status(name)
            if download and not local['ready']:model_install.download(name);local=model_install.status(name)
            if not local['ready']:raise ValueError('本地模型尚未安装，请先下载并校验模型文件')
            key=(name,cross,local['fingerprint'])
            with _LOCK:cached=_MODELS.get(key)
            if cached is None:
                cached=cls(model_name=name,cache_dir=str(ROOT/'.runtime/models'),specific_model_path=local['path'],local_files_only=True,threads=2)
                with _LOCK:_MODELS[key]=cached
        return cached


def _inference_lock(name,cross=False):
    with _LOCK:return _INFERENCE_LOCKS.setdefault((name,cross),threading.RLock())

def embed(texts,name):
    import numpy as np
    model=model_instance(name)
    with _inference_lock(name):
        values=[np.asarray(v,dtype=np.float32) for v in model.embed(texts,batch_size=16)]
    for v in values:
        if v.ndim!=1 or not np.isfinite(v).all() or not float(np.linalg.norm(v)):raise ValueError('语义模型返回无效向量')
    return values

def query_embedding(query,name):
    """Cache only local query vectors, never hits, evidence or permissions.

    The verified model fingerprint is part of the key. Replaced/unavailable
    models cannot reuse an old vector; returned arrays cannot mutate the cache.
    """
    key=(model_key(name),query)
    with _LOCK:
        cached=_QUERY_VECTORS.get(key)
        if cached is not None:
            _QUERY_VECTORS.move_to_end(key);return cached.copy()
    with _QUERY_LOCKS[hash(key)%len(_QUERY_LOCKS)]:
        with _LOCK:cached=_QUERY_VECTORS.get(key)
        if cached is not None:return cached.copy()
        value=embed([query],name)[0].copy()
        with _LOCK:
            _QUERY_VECTORS[key]=value
            while len(_QUERY_VECTORS)>128:_QUERY_VECTORS.popitem(last=False)
        return value.copy()

def embedding_text(source):
    ext=source.get('extension','');location='段落组' if ext=='.docx' else '资料块/工作表' if ext in ('.txt','.md','.csv','.xlsx') else '原页'
    return '资料：'+str(source.get('name',''))+'\n位置：'+location+str(source.get('page_number',''))+' '+str(source.get('page_title',''))+'\n内容：'+source['text']

def build_index(pid,progress=None,knowledge=False):
    from .document_observations import current_source_sql
    scope=__import__('app.reference_knowledge',fromlist=['scope_sql']).scope_sql() if knowledge else ''
    scope+=' AND '+current_source_sql()
    cfg=config(pid);namespace=model_key(cfg['model']);sources=db.rows('SELECT s.*,f.name,f.extension,p.page_number,p.title AS page_title FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1 '+scope,(pid,));changed=[s for s in sources if not db.one('SELECT source_id FROM neural_vectors WHERE source_id=? AND model=? AND text_hash=?',(s['id'],namespace,hashlib.sha256(embedding_text(s).encode()).hexdigest()))]
    for start in range(0,len(changed),16):
        if progress:progress(start,len(changed))
        batch=changed[start:start+16];vectors=embed([embedding_text(s) for s in batch],cfg['model'])
        with db.transaction() as c:
            for s,v in zip(batch,vectors):c.execute('INSERT OR REPLACE INTO neural_vectors VALUES(?,?,?,?,?,?)',(s['id'],namespace,hashlib.sha256(embedding_text(s).encode()).hexdigest(),len(v),v.tobytes(),db.now()))
    return {'model':cfg['model'],'indexed':len(changed),'sources':len(sources),'external_paper_requests':0,'engine':'FastEmbed ONNX CPU'}

def reciprocal_rank_fusion(keyword,semantic,limit,rank_constant=60):
    """Fuse unique ranked results; raw BM25 and cosine scales are never mixed."""
    if not math.isfinite(rank_constant) or rank_constant<=0:raise ValueError('RRF常数必须为正数')
    candidates={};scores={}
    for channel,rows,rank_key in [('keyword',keyword,'bm25_rank'),('neural',semantic,'dense_rank')]:
        seen=set();position=0
        for item in rows:
            sid=item['id']
            if sid in seen:continue
            seen.add(sid);position+=1
            row=candidates.setdefault(sid,{**item,'channels':[]})
            row['channels'].append(channel);row[rank_key]=position
            if channel=='keyword':row['bm25_score']=item.get('rank')
            else:row['semantic_score']=item['semantic_score']
            scores[sid]=scores.get(sid,0.0)+1.0/(rank_constant+position)
    ordered=sorted(candidates.values(),key=lambda row:(-scores[row['id']],row['id']))[:limit]
    return [{**row,'fusion_score':scores[row['id']],'fusion_rank':position} for position,row in enumerate(ordered,1)]


def cross_encoder_rerank(query,candidates,model):
    fingerprint=model_key(model)
    texts=[embedding_text(item) for item in candidates]
    keys=[(fingerprint,query,hashlib.sha256(text.encode()).hexdigest()) for text in texts]
    with _LOCK:hits=[key in _RERANK_SCORES for key in keys]
    # One model instance performs one inference at a time; unrelated models and
    # query-cache lookups do not wait behind this potentially long CPU call.
    with _inference_lock(model,cross=True):
        with _LOCK:
            available={key:_RERANK_SCORES[key] for key in keys if key in _RERANK_SCORES}
        missing={key:text for key,text in zip(keys,texts) if key not in available}
        if missing:
            scorer=model_instance(model,cross=True)
            values=list(scorer.rerank(query,list(missing.values()),batch_size=8))
            if len(values)!=len(missing):raise ValueError('重排模型返回的分数数量与候选数量不一致')
            scores=[float(value) for value in values]
            if any(not math.isfinite(score) for score in scores):raise ValueError('重排模型返回了无效相关性分数')
            available.update(zip(missing,scores))
        scores=[available[key] for key in keys]
        with _LOCK:
            for key in keys:
                _RERANK_SCORES[key]=available[key];_RERANK_SCORES.move_to_end(key)
            while len(_RERANK_SCORES)>2048:_RERANK_SCORES.popitem(last=False)
    ranked=[{**item,'rerank_score':score,'rank':-score,'rerank_cache_hit':hit} for item,score,hit in zip(candidates,scores,hits)]
    ranked.sort(key=lambda item:(-item['rerank_score'],item['fusion_rank']))
    return [{**item,'rerank_rank':position} for position,item in enumerate(ranked,1)]


def _normalized_matrix(pid,namespace,rows,dimension):
    """Cache immutable numeric work only; current rows/permissions stay outside.

    The signature includes current embedding text and actual vector bytes, so
    replacement indexes, edited metadata and in-place repairs invalidate it.
    """
    import numpy as np
    signature=hashlib.sha256();candidates=[]
    for row in sorted(rows,key=lambda item:item['id']):
        expected=hashlib.sha256(embedding_text(row).encode()).hexdigest()
        if row['text_hash']!=expected:continue
        raw=row['vector']
        signature.update((str(row['id'])+'\0'+expected+'\0'+str(row['dimension'])+'\0').encode())
        signature.update(hashlib.sha256(raw).digest())
        candidates.append(row)
    key=(str(db.DB_PATH),pid,namespace,dimension,signature.hexdigest())
    with _LOCK:
        cached=_DENSE_MATRICES.get(key)
        if cached is not None:
            _DENSE_MATRICES.move_to_end(key)
            by_id={row['id']:row for row in candidates}
            return [by_id[identity] for identity in cached[0]],cached[1],True
    selected=[];vectors=[]
    for row in candidates:
        if not isinstance(row['dimension'],int) or row['dimension']!=dimension:continue
        if len(row['vector'])!=dimension*np.dtype(np.float32).itemsize:continue
        vector=np.frombuffer(row['vector'],dtype=np.float32)
        if not np.isfinite(vector).all():continue
        norm=float(np.linalg.norm(vector))
        if not math.isfinite(norm) or not norm:continue
        selected.append(row);vectors.append(vector/norm)
    matrix=np.stack(vectors).astype(np.float32,copy=False) if vectors else np.empty((0,dimension),dtype=np.float32)
    matrix.setflags(write=False)
    if matrix.nbytes<=_MATRIX_CACHE_BYTES//2:
        with _LOCK:
            # Each project/model keeps one current snapshot. Historical matrices
            # cannot accumulate after frequent document edits.
            stale=[old for old in _DENSE_MATRICES if old[:3]==key[:3] and old!=key]
            for old in stale:_DENSE_MATRICES.pop(old,None)
            _DENSE_MATRICES[key]=([row['id'] for row in selected],matrix)
            while len(_DENSE_MATRICES)>8 or sum(value[1].nbytes for value in _DENSE_MATRICES.values())>_MATRIX_CACHE_BYTES:
                _DENSE_MATRICES.popitem(last=False)
    return selected,matrix,False


def _dense_top_k(matrix,query_vector,rows,limit):
    """Vectorized cosine and partial TopK with deterministic source-ID ties."""
    import numpy as np
    query=np.asarray(query_vector,dtype=np.float32)
    norm=float(np.linalg.norm(query))
    if query.ndim!=1 or not np.isfinite(query).all() or not math.isfinite(norm) or not norm:
        raise ValueError('查询模型返回无效向量')
    scores=matrix@(query/norm)
    count=min(len(rows),limit)
    if not count:return []
    if count<len(rows):
        shortlist=np.argpartition(-scores,count-1)[:count]
        boundary=float(scores[shortlist].min())
        above=np.flatnonzero(scores>boundary).tolist()
        tied=np.flatnonzero(scores==boundary).tolist()
        # Matrix rows are ordered by source identity, including boundary ties.
        indexes=above+tied[:count-len(above)]
    else:indexes=list(range(len(rows)))
    indexes.sort(key=lambda index:(-float(scores[index]),rows[index]['id']))
    return [{**{key:value for key,value in rows[index].items() if key!='vector'},
             'semantic_score':float(scores[index])} for index in indexes]


def hybrid(pid,query,kind='research',limit=12,remote=False,force=False,knowledge=False,rerank_override=None,file_ids=None):
    from .parsers import keyword_search
    from .security import file_allows_remote
    started=time.perf_counter();cfg=config(pid);limit=min(100,max(1,int(limit)))
    if knowledge:
        policy=db.setting('reference_knowledge:'+pid,{})
        cfg={**cfg,'rerank':'cross_encoder' if policy.get('rerank_enabled',True) else 'fusion'}
    if rerank_override is not None:
        if rerank_override not in ('fusion','cross_encoder'):raise ValueError('重排覆盖策略无效')
        cfg={**cfg,'rerank':rerank_override}
    pool=min(100,max(int(cfg['candidate_k']),limit));stage_start=time.perf_counter()
    keyword=keyword_search(pid,query,kind,pool,remote,**({'file_ids':file_ids} if file_ids else {}),**({'knowledge':True} if knowledge else {}))
    timings={'bm25':round((time.perf_counter()-stage_start)*1000,3)}
    pipeline={'bm25':{'status':'completed','candidates':len(keyword),'limit':pool},'dense':{'status':'skipped','candidates':0},'rrf':{'status':'skipped','candidates':0,'rank_constant':60},'rerank':{'status':'skipped','type':None,'scored':0}}
    if not cfg['enabled'] and not force:
        return {'items':keyword[:limit],'candidates':keyword,'mode':'keyword','warnings':[],'pipeline':pipeline,'timings_ms':timings}
    from .document_observations import current_source_sql
    scope=__import__('app.reference_knowledge',fromlist=['scope_sql']).scope_sql() if knowledge else ''
    scope+=' AND '+current_source_sql()
    permissions={}
    def eligible_source(source):
        if kind=='research' and not knowledge and source['kind']!='evidence':return False
        if kind=='example' and source['kind'] not in ('example','counterexample'):return False
        if remote:
            fid=source['file_id']
            if fid not in permissions:permissions[fid]=file_allows_remote(fid,pid)
            if not permissions[fid]:return False
        return True
    eligible=db.rows('SELECT s.id,s.file_id,s.kind,f.egress FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1 '+scope,(pid,))
    eligible=[source for source in eligible if eligible_source(source) and (not file_ids or source['file_id'] in file_ids)]
    if not eligible:
        return {'items':[],'candidates':[],'mode':'no_eligible_materials','warnings':['没有当前范围内可用的资料，需补充材料或核对外发权限'],'coverage':{'indexed':0,'eligible':0},'pipeline':pipeline,'timings_ms':timings}
    import numpy as np
    stage_start=time.perf_counter();query_vector=query_embedding(query,cfg['model']);namespace=model_key(cfg['model'])
    vector_rows=db.rows('SELECT s.*,f.name,f.role,f.egress,f.extension,f.active,p.page_number,p.title AS page_title,p.review_state,v.vector,v.dimension,v.text_hash FROM neural_vectors v JOIN sources s ON s.id=v.source_id JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1 AND v.model=? '+scope,(pid,namespace))
    allowed={row['id'] for row in eligible}
    vector_rows=[row for row in vector_rows if row['id'] in allowed]
    matrix_start=time.perf_counter()
    semantic,matrix,matrix_cache_hit=_normalized_matrix(pid,namespace,vector_rows,len(query_vector))
    timings['dense_matrix']=round((time.perf_counter()-matrix_start)*1000,3)
    if not semantic:raise ValueError('没有当前模型版本的有效语义索引，请先建立或恢复索引')
    score_start=time.perf_counter();dense=_dense_top_k(matrix,query_vector,semantic,pool)
    timings['dense_top_k']=round((time.perf_counter()-score_start)*1000,3)
    timings['dense']=round((time.perf_counter()-stage_start)*1000,3)
    pipeline['dense']={'status':'completed','candidates':len(dense),'eligible':len(eligible),'indexed':len(semantic),'limit':pool,'model':cfg['model'],'matrix_cache_hit':matrix_cache_hit,'vectorized_top_k':True,'cache_scope':'project_model_source_versions_numeric_only'}
    stage_start=time.perf_counter();merged=reciprocal_rank_fusion(keyword,dense,pool)
    timings['rrf']=round((time.perf_counter()-stage_start)*1000,3)
    pipeline['rrf']={'status':'completed','candidates':len(merged),'rank_constant':60}
    warnings=[] if len(semantic)==len(eligible) else [f'语义索引覆盖{len(semantic)}/{len(eligible)}，新资料仍可通过BM25召回，请增量更新索引']
    ranked=[{**row,'rerank_score':None,'rank':-row['fusion_score']} for row in merged]
    reranker='fusion_only';reranker_model=None;degraded=False;stage_start=time.perf_counter()
    if cfg['rerank']=='cross_encoder' and merged:
        try:
            ranked=cross_encoder_rerank(query,merged,cfg['cross_encoder'])
            reranker='cross_encoder';reranker_model=cfg['cross_encoder']
            pipeline['rerank']={'status':'completed','type':'cross_encoder','model':reranker_model,'scored':len(ranked),'cache_hits':sum(bool(r.get('rerank_cache_hit')) for r in ranked)}
        except Exception as error:
            degraded=True
            pipeline['rerank']={'status':'failed','type':'cross_encoder','model':cfg['cross_encoder'],'scored':0}
            warnings.append('Cross-Encoder重排未完成，当前保留RRF融合排序：'+str(error))
    elif cfg['rerank']=='cosine' and merged:
        vectors=embed([embedding_text(row) for row in merged],cfg['model'])
        maximum=max(row['fusion_score'] for row in merged)
        scores=[.75*float(vector@query_vector/(np.linalg.norm(vector)*np.linalg.norm(query_vector)))+.25*row['fusion_score']/maximum for row,vector in zip(merged,vectors)]
        ranked=[{**row,'rerank_score':score,'rank':-score} for row,score in zip(merged,scores)]
        ranked.sort(key=lambda row:(-row['rerank_score'],row['fusion_rank']))
        reranker='cosine';pipeline['rerank']={'status':'completed','type':'bi_encoder','model':cfg['model'],'scored':len(ranked)}
    timings['rerank']=round((time.perf_counter()-stage_start)*1000,3)
    items=ranked[:limit]
    if remote:
        # Recheck after local inference, including permissions revoked while reranking.
        file_ids={row['file_id'] for row in [*keyword,*dense,*ranked]}
        current_permissions={fid:file_allows_remote(fid,pid) for fid in file_ids}
        items=[row for row in ranked if current_permissions[row['file_id']]][:limit]
        merged=[row for row in merged if current_permissions.get(row['file_id'],False)]
        keyword=[row for row in keyword if current_permissions.get(row['file_id'],False)]
        dense=[row for row in dense if current_permissions.get(row['file_id'],False)]
    timings['total']=round((time.perf_counter()-started)*1000,3)
    return {
        'items':items,'candidates':merged,'mode':'neural_keyword_rrf','reranker':reranker,
        'reranker_model':reranker_model,'warnings':warnings,'degraded':degraded,
        'coverage':{'indexed':len(semantic),'eligible':len(eligible)},'model':cfg['model'],
        'pipeline':pipeline,'timings_ms':timings,
        'bm25_ids':[row['id'] for row in keyword],'dense_ids':[row['id'] for row in dense],
        'fusion_ids':[row['id'] for row in merged],'final_ids':[row['id'] for row in items],
    }

def model_key(model):
    from .model_install import status
    value=status(model)
    if not value['ready']:raise ValueError('请先安装并校验本地模型')
    return model+':'+value['fingerprint']+':v2-metadata'

def metrics(ids,relevance,k):
    ids=ids[:k];gold={sid:float(gain) for sid,gain in relevance.items() if float(gain)>0};hits=set(ids)&set(gold);seen=set();dcg=0
    for i,sid in enumerate(ids):
        if sid not in seen:dcg+=(2**gold.get(sid,0)-1)/math.log2(i+2);seen.add(sid)
    ideal=sum((2**gain-1)/math.log2(i+2) for i,gain in enumerate(sorted(gold.values(),reverse=True)[:k]))
    return {'recall':len(hits)/len(gold) if gold else 0,'mrr':next((1/(i+1) for i,sid in enumerate(ids) if sid in gold),0),'ndcg':dcg/ideal if ideal else 0}

def evaluate(pid,cases,k=10,progress=None):
    if not isinstance(cases,list) or not 1<=len(cases)<=200:raise ValueError('需要1至200条带来源标注的问题')
    results=[]
    for number,case in enumerate(cases):
        if progress:progress(number,len(cases))
        if not isinstance(case,dict) or not isinstance(case.get('query'),str) or not case['query'].strip():raise ValueError('每个评测问题需要非空query')
        gold=case.get('relevance') or {sid:1 for sid in case.get('expected_source_ids',[])}
        if not gold or not case.get('query'):raise ValueError('问题需要query与relevance或expected_source_ids')
        for sid,gain in gold.items():
            if not db.one('SELECT s.id FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=? AND f.project_id=?',(sid,pid)) or not 0<=float(gain)<=4:raise ValueError('来源或相关性等级无效')
        r=hybrid(pid,case['query'],case.get('kind','all'),k,force=True);candidate_ids=[s['id'] for s in r['candidates']];final_ids=[s['id'] for s in r['items']];keyword_ids=r.get('bm25_ids') or [s['id'] for s in __import__('app.parsers',fromlist=['keyword_search']).keyword_search(pid,case['query'],case.get('kind','all'),k)];dense_ids=r.get('dense_ids',[])
        results.append({'query':case['query'],'candidate':metrics(candidate_ids,gold,len(candidate_ids)),'reranked':metrics(final_ids,gold,k),'keyword':metrics(keyword_ids,gold,k),'bm25':metrics(keyword_ids,gold,k),'dense':metrics(dense_ids,gold,k),'candidate_ids':candidate_ids,'final_ids':final_ids,'candidate_k':len(candidate_ids),'fusion':metrics(candidate_ids,gold,k),'reranker':r.get('reranker'),'pipeline':r.get('pipeline'),'timings_ms':r.get('timings_ms')})
    summary={stage:{key:sum(r[stage][key] for r in results)/len(results) for key in ('recall','mrr','ndcg')} for stage in ('candidate','fusion','reranked','keyword','bm25','dense')}
    return {'cases':results,'summary':summary,'final_k':k,'annotation_status':'submitted_unverified','human_acceptance':False}
