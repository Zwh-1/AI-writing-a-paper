import json,re,hashlib,time,urllib.parse,os,contextvars,functools,threading
import httpx
from contextlib import contextmanager
from . import db
from .config import DEFAULT_GATEWAY
from .network import proxy_for
from .security import get_key,redact as security_redact
class ProviderError(Exception):
    def __init__(self,message,unknown=False,retryable=False,retry_after=None,response_result=None,fallback_allowed=False): super().__init__(message);self.unknown=unknown;self.retryable=retryable;self.retry_after=retry_after;self.response_result=response_result;self.fallback_allowed=fallback_allowed
DEFAULT={'streaming':False,'base_url':DEFAULT_GATEWAY,'protocol':os.environ.get('PAPER_PROTOCOL','openai'),'model':os.environ.get('PAPER_MODEL','deepseek-v4.1-flash-1017'),'max_output_tokens':8192,'timeout_seconds':600,'input_price':None,'output_price':None,'currency':'USD','context_limit':100000,'max_request_cost':2.0,'json_mode':False,'max_attempts':3,'supports_images':False,'thinking_enabled':False}
_REQUEST=contextvars.ContextVar('provider_request_snapshot',default=None)
_LOCKS={};_LOCK_GUARD=threading.Lock()
def settings_lock():
    from filelock import FileLock
    from . import security
    path=str(security.DATA/'.provider-settings.lock')
    with _LOCK_GUARD:
        if path not in _LOCKS:_LOCKS[path]=FileLock(path,timeout=20)
    return _LOCKS[path]
def settings_atomic(function):
    @functools.wraps(function)
    def wrapped(*args,**kwargs):
        with settings_lock():return function(*args,**kwargs)
    return wrapped

def frozen_request(function):
    @functools.wraps(function)
    def wrapped(*args,**kwargs):
        if _REQUEST.get() is not None:return function(*args,**kwargs)
        with settings_lock():snapshot={'config':config(),'key':get_key()}
        token=_REQUEST.set(snapshot)
        try:return function(*args,**kwargs)
        finally:_REQUEST.reset(token)
    return wrapped

@contextmanager
def request_scope(configuration,key):
    """Use an isolated provider snapshot without changing global settings."""
    token=_REQUEST.set({"config":dict(configuration),"key":key})
    try:yield
    finally:_REQUEST.reset(token)

def config():
    snapshot=_REQUEST.get()
    return dict(snapshot['config']) if snapshot is not None else {**DEFAULT,**db.setting('provider',{})}
def redact(message):
    result=security_redact(message);snapshot=_REQUEST.get();key=snapshot.get('key') if snapshot else None
    return result.replace(key,'[已隐藏密钥]') if key else result

def endpoint(base,path):
    base=base.rstrip('/')
    if base.endswith('/v1') and path.startswith('/v1/'): return base+path[3:]
    return base+path

def headers(protocol):
    state=_REQUEST.get();key=state['key'] if state is not None else get_key()
    if not key: raise ProviderError('请先在设置页填写 API key',fallback_allowed=True)
    if protocol not in ('openai','responses'):raise ProviderError('接口协议已停用，请使用 Chat Completions 或 Responses')
    return {'Authorization':'Bearer '+key,'Content-Type':'application/json'}
def client(c): return httpx.Client(timeout=httpx.Timeout(float(c.get('timeout_seconds',600)),connect=12),proxy=proxy_for(c['base_url']),trust_env=False,follow_redirects=False)
def cloudflare_error(operation='模型请求'):
    return operation+'被Cloudflare网页访问防护拦截（HTTP 403），未收到API JSON，尚不能判断密钥是否有效。请检查代理软件是否已连接、系统代理是否已启用，再到“接口与设置”检测模型；仍被拦截时，请向服务方确认API专用地址或放行API请求。已保存密钥不受影响，本地候选仍可核对'

def readable_error(message):
    """Present legacy task errors without rewriting their persisted history."""
    if message is None:return None
    text=redact(message);lower=text.lower()
    if re.search(r'http\s*403\b',lower) and ('<!doctype html' in lower or '<html' in lower) and 'cloudflare' in lower:
        return cloudflare_error()
    return text

def reject_html_gateway(response,operation):
    prefix=response.text[:2000].lstrip('\ufeff \t\r\n').lower()
    html='text/html' in response.headers.get('content-type','').lower() or prefix.startswith(('<!doctype html','<html'))
    if response.status_code==403 and html and ('cloudflare' in response.headers.get('server','').lower() or 'cloudflare' in prefix):
        raise ProviderError(cloudflare_error(operation),fallback_allowed=True)

def response_error(response,operation='中转站'):
    """Only explicit rejections may trigger another provider; unknown outcomes never do."""
    reject_html_gateway(response,operation)
    if response.status_code==429:
        try:delay=max(0,min(20,float(response.headers.get('retry-after',''))))
        except ValueError:delay=None
        raise ProviderError(operation+'明确拒绝本次请求：触发速率限制',retryable=True,retry_after=delay,fallback_allowed=True)
    if response.status_code>=500:raise ProviderError(f'{operation} HTTP {response.status_code}，远端是否执行未知，请核实后重试',unknown=True)
    if response.status_code>=400:
        try:payload=response.json()
        except ValueError:payload={}
        detail=str(payload.get('error','')) if isinstance(payload,dict) else ''
        unsupported=response.status_code==400 and bool(re.search(r'not.support|unsupported|image|vision|不支持|图片|图像',detail,re.I))
        raise ProviderError(f'{operation} HTTP {response.status_code}：'+redact(response.text[:700]),fallback_allowed=response.status_code in (401,402,403,404) or unsupported)

@frozen_request
def models():
    c=config();path='/v1/models'
    with client(c) as h:
        try: response=h.get(endpoint(c['base_url'],path),headers=headers(c['protocol']))
        except httpx.RequestError as e: raise ProviderError('连接中转站失败：'+redact(type(e).__name__)) from e
    reject_html_gateway(response,'模型列表查询')
    if response.status_code>=400: raise ProviderError(f'中转站返回 HTTP {response.status_code}，请检查密钥、协议和权限')
    data=response.json();items=data.get('data',[])
    values=[{'id':x['id'],'name':x.get('displayName') or x['id']} for x in items if isinstance(x,dict) and isinstance(x.get('id'),str) and 'gemini' not in x['id'].lower()]
    db.set_setting('capabilities',{'base_url':c['base_url'],'tested_at':db.now(),'models':values,'model_list':True,'generation':'not_tested','protocol':c['protocol']})
    return values

@frozen_request
def generate(system,user,images=None):
    c=config()
    if c.get('agent_transport'):
        from .writing_agent.provider_adapter import transport
        return transport(system,user,c,images)
    if not c.get('model'): raise ProviderError('请先选择中转站实际支持的模型 ID')
    protocol=c['protocol'];limit=int(c.get('max_output_tokens',8192));images=images or []
    if protocol not in ('openai','responses') or 'gemini' in c['model'].lower():raise ProviderError('模型配置已停用，请选择 DeepSeek 或 GPT')
    if protocol=='responses':
        path='/v1/responses';content=[{'type':'input_text','text':user}]+[{'type':'input_image','image_url':'data:'+i['mime']+';base64,'+i['data']} for i in images]
        body={'model':c['model'],'instructions':system,'input':[{'role':'user','content':content}],'max_output_tokens':limit,'store':False}
    else:
        path='/v1/chat/completions';content=user if not images else [{'type':'text','text':user}]+[{'type':'image_url','image_url':{'url':'data:'+i['mime']+';base64,'+i['data']}} for i in images]
        output_key='max_completion_tokens' if re.search(r'(?:^|/)gpt-(?:[5-9]|[1-9][0-9])',c['model'],re.I) else 'max_tokens'
        body={'model':c['model'],'messages':[{'role':'system','content':system},{'role':'user','content':content}],output_key:limit,'stream':False}
        if c.get('json_mode'): body['response_format']={'type':'json_object'}
        if 'thinking_enabled' in c:body['thinking']={'type':'enabled' if c['thinking_enabled'] else 'disabled'}
        if c.get('reasoning_effort') in ('low','high','max'):body['reasoning_effort']=c['reasoning_effort']
    with client(c) as h:
        try: r=h.post(endpoint(c['base_url'],path),headers=headers(protocol),json=body)
        except httpx.RequestError as e: raise ProviderError('请求已提交但未取得最终响应，请先核实账单或任务状态，再决定重试。'+type(e).__name__,unknown=True) from e
    response_error(r)
    return read_generation(r,c,path,body)


def read_generation(r,c,path,body):
    protocol=c['protocol']
    try: data=r.json()
    except ValueError as e: raise ProviderError('中转站没有返回有效 JSON',unknown=True) from e
    if protocol=='responses':
        text=''.join(p.get('text','') for item in data.get('output',[]) for p in item.get('content',[]) if p.get('type')=='output_text');finish=data.get('status');usage=data.get('usage',{})
    else:
        choices=data.get('choices',[])
        if not choices: raise ProviderError('中转站未返回 choices')
        text=choices[0].get('message',{}).get('content') or '';finish=choices[0].get('finish_reason');u=data.get('usage',{})
        usage={'input_tokens':u.get('prompt_tokens'),'output_tokens':u.get('completion_tokens'),'raw':u}
    cost=None
    if c.get('input_price') is not None and c.get('output_price') is not None and usage.get('input_tokens') is not None and usage.get('output_tokens') is not None:
        cost=(usage['input_tokens']*float(c['input_price'])+usage['output_tokens']*float(c['output_price']))/1_000_000
    result={'text':text,'usage':usage,'cost':cost,'remote_id':data.get('id'),'request_hash':hashlib.sha256(db.dumps({'path':path,'body':body}).encode()).hexdigest()}
    if finish in ('length','MAX_TOKENS','incomplete'):raise ProviderError('模型输出被截断，请减少单次篇幅或调整输出上限',response_result=result)
    if not text:raise ProviderError('模型返回内容为空',response_result=result)
    return result
def parse_json(text):
    cleaned=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
    try: return json.loads(cleaned)
    except ValueError:
        start=cleaned.find('{');end=cleaned.rfind('}')
        if start>=0 and end>start: return json.loads(cleaned[start:end+1])
        raise ProviderError('模型未返回约定 JSON，原始输出作为未接受候选保存，不能自动应用')


def usage_endpoint(base_url):
    """Match the relay contract supplied by the user; no /v1 duplication."""
    return base_url.rstrip('/').removesuffix('/v1')+'/v1/usage'

def normalize_account_usage(response):
    from decimal import Decimal,InvalidOperation
    import math
    if not isinstance(response,dict):raise ProviderError('余额接口未返回JSON对象')
    payload=response
    if not any(k in response for k in ('remaining','quota','balance','is_active','isValid')) and isinstance(response.get('data'),dict):payload=response['data']
    quota=payload.get('quota') if isinstance(payload.get('quota'),dict) else {}
    def first(*values):return next((v for v in values if v is not None),None)
    amount=first(payload.get('remaining'),quota.get('remaining'),payload.get('balance'))
    exact=None;numeric=None
    if amount is not None:
        if isinstance(amount,bool) or not isinstance(amount,(str,int,float)):raise ProviderError('余额字段不是有效数值，未按零余额处理')
        try:
            decimal=Decimal(str(amount).strip())
            if not decimal.is_finite():raise InvalidOperation
            numeric=float(decimal)
            if not math.isfinite(numeric):raise InvalidOperation
            exact=format(decimal,'f')
        except (InvalidOperation,ValueError,OverflowError) as error:raise ProviderError('无法解析余额字段，未按零余额处理') from error
    reported=first(payload.get('is_active'),payload.get('isValid'))
    if reported is None:valid=True;validity_source='default_assumed'
    elif isinstance(reported,bool):valid=reported;validity_source='response'
    elif reported in (0,1):valid=bool(reported);validity_source='response'
    elif isinstance(reported,str) and reported.strip().lower() in ('true','false','1','0'):valid=reported.strip().lower() in ('true','1');validity_source='response'
    else:valid=None;validity_source='unrecognized'
    supplied_unit=first(payload.get('unit'),quota.get('unit'));unit=str(supplied_unit) if isinstance(supplied_unit,(str,int,float)) else 'USD'
    return {'is_valid':valid,'validity_source':validity_source,'remaining':numeric,'remaining_exact':exact,'unit':unit,'unit_source':'response' if supplied_unit is not None else 'default_USD','balance_known':exact is not None,'response_scope':'data' if payload is not response else 'root'}

@frozen_request
def account_usage():
    c=config();url=usage_endpoint(c['base_url'])
    with client({**c,'timeout_seconds':20}) as connection:
        try:response=connection.get(url,headers=headers('openai'))
        except httpx.RequestError as error:raise ProviderError('余额接口连接失败：'+type(error).__name__) from error
    reject_html_gateway(response,'余额查询')
    if response.status_code>=400:raise ProviderError(f'余额查询返回HTTP {response.status_code}，请核对中转站接口权限；已保存密钥不受影响')
    try:raw=response.json()
    except ValueError as error:raise ProviderError('余额接口没有返回有效JSON；未将其视为零余额') from error
    result={**normalize_account_usage(raw),'base_url':c['base_url'],'request_url':url,'fetched_at':db.now(),'http_status':response.status_code,'scope':'account','includes_api_key':False}
    # Persist only normalized billing data. Raw response/headers may contain credentials.
    db.set_setting('account_usage',result)
    history=db.setting('account_usage_history',[]);history.append(result);db.set_setting('account_usage_history',history[-30:])
    return result
