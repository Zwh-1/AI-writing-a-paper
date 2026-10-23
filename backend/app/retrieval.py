"""Local neural/keyword retrieval with explicit candidate and rerank evaluation."""
import hashlib, math, os, threading
from . import db
from .config import ROOT
_LOCK=threading.RLock();_MODELS={}
DEFAULT_MODEL='BAAI/bge-small-zh-v1.5'

def config(pid):return {'enabled':False,'model':DEFAULT_MODEL,'candidate_k':40,'rerank':'fusion','cross_encoder':'BAAI/bge-reranker-base',**db.setting('retrieval:'+pid,{})}
def model_instance(name,download=False,cross=False):
    from . import model_install
    initial=model_install.status(name)
    key=(name,cross,initial['fingerprint'])
    with _LOCK:
        if key not in _MODELS:
            from fastembed import TextEmbedding
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            cls=TextCrossEncoder if cross else TextEmbedding
            from . import model_install
            local=model_install.status(name)
            if download and not local['ready']:model_install.download(name);local=model_install.status(name)
            if not local['ready']:raise ValueError('本地模型尚未安装，请先下载并校验模型文件')
            _MODELS[key]=cls(model_name=name,cache_dir=str(ROOT/'.runtime/models'),specific_model_path=local['path'],local_files_only=True,threads=2)
        return _MODELS[key]

def embed(texts,name):
    import numpy as np
    values=[np.asarray(v,dtype=np.float32) for v in model_instance(name).embed(texts,batch_size=16)]
    for v in values:
        if v.ndim!=1 or not np.isfinite(v).all() or not float(np.linalg.norm(v)):raise ValueError('语义模型返回无效向量')
    return values

def embedding_text(source):
    ext=source.get('extension','');location='段落组' if ext=='.docx' else '资料块/工作表' if ext in ('.txt','.md','.csv','.xlsx') else '原页'
    return '资料：'+str(source.get('name',''))+'\n位置：'+location+str(source.get('page_number',''))+' '+str(source.get('page_title',''))+'\n内容：'+source['text']

def build_index(pid,progress=None):
    cfg=config(pid);namespace=model_key(cfg['model']);sources=db.rows('SELECT s.*,f.name,f.extension,p.page_number,p.title AS page_title FROM sources s JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1',(pid,));changed=[s for s in sources if not db.one('SELECT source_id FROM neural_vectors WHERE source_id=? AND model=? AND text_hash=?',(s['id'],namespace,hashlib.sha256(embedding_text(s).encode()).hexdigest()))]
    for start in range(0,len(changed),16):
        if progress:progress(start,len(changed))
        batch=changed[start:start+16];vectors=embed([embedding_text(s) for s in batch],cfg['model'])
        with db.transaction() as c:
            for s,v in zip(batch,vectors):c.execute('INSERT OR REPLACE INTO neural_vectors VALUES(?,?,?,?,?,?)',(s['id'],namespace,hashlib.sha256(embedding_text(s).encode()).hexdigest(),len(v),v.tobytes(),db.now()))
    return {'model':cfg['model'],'indexed':len(changed),'sources':len(sources),'external_paper_requests':0,'engine':'FastEmbed ONNX CPU'}

def hybrid(pid,query,kind='research',limit=12,remote=False,force=False):
    from .parsers import keyword_search
    cfg=config(pid);pool=min(100,max(int(cfg['candidate_k']),int(limit)));keyword=keyword_search(pid,query,kind,pool,remote)
    if not cfg['enabled'] and not force:return {'items':keyword[:limit],'candidates':keyword,'mode':'keyword','warnings':[]}
    eligible=db.rows('SELECT s.id,s.file_id,s.kind,f.egress FROM sources s JOIN files f ON f.id=s.file_id WHERE f.project_id=? AND f.active=1',(pid,))
    eligible=[s for s in eligible if (not remote or __import__('app.security',fromlist=['file_allows_remote']).file_allows_remote(s['file_id'],pid)) and (kind!='research' or s['kind']=='evidence') and (kind!='example' or s['kind'] in ('example','counterexample'))]
    if not eligible:return {'items':[],'candidates':[],'mode':'no_eligible_materials','warnings':['没有当前范围内可用的资料，需补充材料或核对外发权限'],'coverage':{'indexed':0,'eligible':0}}
    import numpy as np
    query_vector=embed([query],cfg['model'])[0];vector_rows=db.rows('SELECT s.*,f.name,f.role,f.egress,f.extension,f.active,p.page_number,p.title AS page_title,p.review_state,v.vector,v.dimension,v.text_hash FROM neural_vectors v JOIN sources s ON s.id=v.source_id JOIN files f ON f.id=s.file_id LEFT JOIN pages p ON p.id=s.page_id WHERE f.project_id=? AND f.active=1 AND v.model=?',(pid,model_key(cfg['model'])))
    semantic=[]
    for row in vector_rows:
        if remote and not __import__('app.security',fromlist=['file_allows_remote']).file_allows_remote(row['file_id'],pid):continue
        if kind=='research' and row['kind']!='evidence':continue
        if kind=='example' and row['kind'] not in ('example','counterexample'):continue
        if row['text_hash']!=hashlib.sha256(embedding_text(row).encode()).hexdigest():continue
        vector=np.frombuffer(row.pop('vector'),dtype=np.float32)
        if len(vector)!=len(query_vector):continue
        row['semantic_score']=float(vector@query_vector/(np.linalg.norm(vector)*np.linalg.norm(query_vector)));semantic.append(row)
    if not semantic:raise ValueError('没有当前模型版本的有效语义索引，请先建立或恢复索引')
    semantic.sort(key=lambda s:s['semantic_score'],reverse=True);candidates={};scores={}
    for channel,rows in [('keyword',keyword),('neural',semantic[:pool])]:
        for rank,item in enumerate(rows,1):
            sid=item['id'];candidates.setdefault(sid,item.copy());candidates[sid].setdefault('channels',[]).append(channel);scores[sid]=scores.get(sid,0)+1/(60+rank)
    merged=sorted(candidates.values(),key=lambda s:scores[s['id']],reverse=True)[:pool]
    if cfg['rerank']=='fusion':
        ranked=[{**s,'fusion_score':scores[s['id']],'rerank_score':scores[s['id']],'rank':-scores[s['id']]} for s in merged]
        return {'items':ranked[:limit],'candidates':merged,'mode':'neural_keyword_rrf','reranker':'fusion_only','warnings':[] if len(semantic)==len(eligible) else [f'语义索引覆盖{len(semantic)}/{len(eligible)}，建议增量更新'],'coverage':{'indexed':len(semantic),'eligible':len(eligible)},'model':cfg['model']}
    if cfg['rerank']=='cross_encoder' and merged:
        scorer=model_instance(cfg['cross_encoder'],cross=True)
        rerank_scores=list(scorer.rerank(query,[embedding_text(s) for s in merged]))
    else:
        # Local bi-encoder rescoring; it is explicitly not labelled cross-encoder reranking.
        vectors=embed([embedding_text(s) for s in merged],cfg['model']) if merged else []
        rerank_scores=[.75*float(v@query_vector/(np.linalg.norm(v)*np.linalg.norm(query_vector)))+.25*scores[s['id']]/max(scores.values()) for s,v in zip(merged,vectors)]
    ranked=[]
    for s,score in zip(merged,rerank_scores):ranked.append({**s,'fusion_score':scores[s['id']],'rerank_score':float(score),'rank':-float(score)})
    ranked.sort(key=lambda s:s['rerank_score'],reverse=True)
    return {'items':ranked[:limit],'candidates':merged,'mode':'neural_keyword_rrf','reranker':cfg['rerank'],'warnings':[] if len(semantic)==len(eligible) else [f'语义索引覆盖{len(semantic)}/{len(eligible)}，新资料仍可通过关键词进入候选，请增量更新索引'],'coverage':{'indexed':len(semantic),'eligible':len(eligible)},'model':cfg['model']}

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
        r=hybrid(pid,case['query'],case.get('kind','all'),k,force=True);candidate_ids=[s['id'] for s in r['candidates']];final_ids=[s['id'] for s in r['items']];keyword_ids=[s['id'] for s in __import__('app.parsers',fromlist=['keyword_search']).keyword_search(pid,case['query'],case.get('kind','all'),k)]
        results.append({'query':case['query'],'candidate':metrics(candidate_ids,gold,len(candidate_ids)),'reranked':metrics(final_ids,gold,k),'keyword':metrics(keyword_ids,gold,k),'candidate_ids':candidate_ids,'final_ids':final_ids,'candidate_k':len(candidate_ids),'fusion':metrics(candidate_ids,gold,k)})
    summary={stage:{key:sum(r[stage][key] for r in results)/len(results) for key in ('recall','mrr','ndcg')} for stage in ('candidate','fusion','reranked','keyword')}
    return {'cases':results,'summary':summary,'final_k':k,'annotation_status':'submitted_unverified','human_acceptance':False}
