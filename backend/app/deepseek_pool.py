"""DeepSeek multi-endpoint pool: automatic failover, retry, and circuit breaker.

Each endpoint carries its own base_url, model, protocol and per-endpoint key.
The pool persists endpoint configuration in the settings table and stores
per-endpoint keys in a single Windows DPAPI-encrypted JSON blob, so the main
credential file remains the primary endpoint key for backward compatibility.

Failover strategy:
  1. Requests start at the highest-priority healthy endpoint (or the next one
     after a rotation marker when mode == 'round_robin').
  2. On a retryable failure the same endpoint is retried up to
     strategy.retry_same_endpoint times with exponential backoff.
  3. After that the pool rotates to the next enabled endpoint.
  4. When all endpoints are exhausted, control returns to the caller (model
     routing), which may then fall back to the GPT backup provider.

Health / circuit breaker:
  An endpoint accumulates consecutive_failures. When it reaches
  circuit_breaker.failure_threshold it is marked 'open' (skipped) for
  circuit_breaker.cooldown_seconds; after the cooldown it returns to 'half_open'
  and the next request is a probe. A success moves it back to 'active'.
"""
import hashlib, json, math, re, time, threading, uuid
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import HTTPException
from . import db, provider, security

POOL_SETTING = 'deepseek_pool'
KEYS_FILE = 'deepseek-pool-keys.dpapi'
MAX_ENDPOINTS = 6

DEFAULT_STRATEGY = {
    'mode': 'priority',              # 'priority' | 'round_robin'
    'retry_same_endpoint': 4,        # four retries plus the initial attempt
    'rotate_delay_base': 0.4,
    'rotate_delay_max': 12.0,
    'failover_on': [
        'connect_error', 'rate_limit', 'http_401', 'http_402',
        'http_403', 'http_404', 'http_429', 'timeout',
    ],
    'circuit_breaker': {
        'enabled': True,
        'failure_threshold': 3,
        'cooldown_seconds': 120,
    },
}

DEFAULT = {
    'strategy': DEFAULT_STRATEGY,
    'endpoints': [],
}

# Fields allowed on each endpoint; anything else is dropped on save.
ENDPOINT_FIELDS = (
    'id', 'name', 'enabled', 'priority', 'base_url', 'protocol', 'model',
    'max_output_tokens', 'timeout_seconds', 'context_limit', 'max_request_cost',
    'input_price', 'output_price', 'currency', 'json_mode', 'supports_images',
    'thinking_enabled', 'streaming', 'max_attempts', 'credential_ref',
)

# Credential storage reference: 'primary' uses the existing credential.dpapi,
# 'pool' uses the pooled keys blob.
CREDENTIAL_PRIMARY = 'primary'
CREDENTIAL_POOL = 'pool'


def _now():
    return time.time()


def _hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def config():
    """Return the whole pool configuration (strategy + endpoints)."""
    saved = db.setting(POOL_SETTING, {})
    strategy = {**DEFAULT_STRATEGY, **saved.get('strategy', {})}
    breaker = {**DEFAULT_STRATEGY['circuit_breaker'], **strategy.get('circuit_breaker', {})}
    strategy['circuit_breaker'] = breaker
    endpoints = [e for e in saved.get('endpoints', []) if isinstance(e, dict)]
    return {'strategy': strategy, 'endpoints': endpoints,
            **({k:saved.get(k) for k in ('unified','active_endpoint_id')} if saved.get('unified') else {})}


def _read_keys():
    """Read the pooled keys JSON; returns {endpoint_id: key}."""
    path = security.DATA / KEYS_FILE
    if not path.exists():
        return {}
    try:
        raw = security.protect(path.read_bytes(), True).decode('utf-8')
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_keys(keys):
    """Encrypt and atomically write the pooled keys JSON."""
    import tempfile
    payload = json.dumps(keys, ensure_ascii=False, separators=(',', ':'))
    encrypted = security.protect(payload.encode('utf-8'))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.pool-keys-', suffix='.tmp',
                                         dir=security.DATA, delete=False) as f:
            temporary = Path(f.name)
            f.write(encrypted)
            f.flush()
            import os
            os.fsync(f.fileno())
        import os
        os.replace(temporary, security.DATA / KEYS_FILE)
    except OSError as error:
        raise HTTPException(503, '多端点密钥无法写入数据目录，请检查目录权限和磁盘空间') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def endpoint_key(endpoint):
    """Return the key for an endpoint, reading the right storage."""
    ref = endpoint.get('credential_ref', CREDENTIAL_PRIMARY)
    if ref == CREDENTIAL_PRIMARY:
        return security.get_key()
    keys = _read_keys()
    return keys.get(endpoint.get('id'), '')


def endpoint_key_status(endpoint):
    """Return {'configured': bool, 'masked': str} for a single endpoint."""
    key = endpoint_key(endpoint)
    return {'configured': bool(key), 'masked': ('••••' + key[-4:]) if key else ''}


def _endpoint_to_provider(endpoint):
    """Build a provider config dict for a single endpoint.

    Carries the adaptive pricing basis (expense_pricing_basis, pricing_source)
    through so a freshly switched endpoint is accepted by the expense preflight
    without a manual tariff import.
    """
    return {
        **provider.DEFAULT,
        **{k: endpoint.get(k) for k in provider.DEFAULT if k in endpoint and endpoint.get(k) is not None},
        'base_url': endpoint.get('base_url', provider.DEFAULT['base_url']),
        'protocol': endpoint.get('protocol', 'openai'),
        'model': endpoint.get('model', provider.DEFAULT['model']),
        # Adaptive pricing metadata survives the conversion.
        **{k: endpoint[k] for k in ('expense_pricing_basis', 'pricing_source')
           if endpoint.get(k) is not None},
    }


def _validate_endpoint(endpoint):
    """Validate a single endpoint dict; returns the cleaned dict."""
    allowed = {k: endpoint.get(k) for k in ENDPOINT_FIELDS if k in endpoint}
    # 修正可能存在的首尾空格，避免 base_url 带空格导致请求失败
    if 'base_url' in allowed and isinstance(allowed['base_url'], str):
        allowed['base_url'] = allowed['base_url'].strip()
    url = urlsplit(str(allowed.get('base_url', '')).rstrip('/'))
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise HTTPException(422, 'DeepSeek 端点须使用不含凭据的 HTTPS 地址')
    if url.query or url.fragment:
        raise HTTPException(422, 'DeepSeek 地址不能包含查询参数或片段')
    if allowed.get('protocol', 'openai') not in ('openai', 'responses'):
        raise HTTPException(422, '端点协议无效，请使用 Chat Completions 或 Responses')
    model = str(allowed.get('model', ''))
    if model and ('gemini' in model.lower() or not re.fullmatch(r'(?:[\w.-]+/)?[\w.:-]+', model)):
        raise HTTPException(422, '端点模型 ID 无效，请使用服务商返回的模型 ID')
    if not isinstance(allowed.get('enabled', True), bool):
        raise HTTPException(422, '端点启用开关格式无效')
    if 'priority' in allowed and (isinstance(allowed['priority'], bool) or
                                  not isinstance(allowed['priority'], (int, float)) or
                                  not math.isfinite(allowed['priority']) or allowed['priority'] < 1):
        raise HTTPException(422, '端点优先级须为不小于 1 的数值')
    for name, low, high in (('max_output_tokens', 128, 1000000),
                            ('timeout_seconds', 15, 3600),
                            ('context_limit', 512, 2000000),
                            ('max_request_cost', 0.000001, 1000000),
                            ('max_attempts', 1, 5)):
        value = allowed.get(name, provider.DEFAULT[name])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
           not math.isfinite(value) or not low <= value <= high:
            raise HTTPException(422, '端点配置数值无效：' + name)
        if name == 'max_attempts' and type(value) is not int:
            raise HTTPException(422, '最多尝试次数（含首次）须为1至5的整数')
    for name in ('input_price', 'output_price'):
        value = allowed.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                  not math.isfinite(value) or value < 0):
            raise HTTPException(422, '端点 Token 单价须为非负有限数值')
    if not isinstance(allowed.get('credential_ref', CREDENTIAL_PRIMARY), str) or \
       allowed.get('credential_ref', CREDENTIAL_PRIMARY) not in (CREDENTIAL_PRIMARY, CREDENTIAL_POOL):
        raise HTTPException(422, '端点凭据存储类型无效')
    clean = {
        'id': allowed.get('id') or uuid.uuid4().hex[:12],
        'name': str(allowed.get('name', '') or '端点'),
        'enabled': bool(allowed.get('enabled', True)),
        'priority': float(allowed.get('priority', 1)),
        'base_url': str(allowed.get('base_url', '')).rstrip('/'),
        'protocol': allowed.get('protocol', 'openai'),
        'model': model,
        'max_output_tokens': int(allowed.get('max_output_tokens', provider.DEFAULT['max_output_tokens'])),
        'timeout_seconds': float(allowed.get('timeout_seconds', provider.DEFAULT['timeout_seconds'])),
        'context_limit': int(allowed.get('context_limit', provider.DEFAULT['context_limit'])),
        'max_request_cost': float(allowed.get('max_request_cost', provider.DEFAULT['max_request_cost'])),
        'input_price': allowed.get('input_price'),
        'output_price': allowed.get('output_price'),
        'currency': str(allowed.get('currency', 'USD')),
        'json_mode': bool(allowed.get('json_mode', provider.DEFAULT['json_mode'])),
        'supports_images': bool(allowed.get('supports_images', provider.DEFAULT['supports_images'])),
        'thinking_enabled': bool(allowed.get('thinking_enabled', provider.DEFAULT['thinking_enabled'])),
        'streaming': bool(allowed.get('streaming', provider.DEFAULT['streaming'])),
        'max_attempts': int(allowed.get('max_attempts', provider.DEFAULT['max_attempts'])),
        'credential_ref': allowed.get('credential_ref', CREDENTIAL_PRIMARY),
    }
    # Preserve health if re-saving an existing endpoint.
    existing = next((e for e in config()['endpoints'] if e.get('id') == clean['id']), None)
    if existing:
        clean['health'] = dict(existing.get('health', {}))
    else:
        clean['health'] = {'status': 'active', 'consecutive_failures': 0,
                           'last_failure': None, 'last_success': None}
    return clean


def settings():
    """Return the pool settings view for the API."""
    cfg = config()
    view = []
    for ep in cfg['endpoints']:
        item = dict(ep)
        item['credential'] = endpoint_key_status(ep)
        view.append(item)
    return {'strategy': cfg['strategy'], 'endpoints': view, 'max_endpoints': MAX_ENDPOINTS}


@provider.settings_atomic
def save_settings(body):
    """Save strategy and/or endpoint list. Returns settings()."""
    if not isinstance(body, dict):
        raise HTTPException(422, '请求体必须为对象')
    current = config()
    if current.get('unified') and 'endpoints' in body:
        raise HTTPException(409, '请通过统一的 DeepSeek 接口列表新增或编辑接口')
    strategy = current['strategy']
    if 'strategy' in body:
        supplied = body['strategy']
        if not isinstance(supplied, dict):
            raise HTTPException(422, '故障切换策略格式无效')
        mode = supplied.get('mode', strategy['mode'])
        if mode not in (('manual', 'priority', 'round_robin') if current.get('unified') else ('priority', 'round_robin')):
            raise HTTPException(422, '切换模式须为 priority 或 round_robin')
        retry = supplied.get('retry_same_endpoint', strategy['retry_same_endpoint'])
        if isinstance(retry, bool) or not isinstance(retry, (int, float)) or \
           not math.isfinite(retry) or not 0 <= int(retry) <= 10:
            raise HTTPException(422, '同端点重试次数须在 0 至 10 之间')
        strategy = {**strategy, 'mode': mode, 'retry_same_endpoint': int(retry)}
        if 'circuit_breaker' in supplied and isinstance(supplied['circuit_breaker'], dict):
            cb = supplied['circuit_breaker']
            breaker = dict(strategy['circuit_breaker'])
            if 'enabled' in cb and isinstance(cb['enabled'], bool):
                breaker['enabled'] = cb['enabled']
            if 'failure_threshold' in cb:
                v = cb['failure_threshold']
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 1 <= int(v) <= 20:
                    raise HTTPException(422, '熔断阈值须在 1 至 20 之间')
                breaker['failure_threshold'] = int(v)
            if 'cooldown_seconds' in cb:
                v = cb['cooldown_seconds']
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 1 <= v <= 86400:
                    raise HTTPException(422, '熔断冷却时间须在 1 至 86400 秒之间')
                breaker['cooldown_seconds'] = float(v)
            strategy['circuit_breaker'] = breaker
    endpoints = []
    if 'endpoints' in body:
        supplied = body['endpoints']
        if not isinstance(supplied, list) or len(supplied) > MAX_ENDPOINTS:
            raise HTTPException(422, f'端点列表须为不超过 {MAX_ENDPOINTS} 项的数组')
        endpoints = [_validate_endpoint(e) for e in supplied if isinstance(e, dict)]
        # Save per-endpoint keys provided in the same request.
        for ep in supplied:
            if isinstance(ep, dict) and ep.get('api_key'):
                _save_endpoint_key(ep.get('id') or '', ep['api_key'])
    else:
        endpoints = current['endpoints']
    # Accept a flat endpoint_keys mapping {id_or_name: key} for batch saves.
    if 'endpoint_keys' in body and isinstance(body['endpoint_keys'], dict):
        for eid, key in body['endpoint_keys'].items():
            if isinstance(key, str) and key.strip():
                _save_endpoint_key(eid, key)
    saved = {**current, 'strategy': strategy, 'endpoints': endpoints}
    db.set_setting(POOL_SETTING, saved)
    return settings()


def _save_endpoint_key(endpoint_id, value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or '\n' in value or '\r' in value:
        raise HTTPException(422, '端点密钥格式无效，请粘贴完整的单行密钥')
    keys = _read_keys()
    keys[endpoint_id] = value.strip()
    _write_keys(keys)


@provider.settings_atomic
def save_endpoint_key(endpoint_id, value):
    """Persist a single endpoint key. Returns settings()."""
    if not endpoint_id:
        raise HTTPException(422, '端点标识缺失')
    _save_endpoint_key(endpoint_id, value)
    return settings()


def _healthy(endpoint):
    """Whether an endpoint is currently eligible (not open)."""
    health = endpoint.get('health', {})
    status = health.get('status', 'active')
    if status == 'active':
        return True
    if status == 'open':
        cooldown = config()['strategy']['circuit_breaker'].get('cooldown_seconds', 120)
        last = health.get('last_failure') or 0
        if _now() - last >= cooldown:
            return True
        return False
    # half_open allows a probe.
    return True


def _sort_endpoints(endpoints):
    return sorted(endpoints, key=lambda e: (e.get('priority', 1), e.get('name', '')))


def endpoints():
    """Enabled endpoints sorted by priority, with round-robin rotation."""
    cfg = config()
    from .official_api_policy import is_official, status as official_policy_status
    official_blocked=official_policy_status()['blocked']
    enabled = _sort_endpoints([e for e in cfg['endpoints'] if e.get('enabled', True)
                              and not (official_blocked and is_official(e))])
    if cfg.get('unified'):
        active=cfg.get('active_endpoint_id')
        if cfg['strategy'].get('mode')=='manual':
            return [e for e in enabled if e.get('id')==active]
        enabled.sort(key=lambda e:e.get('id')!=active)
    if cfg['strategy'].get('mode') == 'round_robin':
        marker = db.setting('deepseek_pool_rotation', 0)
        count = max(1, len(enabled))
        offset = marker % count
        enabled = enabled[offset:] + enabled[:offset]
    return enabled


def active_endpoint():
    """Return the first healthy endpoint, or None."""
    for ep in endpoints():
        if _healthy(ep):
            return ep
    return None


@provider.settings_atomic
def mark_success(endpoint_id):
    """Record a successful call for an endpoint."""
    cfg = config()
    for ep in cfg['endpoints']:
        if ep.get('id') == endpoint_id:
            ep['health'] = {'status': 'active', 'consecutive_failures': 0,
                            'last_failure': None, 'last_success': _now()}
            db.set_setting(POOL_SETTING, cfg)
            return


@provider.settings_atomic
def mark_failure(endpoint_id, error_kind='unknown'):
    """Record a failure; may open the circuit breaker."""
    cfg = config()
    breaker = cfg['strategy']['circuit_breaker']
    for ep in cfg['endpoints']:
        if ep.get('id') == endpoint_id:
            health = dict(ep.get('health', {}))
            failures = int(health.get('consecutive_failures', 0)) + 1
            if breaker.get('enabled', True) and failures >= int(breaker.get('failure_threshold', 3)):
                health = {'status': 'open', 'consecutive_failures': failures,
                          'last_failure': _now(), 'last_success': health.get('last_success'),
                          'last_error': error_kind}
            else:
                health = {'status': 'active', 'consecutive_failures': failures,
                          'last_failure': _now(), 'last_success': health.get('last_success'),
                          'last_error': error_kind}
            ep['health'] = health
            db.set_setting(POOL_SETTING, cfg)
            return


@provider.settings_atomic
def rotate():
    """Advance the round-robin marker by one endpoint. Returns the new index."""
    cfg = config()
    count = max(1, len([e for e in cfg['endpoints'] if e.get('enabled', True)]))
    marker = int(db.setting('deepseek_pool_rotation', 0))
    marker = (marker + 1) % count
    db.set_setting('deepseek_pool_rotation', marker)
    return marker


def classify_error(error):
    """Map a ProviderError to a failover kind, or None if not failover-worthy."""
    if not isinstance(error, provider.ProviderError):
        return None
    if error.unknown:
        return None
    if error.retryable:
        return 'rate_limit'
    # Fall back on the message for HTTP status hints.
    text = str(error)
    if any(s in text for s in ('401', '402', '403', '404')):
        return 'http_' + next(s for s in ('401', '402', '403', '404') if s in text)
    if '429' in text:
        return 'rate_limit'
    if any(s in text for s in ('connect', '连接')):
        return 'connect_error'
    if any(s in text for s in ('超时', 'timeout', 'Timeout')):
        return 'timeout'
    return 'unknown'


def retry_delay(attempt, error_kind, retry_after=None, base=None):
    """Exponential backoff with jitter for a given attempt (0-based)."""
    cfg = config()
    base = base if base is not None else float(cfg['strategy'].get('rotate_delay_base', 0.4))
    cap = float(cfg['strategy'].get('rotate_delay_max', 12.0))
    if error_kind == 'rate_limit' and retry_after is not None:
        return min(cap, float(retry_after or 0))
    delay = min(cap, base * (2 ** attempt))
    return delay
