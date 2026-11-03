import json,re,hashlib,time,urllib.parse,os,contextvars,functools,threading
import httpx
from contextlib import contextmanager,nullcontext
from . import db
from .config import DEFAULT_GATEWAY
from .network import proxy_for
from .security import get_key,redact as security_redact
class ProviderError(Exception):
    def __init__(self,message,unknown=False,retryable=False,retry_after=None,response_result=None,fallback_allowed=False): super().__init__(message);self.unknown=unknown;self.retryable=retryable;self.retry_after=retry_after;self.response_result=response_result;self.fallback_allowed=fallback_allowed
DEFAULT={'streaming':False,'base_url':DEFAULT_GATEWAY,'protocol':os.environ.get('PAPER_PROTOCOL','openai'),'model':os.environ.get('PAPER_MODEL','deepseek-v4.1-flash-1017'),'max_output_tokens':8192,'timeout_seconds':600,'input_price':None,'output_price':None,'currency':'USD','context_limit':100000,'max_request_cost':2.0,'json_mode':False,'max_attempts':3,'supports_images':False,'thinking_enabled':False}
_REQUEST=contextvars.ContextVar('provider_request_snapshot',default=None)
_CLIENT_REQUEST_ID=contextvars.ContextVar('provider_client_request_id',default=None)
_CONNECTION_POOL=contextvars.ContextVar('provider_connection_pool',default=None)
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
def request_scope(configuration,key,route=None):
    """Use an isolated provider snapshot without changing global settings."""
    token=_REQUEST.set({"config":dict(configuration),"key":key,"route":route})
    try:yield
    finally:_REQUEST.reset(token)

def config():
    snapshot=_REQUEST.get()
    if snapshot is not None:return dict(snapshot['config'])
    from . import deepseek_profiles
    active=deepseek_profiles.selected() if deepseek_profiles.enabled() else None
    return deepseek_profiles.pool._endpoint_to_provider(active) if active else {**DEFAULT,**db.setting('provider',{})}
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
    return {'Authorization':'Bearer '+key,'Content-Type':'application/json',**({'X-Client-Request-Id':_CLIENT_REQUEST_ID.get()} if _CLIENT_REQUEST_ID.get() else {})}

@contextmanager
def connection_pool():
    """Reuse transports only inside one explicitly scoped operation."""
    if _CONNECTION_POOL.get() is not None:
        yield
        return
    state={'clients':{},'lock':threading.Lock()};token=_CONNECTION_POOL.set(state)
    try:yield
    finally:
        try:
            for connection in state['clients'].values():connection.close()
        finally:_CONNECTION_POOL.reset(token)

def client(c):
    connect=float(c.get('connect_timeout_seconds',12))
    if not 1<=connect<=120:raise ProviderError('连接等待时间须为1至120秒')
    timeout=float(c.get('timeout_seconds',600));proxy=proxy_for(c['base_url'])
    def create():return httpx.Client(timeout=httpx.Timeout(timeout,connect=connect),proxy=proxy,trust_env=False,follow_redirects=False)
    pool=_CONNECTION_POOL.get()
    if pool is None:return create()
    snapshot=_REQUEST.get() or {}
    credential=hashlib.sha256(str(snapshot.get('key') or '').encode()).hexdigest()
    key=(c['base_url'],proxy,connect,timeout,credential)
    with pool['lock']:
        if key not in pool['clients']:pool['clients'][key]=create()
        return nullcontext(pool['clients'][key])
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
    try:
        return _response_error(response,operation)
    except ProviderError as error:
        # Unknown HTTP outcomes can still carry a provider request ID needed
        # for a later receipt lookup. Never retain raw error bodies/headers.
        error.response_evidence=response_evidence(response,{})
        raise

def _response_error(response,operation='中转站'):
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
        except (httpx.ConnectTimeout,httpx.ConnectError) as e:
            error=ProviderError('建立HTTP连接前失败，未发送模型请求。'+type(e).__name__,retryable=True)
            error.failure_phase='connect_before_http';error.transport_error_type=type(e).__name__
            raise error from e
        except httpx.RequestError as e: raise ProviderError('请求已提交但未取得最终响应，请先核实账单或任务状态，再决定重试。'+type(e).__name__,unknown=True) from e
    response_error(r)
    return read_generation(r,c,path,body)


def _pool_generate_once(system,user,images,ep):
    """Send one generation through a specific pool endpoint.

    Returns (result, ep, key). Raises ProviderError on failure; the
    caller (pool_generate) handles classification, retry and rotation.
    """
    from . import deepseek_pool
    if ep is None:
        # Fall back to the legacy single-config path so existing settings keep working.
        return generate(system,user,images), {'id': None, 'name': 'legacy'}, get_key()
    c=deepseek_pool._endpoint_to_provider(ep)
    key=deepseek_pool.endpoint_key(ep)
    if not key:
        raise ProviderError('端点密钥未配置，无法请求：'+ep.get('name',''))
    if not c.get('model'):
        raise ProviderError('端点未选择模型 ID：'+ep.get('name',''))
    protocol=c['protocol'];limit=int(c.get('max_output_tokens',8192));images=images or []
    if protocol not in ('openai','responses') or 'gemini' in c['model'].lower():
        raise ProviderError('端点模型配置已停用，请选择 DeepSeek 模型：'+ep.get('name',''))
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
    with request_scope(c, key):
        with client(c) as h:
            try: r=h.post(endpoint(c['base_url'],path),headers=headers(protocol),json=body)
            except (httpx.ConnectTimeout,httpx.ConnectError) as e:
                error=ProviderError('建立HTTP连接前失败，未发送模型请求。'+type(e).__name__,retryable=True)
                error.failure_phase='connect_before_http';error.transport_error_type=type(e).__name__
                raise error from e
            except httpx.RequestError as e: raise ProviderError('请求已提交但未取得最终响应，请先核实账单或任务状态，再决定重试。'+type(e).__name__,unknown=True) from e
    response_error(r)
    result=read_generation(r,c,path,body)
    result['provider_snapshot']={k:c[k] for k in ('base_url','protocol','model')}
    return result, ep, key


def pool_generate(system,user,images=None,on_retry=None):
    """Generation through the DeepSeek multi-endpoint pool with failover/retry.

    Strategy:
      - Each healthy endpoint is tried in priority order.
      - Same-endpoint retry up to strategy.retry_same_endpoint times.
      - After exhausting one endpoint, rotate to the next; mark failures.
      - Unknown/executed outcomes are never replayed (they may have charged).

    Returns the first successful result. Raises the last ProviderError when all
    endpoints are exhausted.
    """
    from . import deepseek_pool
    cfg=deepseek_pool.config()
    snapshot=_REQUEST.get() or {}
    endpoints=[] if cfg['strategy'].get('mode')=='manual' or snapshot.get('route') not in (None,'deepseek') else deepseek_pool.endpoints()
    if not endpoints:
        # Legacy single-endpoint configuration still works unchanged.
        result=generate(system,user,images)
        result['provider_snapshot']={k:config()[k] for k in ('base_url','protocol','model')}
        result['pool_used']='legacy'
        return result
    strategy=cfg['strategy']
    same_retry=int(strategy.get('retry_same_endpoint',2))
    last_error=None
    last_attempt=0
    used_endpoint_ids=[]
    # Snapshot enabled endpoints; circuit-open ones are skipped by active_endpoint.
    tried=[]
    for ep in endpoints:
        if not deepseek_pool._healthy(ep):
            continue
        tried.append(ep)
    if not tried:
        tried=endpoints
    for ep in tried:
        used_endpoint_ids.append(ep.get('id'))
        # 故障切换到新端点时自动适配该 API 的计价结构，避免因缺失报价而阻断。
        from . import deepseek_profiles
        ep = deepseek_profiles._autobind_pricing(ep)
        for attempt in range(same_retry+1):
            last_attempt=attempt
            try:
                result,endpoint,key=_pool_generate_once(system,user,images,ep)
                deepseek_pool.mark_success(ep.get('id'))
                if strategy.get('mode')=='round_robin':
                    deepseek_pool.rotate()
                result['pool_used']='pool'
                result['pool_endpoint']=endpoint.get('name') or endpoint.get('id')
                result['pool_attempt']=attempt+1
                return result
            except ProviderError as e:
                last_error=e
                kind=deepseek_pool.classify_error(e)
                deepseek_pool.mark_failure(ep.get('id'),kind or 'unknown')
                if e.unknown:
                    # Unknown outcome may have been charged: never replay.
                    raise
                if e.response_result is not None:
                    # Partial output exists: preserve it, do not retry.
                    raise
                failover_allowed=kind in strategy.get('failover_on',[])
                if not failover_allowed:
                    raise
                if on_retry and attempt<same_retry:
                    delay=deepseek_pool.retry_delay(attempt,kind,retry_after=e.retry_after)
                    on_retry(ep,attempt,kind,delay)
                if attempt<same_retry:
                    continue
        # Exhausted this endpoint; rotate before trying the next.
        if strategy.get('mode')=='round_robin':
            deepseek_pool.rotate()
    if last_error is not None:
        last_error.pool_endpoints_used=used_endpoint_ids
        raise last_error
    raise ProviderError('DeepSeek 多端点池没有可用的端点配置')


def read_generation(r,c,path,body):
    protocol=c['protocol']
    try: data=r.json()
    except ValueError as e: raise ProviderError('中转站没有返回有效 JSON',unknown=True) from e
    if not isinstance(data,dict):raise ProviderError('中转站响应不是JSON对象，执行费用未知',unknown=True)
    from . import adapter
    if protocol=='responses':
        text=''.join(p.get('text','') for item in data.get('output',[]) for p in item.get('content',[]) if p.get('type')=='output_text');finish=data.get('status');raw_usage=data.get('usage',{})
        normalized=adapter.normalize_usage(raw_usage)
        usage={'input_tokens':normalized['input_tokens'],'output_tokens':normalized['output_tokens'],'raw':raw_usage}
    else:
        choices=data.get('choices',[])
        if not choices: raise ProviderError('中转站未返回 choices，执行费用未知',unknown=True)
        text=choices[0].get('message',{}).get('content') or '';finish=choices[0].get('finish_reason');raw_usage=data.get('usage',{})
        normalized=adapter.normalize_usage(raw_usage)
        usage={'input_tokens':normalized['input_tokens'],'output_tokens':normalized['output_tokens'],'raw':raw_usage}
    cost=usage_cost(c,usage)
    result={'text':text,'usage':usage,'cost':cost,'remote_id':data.get('id'),'response_evidence':response_evidence(r,data),'request_hash':hashlib.sha256(db.dumps({'path':path,'body':body}).encode()).hexdigest()}
    if finish in ('length','MAX_TOKENS','incomplete'):raise ProviderError('模型输出被截断，请减少单次篇幅或调整输出上限',response_result=result)
    if not text:raise ProviderError('模型返回内容为空',response_result=result)
    return result

def response_evidence(response,data):
    """Keep a reproducible response fingerprint without raw headers or secrets."""
    return {'provider_request_id':response.headers.get('x-request-id'),'response_id':data.get('id'),
            'reported_model':data.get('model'),'http_status':response.status_code,'received_at':db.now(),
            'response_sha256':hashlib.sha256(response.content).hexdigest(),
            'usage_sha256':hashlib.sha256(db.dumps(data.get('usage',{})).encode()).hexdigest()}
def parse_json(text):
    """Parse model JSON output with layered tolerance.

    Tries in order:
      1. Direct json.loads on the cleaned text.
      2. After stripping code fences (```json / ```JSON / ``` / ~~~ variants), BOM and stray control chars.
      3. After removing trailing commas (non-standard JSON some models emit).
      4. After normalizing single-quoted keys/values to double quotes (Python-dict style output).
      5. After extracting the outermost balanced {...} object from surrounding prose.
    Raises ProviderError with a diagnostic snippet only when all strategies fail.
    """
    if not isinstance(text, str):
        raise ProviderError('模型响应正文必须是字符串')
    original = text
    # 1) strip BOM / zero-width / control chars that break json.loads
    cleaned = text.strip('\ufeff\u200b\u200c\u200d')
    # 2) remove common code fences (```json, ```JSON, ~~~json, bare ``` or ~~~)
    cleaned = re.sub(r'^```(?:json|JSON|Json)?\s*', '', cleaned)
    cleaned = re.sub(r'```\s*$', '', cleaned)
    cleaned = re.sub(r'^~~~(?:json|JSON|Json)?\s*', '', cleaned)
    cleaned = re.sub(r'~~~\s*$', '', cleaned)
    cleaned = cleaned.strip()

    def _try(fragment):
        """Attempt a direct load, then trailing-comma repair, then quote repair."""
        attempts = [fragment]
        # strip trailing commas: ,\n} -> \n}  ,\n] -> \n]
        attempts.append(re.sub(r',(\s*[}\]])', r'\1', fragment))
        # single-quoted JSON: 'key':'value' -> "key":"value" (only when no double quotes present)
        if '"' not in fragment:
            attempts.append(re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', fragment))
        for attempt in attempts:
            try:
                return json.loads(attempt)
            except (ValueError, TypeError):
                continue
        return None

    # 3) try the cleaned text as-is
    parsed = _try(cleaned)
    if parsed is not None:
        return parsed

    # 4) find the outermost balanced {...} object; try each candidate window
    def _balanced_object(s):
        """Return the outermost {...} substring that is bracket-balanced."""
        i = s.find('{')
        while i >= 0:
            depth = 0
            in_str = False
            esc = False
            j = i
            while j < len(s):
                ch = s[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == '\\':
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            return s[i:j + 1]
                j += 1
            i = s.find('{', i + 1)
        return None

    candidate = _balanced_object(cleaned)
    if candidate and candidate != cleaned:
        parsed = _try(candidate)
        if parsed is not None:
            return parsed

    # 5) fall back to the old loose window (first { to last }) when balance scan fails
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start >= 0 and end > start:
        window = cleaned[start:end + 1]
        parsed = _try(window)
        if parsed is not None:
            return parsed

    # 6) build a helpful diagnostic
    preview = original[:200].replace('\n', '\\n')
    raise ProviderError(
        f'模型未返回约定 JSON，原始输出作为未接受候选保存，不能自动应用。'
        f'解析尝试：直接解析、代码围栏清理、尾逗号修复、单引号修复、大括号提取均失败；'
        f'响应预览：{preview!r}')


def usage_endpoint(base_url):
    """Match the relay contract supplied by the user; no /v1 duplication.

    Uses the API-family classifier so every endpoint in a family gets the same
    contract regardless of custom subdomains. Falls back to the legacy rules
    for endpoints the adapter cannot classify.
    """
    from . import adapter
    base = base_url.rstrip('/').removesuffix('/v1')
    family = adapter.detect_api_family(base_url)
    if family == adapter.FAMILY_DEEPSEEK_OFFICIAL:
        return base + '/v1/user/balance'
    if family in (adapter.FAMILY_NEW_API, adapter.FAMILY_X5M5X):
        return base + '/v1/dashboard/billing/subscription'
    # Legacy host heuristics for unclassified relays.
    if 'deepseek.com' in base:
        return base + '/v1/user/balance'
    if 'new.' in base or 'x5m5x.com' in base and not base.startswith('https://api.'):
        return base + '/v1/dashboard/billing/subscription'
    return base + '/v1/usage'


def usage_statistics_endpoint(base_url):
    """Request accounting totals are distinct from the account balance resource.

    Relays that expose the legacy per-model statistics contract use /v1/usage.
    Unsupported services fail closed in reconciliation; a balance is never
    interpreted as evidence for completed request costs.
    """
    return base_url.rstrip('/').removesuffix('/v1') + '/v1/usage'


def normalize_account_usage(response):
    """Normalize any vendor balance response through the adapter.

    Preserves the exact legacy contract (is_valid, remaining_exact, unit,
    validity_source, unit_source) while making it robust to family-specific
    field names. Unknown or malformed amounts are never silently zeroed.
    """
    from . import adapter
    if not isinstance(response, dict):
        raise ProviderError('余额接口未返回JSON对象')
    try:
        result = adapter.normalize_balance(response)
    except ValueError as error:
        raise ProviderError(str(error)) from error
    return result


def usage_cost(cfg, usage):
    """A rate calculation is an estimate; malformed usage is never a zero bill.

    Family defaults are consulted ONLY when the configuration carries an
    explicit adaptive pricing basis (endpoint_probe / endpoint_configured /
    family_default_conservative_estimate). A plain configuration with no
    bound pricing evidence keeps the original semantics: if prices are missing,
    the cost is unknown (None) and the reservation ceiling applies.
    """
    import math
    if not isinstance(usage, dict):
        return None
    if not all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens')):
        return None
    cfg = dict(cfg or {})
    # Resolve any missing price only when an adaptive basis was bound.
    if not all(type(cfg.get(k)) in (int, float) and math.isfinite(cfg[k]) and cfg[k] >= 0 for k in ('input_price', 'output_price')):
        basis = cfg.get('expense_pricing_basis') or cfg.get('pricing_evidence') or {}
        if isinstance(basis, dict) and basis.get('kind') in ('endpoint_configured', 'endpoint_probe', 'family_default_conservative_estimate'):
            for name in ('input_price', 'output_price'):
                if not (type(cfg.get(name)) in (int, float) and math.isfinite(cfg[name]) and cfg[name] >= 0):
                    if type(basis.get(name)) in (int, float) and math.isfinite(basis[name]) and basis[name] >= 0:
                        cfg[name] = basis[name]
    if not all(type(cfg.get(k)) in (int, float) and math.isfinite(cfg[k]) and cfg[k] >= 0 for k in ('input_price', 'output_price')):
        return None
    amount = (usage['input_tokens'] * cfg['input_price'] + usage['output_tokens'] * cfg['output_price']) / 1e6
    return amount if math.isfinite(amount) else None


def auto_bind_pricing(cfg, key=None, response=None):
    """Attempt to resolve pricing for a freshly switched endpoint.

    Priority:
      1. Explicit configured input_price/output_price.
      2. Family default (safety fallback, marked as such).
      3. Pricing extracted from a provider response (e.g. balance/pricing body).

    Returns a tuple (resolved_cfg, source_marker) where source_marker is one of
    'configured', 'family_default', 'response_extracted', 'unresolved'.
    """
    import math
    from . import adapter
    cfg = dict(cfg or {})
    resolved = dict(cfg)
    if all(type(resolved.get(k)) in (int, float) and math.isfinite(resolved[k]) and resolved[k] >= 0
           for k in ('input_price', 'output_price')):
        return resolved, 'configured'
    # Try extracting from a response body when available.
    if isinstance(response, dict):
        pricing = adapter.normalize_pricing(response)
        if pricing:
            for k in ('input_price', 'output_price'):
                if not (type(resolved.get(k)) in (int, float) and math.isfinite(resolved[k]) and resolved[k] >= 0):
                    if pricing.get(k) is not None:
                        resolved[k] = pricing[k]
            resolved.setdefault('currency', pricing.get('currency', resolved.get('currency', 'USD')))
            if all(type(resolved.get(k)) in (int, float) and math.isfinite(resolved[k]) and resolved[k] >= 0
                   for k in ('input_price', 'output_price')):
                return resolved, 'response_extracted'
    # Family default as the final safety net.
    family = adapter.detect_api_family(cfg.get('base_url'), cfg.get('model'))
    defaults = adapter.default_pricing(family, cfg.get('model'))
    if any(defaults.get(k) is not None for k in ('input_price', 'output_price')):
        for k in ('input_price', 'output_price'):
            if not (type(resolved.get(k)) in (int, float) and math.isfinite(resolved[k]) and resolved[k] >= 0):
                if defaults.get(k) is not None:
                    resolved[k] = defaults[k]
        resolved.setdefault('currency', defaults.get('currency', resolved.get('currency', 'USD')))
        return resolved, 'family_default'
    return resolved, 'unresolved'

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
