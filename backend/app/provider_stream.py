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
    fragments=[];usage={};remote_id=None;finished=False;finish=None
    with provider.client(c) as connection:
        try:
            with connection.stream('POST',provider.endpoint(c['base_url'],path),headers=provider.headers(protocol),json=body) as response:
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
        except httpx.RequestError as e:raise provider.ProviderError('流连接中断，远端执行情况未知；片段已保存，未自动重发',unknown=True) from e
    cost=None
    if c.get('input_price') is not None and c.get('output_price') is not None and usage.get('input_tokens') is not None and usage.get('output_tokens') is not None:cost=(usage['input_tokens']*float(c['input_price'])+usage['output_tokens']*float(c['output_price']))/1e6
    result={'streamed':True,'text':''.join(fragments),'usage':usage,'cost':cost,'remote_id':remote_id,'request_hash':hashlib.sha256(db.dumps({'path':path,'body':body}).encode()).hexdigest()}
    if finish in ('length','MAX_TOKENS','incomplete'):raise provider.ProviderError('模型输出被截断，已保存响应和用量',response_result=result)
    if not finished:raise provider.ProviderError('未收到完整完成事件，保留流式片段供恢复检查',unknown=True)
    if not fragments:raise provider.ProviderError('模型返回内容为空',response_result=result)
    return result
