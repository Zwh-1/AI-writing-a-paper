"""Actual upstream SSE deltas. Partial output is durable but never auto-accepted."""
import hashlib,json,re
import httpx
from . import provider,db

def frames(lines):
    data=[];event=''
    for line in lines:
        if line=='':
            if data:yield event,'\n'.join(data)
            data=[];event='';continue
        if line.startswith(':'):continue
        if line.startswith('event:'):event=line[6:].strip()
        if line.startswith('data:'):data.append(line[5:].lstrip())
    if data:yield event,'\n'.join(data)

@provider.frozen_request
def generate(system,user,images=None,on_delta=lambda text:None):
    if images:raise provider.ProviderError('当前流式适配仅支持文本；图像请求请关闭流式开关后调用')
    c=provider.config();protocol=c['protocol'];limit=int(c['max_output_tokens']);model=c['model']
    if protocol not in ('openai','responses') or 'gemini' in model.lower():raise provider.ProviderError('模型配置已停用，请选择 DeepSeek 或 GPT')
    if protocol=='responses':path='/v1/responses';body={'model':model,'instructions':system,'input':user,'max_output_tokens':limit,'stream':True,'store':False}
    else:
        output_key='max_completion_tokens' if re.search(r'(?:^|/)gpt-(?:[5-9]|[1-9][0-9])',model,re.I) else 'max_tokens'
        path='/v1/chat/completions';body={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':user}],output_key:limit,'stream':True}
        body['stream_options']={'include_usage':True}
        if c.get('json_mode'):body['response_format']={'type':'json_object'}
        if 'thinking_enabled' in c:body['thinking']={'type':'enabled' if c['thinking_enabled'] else 'disabled'}
        if c.get('reasoning_effort') in ('low','high','max'):body['reasoning_effort']=c['reasoning_effort']
    fragments=[];usage={};remote_id=None;finished=False;finish=None;response_received=False
    with provider.client(c) as connection:
        try:
            with connection.stream('POST',provider.endpoint(c['base_url'],path),headers=provider.headers(protocol),json=body) as response:
                response_received=True
                if response.status_code>=400:
                    response.read();provider.response_error(response,'流式接口')
                if 'text/event-stream' not in response.headers.get('content-type',''):
                    response.read();result=provider.read_generation(response,c,path,body);result['streamed']=False
                    return result
                for event,raw in frames(response.iter_lines()):
                    if raw=='[DONE]':continue
                    try:data=json.loads(raw)
                    except ValueError as e:raise provider.ProviderError('流式事件不是有效JSON，已保存此前片段',unknown=True) from e
                    delta='';remote_id=data.get('id',remote_id)
                    if data.get('error'):raise provider.ProviderError('上游流式错误：'+provider.redact(str(data['error'])),unknown=True)
                    if protocol=='responses':
                        kind=data.get('type',event)
                        if kind=='response.output_text.delta':delta=data.get('delta','')
                        if kind=='response.completed':finished=True;info=data.get('response',{});remote_id=info.get('id',remote_id);usage=info.get('usage',{});finish='completed'
                        if kind in ('response.failed','response.incomplete','error'):raise provider.ProviderError('远端响应未完整完成，片段已保留',unknown=True)
                    else:
                        for choice in data.get('choices',[]):
                            delta+=choice.get('delta',{}).get('content') or ''
                            if choice.get('finish_reason'):finish=choice['finish_reason'];finished=finish=='stop'
                        if data.get('usage'):u=data['usage'];usage={'input_tokens':u.get('prompt_tokens'),'output_tokens':u.get('completion_tokens'),'raw':u}
                    if delta:
                        if sum(map(len,fragments))+len(delta)>2_000_000:raise provider.ProviderError('流式输出超过应用大小限制，片段已保留',unknown=True)
                        fragments.append(delta);on_delta(delta)
        except (httpx.ConnectTimeout,httpx.ConnectError) as e:
            if response_received:raise provider.ProviderError('流响应后连接异常，远端执行情况未知',unknown=True) from e
            error=provider.ProviderError('建立HTTP流连接前失败，未发送模型请求。'+type(e).__name__,retryable=True)
            error.failure_phase='connect_before_http';error.transport_error_type=type(e).__name__
            raise error from e
        except httpx.RequestError as e:raise provider.ProviderError('流连接中断，远端执行情况未知；片段已保存，未自动重发',unknown=True) from e
    cost=provider.usage_cost(c,usage)
    result={'streamed':True,'text':''.join(fragments),'usage':usage,'cost':cost,'remote_id':remote_id,'response_evidence':{'provider_request_id':response.headers.get('x-request-id'),'response_id':remote_id,'http_status':response.status_code,'received_at':db.now(),'usage_sha256':hashlib.sha256(db.dumps(usage).encode()).hexdigest()},'request_hash':hashlib.sha256(db.dumps({'path':path,'body':body}).encode()).hexdigest()}
    if finish in ('length','MAX_TOKENS','incomplete'):raise provider.ProviderError('模型输出被截断，已保存响应和用量',response_result=result)
    if not finished:raise provider.ProviderError('未收到完整完成事件，保留流式片段供恢复检查',unknown=True)
    if not fragments:raise provider.ProviderError('模型返回内容为空',response_result=result)
    return result


def pool_generate(system,user,on_delta=lambda text:None):
    """Streaming generation through the DeepSeek multi-endpoint pool.

    Failover is only attempted when no delta has been delivered yet; once any
    output arrives the request is presumed charged and is never replayed.
    """
    from . import deepseek_pool
    cfg=deepseek_pool.config()
    snapshot=provider._REQUEST.get() or {}
    endpoints=[] if cfg['strategy'].get('mode')=='manual' or snapshot.get('route') not in (None,'deepseek') else deepseek_pool.endpoints()
    if not endpoints:
        result=generate(system,user,on_delta=on_delta)
        result['provider_snapshot']={k:provider.config()[k] for k in ('base_url','protocol','model')}
        result['pool_used']='legacy'
        return result
    strategy=cfg['strategy']
    same_retry=int(strategy.get('retry_same_endpoint',2))
    last_error=None
    tried=[ep for ep in endpoints if deepseek_pool._healthy(ep)] or endpoints
    for ep in tried:
        for attempt in range(same_retry+1):
            try:
                result,endpoint,key=_stream_generate_once(system,user,on_delta,ep)
                deepseek_pool.mark_success(ep.get('id'))
                if strategy.get('mode')=='round_robin':
                    deepseek_pool.rotate()
                result['pool_used']='pool'
                result['pool_endpoint']=endpoint.get('name') or endpoint.get('id')
                result['pool_attempt']=attempt+1
                return result
            except provider.ProviderError as e:
                last_error=e
                kind=deepseek_pool.classify_error(e)
                deepseek_pool.mark_failure(ep.get('id'),kind or 'unknown')
                if e.unknown or e.response_result is not None:
                    # May have been charged or partial output exists: never replay.
                    raise
                failover_allowed=kind in strategy.get('failover_on',[])
                if not failover_allowed:
                    raise
                if attempt<same_retry:
                    continue
        if strategy.get('mode')=='round_robin':
            deepseek_pool.rotate()
    if last_error is not None:
        raise last_error
    raise provider.ProviderError('DeepSeek 多端点池没有可用的端点配置')


def _stream_generate_once(system,user,on_delta,ep):
    """Send one streaming generation through a specific pool endpoint.

    Returns (result, ep, key). Raises ProviderError; if any delta was
    delivered the error is marked unknown so the pool will not replay it.
    """
    from . import deepseek_pool
    if ep is None:
        result=generate(system,user,on_delta=on_delta)
        return result, {'id': None, 'name': 'legacy'}, provider.get_key()
    c=deepseek_pool._endpoint_to_provider(ep)
    key=deepseek_pool.endpoint_key(ep)
    if not key:
        raise provider.ProviderError('端点密钥未配置，无法请求：'+ep.get('name',''))
    protocol=c['protocol'];limit=int(c['max_output_tokens']);model=c['model']
    if protocol not in ('openai','responses') or 'gemini' in model.lower():
        raise provider.ProviderError('端点模型配置已停用，请选择 DeepSeek 模型：'+ep.get('name',''))
    if protocol=='responses':
        path='/v1/responses';body={'model':model,'instructions':system,'input':user,'max_output_tokens':limit,'stream':True,'store':False}
    else:
        output_key='max_completion_tokens' if re.search(r'(?:^|/)gpt-(?:[5-9]|[1-9][0-9])',model,re.I) else 'max_tokens'
        path='/v1/chat/completions';body={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':user}],output_key:limit,'stream':True}
        body['stream_options']={'include_usage':True}
        if c.get('json_mode'):body['response_format']={'type':'json_object'}
        if 'thinking_enabled' in c:body['thinking']={'type':'enabled' if c['thinking_enabled'] else 'disabled'}
        if c.get('reasoning_effort') in ('low','high','max'):body['reasoning_effort']=c['reasoning_effort']
    fragments=[];usage={};remote_id=None;finished=False;finish=None;response_received=False
    with provider.request_scope(c,key):
        with provider.client(c) as connection:
            try:
                with connection.stream('POST',provider.endpoint(c['base_url'],path),headers=provider.headers(protocol),json=body) as response:
                    response_received=True
                    if response.status_code>=400:
                        response.read();provider.response_error(response,'流式接口')
                    if 'text/event-stream' not in response.headers.get('content-type',''):
                        response.read();result=provider.read_generation(response,c,path,body);result['streamed']=False
                        return result, ep, key
                    for event,raw in frames(response.iter_lines()):
                        if raw=='[DONE]':continue
                        try:data=json.loads(raw)
                        except ValueError as e:raise provider.ProviderError('流式事件不是有效JSON，已保存此前片段',unknown=True) from e
                        delta='';remote_id=data.get('id',remote_id)
                        if data.get('error'):raise provider.ProviderError('上游流式错误：'+provider.redact(str(data['error'])),unknown=True)
                        if protocol=='responses':
                            kind=data.get('type',event)
                            if kind=='response.output_text.delta':delta=data.get('delta','')
                            if kind=='response.completed':finished=True;info=data.get('response',{});remote_id=info.get('id',remote_id);usage=info.get('usage',{});finish='completed'
                            if kind in ('response.failed','response.incomplete','error'):raise provider.ProviderError('远端响应未完整完成，片段已保留',unknown=True)
                        else:
                            for choice in data.get('choices',[]):
                                delta+=choice.get('delta',{}).get('content') or ''
                                if choice.get('finish_reason'):finish=choice['finish_reason'];finished=finish=='stop'
                            if data.get('usage'):u=data['usage'];usage={'input_tokens':u.get('prompt_tokens'),'output_tokens':u.get('completion_tokens'),'raw':u}
                        if delta:
                            if sum(map(len,fragments))+len(delta)>2_000_000:raise provider.ProviderError('流式输出超过应用大小限制，片段已保留',unknown=True)
                            fragments.append(delta);on_delta(delta)
            except (httpx.ConnectTimeout,httpx.ConnectError) as e:
                if response_received:raise provider.ProviderError('流响应后连接异常，远端执行情况未知',unknown=True) from e
                error=provider.ProviderError('建立HTTP流连接前失败，未发送模型请求。'+type(e).__name__,retryable=True)
                error.failure_phase='connect_before_http';error.transport_error_type=type(e).__name__
                raise error from e
            except httpx.RequestError as e:raise provider.ProviderError('流连接中断，远端执行情况未知；片段已保存，未自动重发',unknown=True) from e
    cost=provider.usage_cost(c,usage)
    result={'streamed':True,'text':''.join(fragments),'usage':usage,'cost':cost,'remote_id':remote_id,'response_evidence':{'provider_request_id':response.headers.get('x-request-id'),'response_id':remote_id,'http_status':response.status_code,'received_at':db.now(),'usage_sha256':hashlib.sha256(db.dumps(usage).encode()).hexdigest()},'request_hash':hashlib.sha256(db.dumps({'path':path,'body':body}).encode()).hexdigest()}
    result['provider_snapshot']={k:c[k] for k in ('base_url','protocol','model')}
    if finish in ('length','MAX_TOKENS','incomplete'):raise provider.ProviderError('模型输出被截断，已保存响应和用量',response_result=result)
    if not finished:raise provider.ProviderError('未收到完整完成事件，保留流式片段供恢复检查',unknown=True)
    if not fragments:raise provider.ProviderError('模型返回内容为空',response_result=result)
    return result, ep, key
