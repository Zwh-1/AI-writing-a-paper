"""Isolated GraphRAG 3.3.0 worker. JSON-lines RPC carries no provider secrets."""
import asyncio
import os
import json
import sys
import threading
import traceback
import runpy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
sys.path.insert(0,str(ROOT/'.runtime/graphrag-deps'))
wire=sys.stdout
sys.stdout=sys.stderr
lock=threading.Lock()

def emit(value):
    wire.write(json.dumps(value,ensure_ascii=False,default=str)+'\n');wire.flush()

def rpc(kind,body):
    with lock:
        emit({'event':kind,**body});reply=json.loads(sys.stdin.readline())
        if 'error' in reply:raise RuntimeError(reply['error'])
        return reply

def register_models():
    parse_json=runpy.run_path(str(ROOT/'backend/app/knowledge_json.py'))['parse']
    from graphrag_llm.completion import LLMCompletion,register_completion
    from graphrag_llm.embedding import LLMEmbedding,register_embedding
    from graphrag_llm.tokenizer import Tokenizer,register_tokenizer
    from graphrag_llm.utils import create_completion_response,structure_completion_response,create_embedding_response
    from graphrag_llm.types import LLMCompletionChunk,LLMChoiceChunk,LLMChoiceDelta
    class BytesTokenizer(Tokenizer):
        def __init__(self,**kw):pass
        def encode(self,text):return list(text.encode('utf-8'))
        def decode(self,values):return bytes(values).decode('utf-8',errors='replace')
    class MeteredCompletion(LLMCompletion):
        def __init__(self,tokenizer,metrics_store,**kw):self._tokenizer=tokenizer;self._metrics_store=metrics_store
        def _response(self,**kw):
            fmt=kw.get('response_format');schema=fmt.model_json_schema() if fmt is not None else None
            messages=kw.get('messages',[])
            if not isinstance(messages,(list,str)):messages=str(messages)
            result=rpc('completion',{'messages':messages,'response_schema':schema})
            response=create_completion_response(result['text']);response.id=result.get('run_id','workbench');response.model=result.get('model','workbench-metered')
            usage=result.get('usage') or {}
            if usage.get('input_tokens') is not None or usage.get('prompt_tokens') is not None:
                response.usage.prompt_tokens=int(usage.get('input_tokens',usage.get('prompt_tokens',0)));response.usage.completion_tokens=int(usage.get('output_tokens',usage.get('completion_tokens',0)));response.usage.total_tokens=response.usage.prompt_tokens+response.usage.completion_tokens
            else:response.usage=None
            if fmt is not None:
                _,canonical,repaired=parse_json(response.content)
                if repaired:
                    response.choices[0].message.content=canonical
                    emit({'event':'protocol_notice','kind':'syntax_only_normalization','run_id':response.id,'facts_changed':False})
                response.formatted_response=structure_completion_response(canonical,fmt)
            return response
        def _chunks(self,response):
            return [LLMCompletionChunk(id=response.id,object='chat.completion.chunk',created=response.created,model=response.model,
                choices=[LLMChoiceChunk(index=0,delta=LLMChoiceDelta(role='assistant',content=response.content),finish_reason=None)]),
                LLMCompletionChunk(id=response.id,object='chat.completion.chunk',created=response.created,model=response.model,
                choices=[LLMChoiceChunk(index=0,delta=LLMChoiceDelta(content=''),finish_reason='stop')],usage=response.usage)]
        def completion(self,**kw):
            response=self._response(**kw)
            return iter(self._chunks(response)) if kw.get('stream') else response
        async def completion_async(self,**kw):
            response=self._response(**kw)
            if not kw.get('stream'):return response
            async def completed_chunks():
                for chunk in self._chunks(response):yield chunk
            return completed_chunks()
        @property
        def tokenizer(self):return self._tokenizer
        @property
        def metrics_store(self):return self._metrics_store
    class LocalEmbedding(LLMEmbedding):
        def __init__(self,tokenizer,metrics_store,**kw):self._tokenizer=tokenizer;self._metrics_store=metrics_store
        def embedding(self,**kw):
            values=kw.get('input',[]);values=[values] if isinstance(values,str) else values
            result=rpc('embedding',{'texts':values});vectors=result['vectors']
            response=create_embedding_response(vectors[0] if vectors else [],batch_size=len(vectors))
            for row,vector in zip(response.data,vectors):row.embedding=vector
            response.model=result['model'];return response
        async def embedding_async(self,**kw):return self.embedding(**kw)
        @property
        def tokenizer(self):return self._tokenizer
        @property
        def metrics_store(self):return self._metrics_store
    register_tokenizer('workbench_bytes',BytesTokenizer)
    register_tokenizer('litellm',BytesTokenizer)
    register_completion('workbench_metered',MeteredCompletion)
    register_embedding('workbench_local',LocalEmbedding)

def config(folder,packet):
    from graphrag.config.models.graph_rag_config import GraphRagConfig
    from graphrag_llm.config import ModelConfig
    tokenizer={'type':'workbench_bytes','model':'bytes'}
    return GraphRagConfig(completion_models={'default_completion_model':ModelConfig(type='workbench_metered',model_provider='workbench',model=packet.get('model','metered'),tokenizer=tokenizer)},
        embedding_models={'default_embedding_model':ModelConfig(type='workbench_local',model_provider='workbench',model=packet.get('embedding_model','local'),tokenizer=tokenizer)},
        concurrent_requests=1,async_mode='asyncio',output_storage={'type':'file','base_dir':str(folder)},input_storage={'type':'file','base_dir':str(folder)},
        cache={'type':'memory'},reporting={'type':'file','base_dir':str(folder/'logs')},
        vector_store={'type':'lancedb','db_uri':str(folder/'vectors'),'vector_size':packet.get('vector_size',1024)},
        cluster_graph={'max_cluster_size':12,'use_lcc':False,'seed':17},
        community_reports={'max_input_length':10000,'max_length':1500},
        local_search={'max_context_tokens':12000,'top_k_entities':8,'top_k_relationships':12},
        drift_search={'n_depth':2,'primer_folds':1,'drift_k_followups':2},
        global_search={'max_context_tokens':14000,'dynamic_search_llm':'default_completion_model'})

async def main(packet):
    import importlib.metadata
    version=importlib.metadata.version('graphrag')
    if version!='3.3.0':raise RuntimeError('GraphRAG版本不匹配：'+version)
    import pandas as pd
    register_models();folder=Path(packet['folder']).resolve();folder.mkdir(parents=True,exist_ok=True);cfg=config(folder,packet)
    if packet['operation']=='diagnose':
        checked=False
        if packet.get('protocol_test'):
            from graphrag_llm.completion import create_completion
            global rpc
            original_rpc=rpc
            try:
                rpc=lambda kind,body:{'text':'受控合成回答','model':'synthetic-protocol-model','usage':{'input_tokens':3,'output_tokens':5}}
                model=create_completion(cfg.get_completion_model_config('default_completion_model'))
                assert model.completion(messages='test').content=='受控合成回答'
                assert ''.join(c.choices[0].delta.content or '' for c in model.completion(messages='test',stream=True))=='受控合成回答'
                response=await model.completion_async(messages='test',stream=True);texts=[]
                async for chunk in response:texts.append(chunk.choices[0].delta.content or '')
                assert ''.join(texts)=='受控合成回答';checked=True
            finally:rpc=original_rpc
        return {'version':version,'config_valid':True,'backend':'microsoft_graphrag','provider':'workbench_metered','synthetic_protocol_test':checked,'stream_transport':'buffered_completed_response'}
    from graphrag import api
    if packet['operation']=='build':
        source=packet['tables']
        for name,rows in source.items():pd.DataFrame(rows).to_parquet(folder/(name+'.parquet'),index=False)
        cfg.workflows=['create_communities']
        if packet.get('reports',True):cfg.workflows.append('create_community_reports')
        if packet.get('embeddings'):cfg.workflows.append('generate_text_embeddings')
        outputs=await api.build_index(config=cfg,input_documents=pd.DataFrame(source.get('documents') or [{'id':'byog','text':'BYOG 已有原文映射','title':'BYOG'}]))
        errors=[{'workflow':o.workflow,'error':str(o.error)} for o in outputs if o.error]
        if errors:raise RuntimeError(json.dumps(errors,ensure_ascii=False))
        return {'version':version,'workflows':[o.workflow for o in outputs],'tables':{p.stem:len(pd.read_parquet(p)) for p in folder.glob('*.parquet')},'backend':'microsoft_graphrag','source_mapping':packet['mapping']}
    mode=packet.get('mode','global');tables={name:pd.read_parquet(folder/(name+'.parquet')) for name in ('entities','relationships','text_units','communities','community_reports')}
    common={'config':cfg,'entities':tables['entities'],'community_reports':tables['community_reports'],'community_level':0,'response_type':'中文回答，保留适用条件和来源编号','query':packet['query']}
    if mode=='global':response,context=await api.global_search(**common,communities=tables['communities'],dynamic_community_selection=False)
    elif mode=='drift':response,context=await api.drift_search(**common,communities=tables['communities'],text_units=tables['text_units'],relationships=tables['relationships'])
    elif mode=='local':response,context=await api.local_search(**common,communities=tables['communities'],text_units=tables['text_units'],relationships=tables['relationships'],covariates=None)
    else:raise ValueError('Unsupported official query mode')
    def serial(value):
        if isinstance(value,pd.DataFrame):return value.to_dict(orient='records')
        if isinstance(value,dict):return {k:serial(v) for k,v in value.items()}
        if isinstance(value,list):return [serial(v) for v in value]
        return value
    return {'response':serial(response),'context':serial(context),'mode':mode,'backend':'microsoft_graphrag','version':version,'stream_transport':'buffered_completed_response'}

if __name__=='__main__':
    try:emit({'event':'result','result':asyncio.run(main(json.loads(sys.stdin.readline())))})
    except BaseException as error:
        traceback.print_exc(file=sys.stderr);emit({'event':'error','error':str(error),'type':type(error).__name__});raise SystemExit(1)
