"""One DeepSeek provider list shared by writing, summaries and model queries."""
import copy
import hashlib
import re
import uuid
from fastapi import APIRouter, HTTPException
from . import db, deepseek_pool as pool, provider, security

router = APIRouter(prefix='/deepseek')
MAX_PROFILES = 32


def enabled():
    return bool(db.setting(pool.POOL_SETTING, {}).get('unified'))


def selected():
    cfg = db.setting(pool.POOL_SETTING, {})
    return next((e for e in cfg.get('endpoints', [])
                 if e.get('id') == cfg.get('active_endpoint_id')), None)


def _commit(cfg, keys=None):
    old_keys = pool._read_keys() if keys is not None else None
    if keys is not None:
        pool._write_keys(keys)
    try:
        db.set_setting(pool.POOL_SETTING, cfg)
    except Exception:
        if old_keys is not None:
            pool._write_keys(old_keys)
        raise


def _initialize():
    if enabled():
        return
    old = pool.config()
    entries, keys, seen = [], pool._read_keys(), set()

    def add(configuration, key, name, extra=None):
        identity = (configuration.get('base_url', '').rstrip('/').removesuffix('/v1'),
                    configuration.get('model'), hashlib.sha256(key.encode()).hexdigest())
        if identity in seen:
            return
        seen.add(identity)
        eid = uuid.uuid4().hex[:12]
        entry = {**provider.DEFAULT, **configuration, **(extra or {}), 'id': eid,
                 'name': name, 'credential_ref': 'pool', 'enabled': True,
                 'priority': len(entries) + 1}
        entries.append(entry)
        if key:
            keys[eid] = key

    primary = {**provider.DEFAULT, **db.setting('provider', {})}
    add(primary, security.get_key(), 'DeepSeek 主接口',
        {'account_usage': db.setting('account_usage'), 'capabilities': db.setting('capabilities', {})})
    # Read every legacy credential before enabling unified credential resolution.
    summary = db.setting('material_summary_provider')
    summary_key = security.get_key('material-summary')
    if summary or summary_key:
        from .material_summary import DEFAULT
        add({**DEFAULT, **(summary or {})}, summary_key, '原资料总结接口',
            {'account_usage': db.setting('material_summary_account_usage')})
    for endpoint in old['endpoints']:
        add(pool._endpoint_to_provider(endpoint), pool.endpoint_key(endpoint),
            endpoint.get('name') or 'DeepSeek 接口', {'health': endpoint.get('health', {})})
    db.set_setting('deepseek_profiles_migration', {'at': db.now(), 'provider': primary,
                   'material_summary_provider': summary, 'pool': old})
    _commit({'unified': True, 'active_endpoint_id': entries[0]['id'],
             'strategy': {**old['strategy'], 'mode': 'manual'}, 'endpoints': entries}, keys)


@provider.settings_atomic
def settings():
    _initialize()
    result = pool.settings()
    result.update(active_endpoint_id=pool.config().get('active_endpoint_id'),
                  max_endpoints=MAX_PROFILES)
    for ep in result['endpoints']:
        ep['active'] = ep['id'] == result['active_endpoint_id']
    return result


def _find(cfg, eid):
    entry = next((e for e in cfg['endpoints'] if e['id'] == eid), None)
    if entry is None:
        raise HTTPException(404, 'DeepSeek 接口不存在，请刷新后重试')
    return entry


def _reset_current(entry):
    db.set_setting('capabilities', entry.get('capabilities', {}))
    db.set_setting('account_usage', entry.get('account_usage'))
    db.set_setting('material_summary_account_usage', entry.get('account_usage'))


@provider.settings_atomic
def save_endpoint(body, eid=None):
    _initialize()
    cfg = copy.deepcopy(pool.config())
    previous = _find(cfg, eid) if eid else None
    if previous is None and len(cfg['endpoints']) >= MAX_PROFILES:
        raise HTTPException(422, f'最多保存 {MAX_PROFILES} 个 DeepSeek 接口')
    supplied = body.get('provider', {})
    if not isinstance(supplied, dict):
        raise HTTPException(422, 'DeepSeek 接口配置格式无效')
    eid = eid or uuid.uuid4().hex[:12]
    name = body.get('name', (previous or {}).get('name', 'DeepSeek 接口'))
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
        raise HTTPException(422, '请填写不超过 100 字的接口名称')
    candidate = {**provider.DEFAULT, **(previous or {}),
                 **{k: v for k, v in supplied.items() if k in provider.DEFAULT},
                 'id': eid, 'name': name.strip(), 'credential_ref': 'pool',
                 'enabled': True, 'priority': body.get('priority', (previous or {}).get('priority', len(cfg['endpoints']) + 1))}
    if not isinstance(candidate['model'], str) or not re.fullmatch(
            r'(?:[\w.-]+/)?deepseek[\w.:-]*', candidate['model'], re.I):
        raise HTTPException(422, '请选择服务商实际提供的 DeepSeek 模型 ID')
    entry = pool._validate_endpoint(candidate)
    keys = pool._read_keys()
    changed_key = False
    if 'api_key' in body:
        key = body['api_key']
        if not isinstance(key, str) or len(key) > 4096 or '\n' in key or '\r' in key or not key.strip():
            raise HTTPException(422, '请填写完整的单行 API key；留空时请省略该字段以保留密钥')
        changed_key = keys.get(eid) != key.strip()
        keys[eid] = key.strip()
    if not previous and not keys.get(eid):
        raise HTTPException(422, '新增接口需要填写 API key')
    same_account = bool(previous and not changed_key and previous['base_url'] == entry['base_url'])
    entry['account_usage'] = previous.get('account_usage') if same_account else None
    entry['capabilities'] = previous.get('capabilities', {}) if same_account and previous['model'] == entry['model'] and previous['protocol'] == entry['protocol'] else {}
    if not same_account:
        entry['health'] = {'status': 'active', 'consecutive_failures': 0}
    cfg['endpoints'] = [entry if e['id'] == eid else e for e in cfg['endpoints']] if previous else [*cfg['endpoints'], entry]
    _commit(cfg, keys if 'api_key' in body else None)
    if eid == cfg['active_endpoint_id']:
        _reset_current(entry)
    return settings()


@provider.settings_atomic
def save_active(body):
    return save_endpoint(body, pool.config().get('active_endpoint_id'))


@provider.settings_atomic
def activate(eid):
    _initialize()
    cfg = copy.deepcopy(pool.config())
    entry = _find(cfg, eid)
    if not pool.endpoint_key(entry):
        raise HTTPException(422, '请先编辑此接口并保存 API key，再切换使用')
    if not entry.get('model'):
        raise HTTPException(422, '请先为此接口选择 DeepSeek 模型')
    cfg['active_endpoint_id'] = eid
    # Explicit switching always pins this API until the user enables failover again.
    cfg['strategy']['mode'] = 'manual'
    entry['health'] = {'status': 'active', 'consecutive_failures': 0}
    _commit(cfg)
    _reset_current(entry)
    return settings()


@provider.settings_atomic
def remove(eid):
    _initialize()
    cfg = copy.deepcopy(pool.config())
    _find(cfg, eid)
    if eid == cfg['active_endpoint_id']:
        raise HTTPException(409, '正在使用的接口不能删除，请先切换到另一个接口')
    cfg['endpoints'] = [e for e in cfg['endpoints'] if e['id'] != eid]
    keys = pool._read_keys()
    keys.pop(eid, None)
    _commit(cfg, keys)
    return settings()


def _snapshot(eid):
    with provider.settings_lock():
        _initialize()
        entry = copy.deepcopy(_find(pool.config(), eid))
        key = pool.endpoint_key(entry)
    if not key:
        raise HTTPException(422, '此接口尚未保存 API key')
    return entry, pool._endpoint_to_provider(entry), key


def inspect_endpoint(eid, kind):
    entry, cfg, key = _snapshot(eid)
    # Query this row without switching the current provider or storing its result globally.
    with provider.request_scope(cfg, key, route='deepseek-inspection'):
        if kind == 'models':
            try:
                with provider.client(cfg) as client:
                    response = client.get(provider.endpoint(cfg['base_url'], '/v1/models'), headers=provider.headers(cfg['protocol']))
            except provider.httpx.RequestError as error:
                raise provider.ProviderError('模型查询连接失败：' + type(error).__name__) from error
            provider.reject_html_gateway(response, 'DeepSeek 模型查询')
            if response.status_code >= 400:
                raise provider.ProviderError('模型查询失败：HTTP ' + str(response.status_code))
            try:
                items = response.json().get('data', [])
                result = {'models': [{'id': x['id'], 'name': x.get('displayName') or x['id']} for x in items
                           if isinstance(x, dict) and isinstance(x.get('id'), str) and re.fullmatch(r'(?:[\w.-]+/)?deepseek[\w.:-]*', x['id'], re.I)]}
            except (ValueError, AttributeError, TypeError) as error:
                raise provider.ProviderError('模型接口未返回有效的模型列表') from error
            return result
        try:
            with provider.client({**cfg, 'timeout_seconds': 20}) as client:
                response = client.get(provider.usage_endpoint(cfg['base_url']), headers=provider.headers('openai'))
        except provider.httpx.RequestError as error:
            raise provider.ProviderError('余额查询连接失败：' + type(error).__name__) from error
        provider.reject_html_gateway(response, '余额查询')
        if response.status_code >= 400:
            raise provider.ProviderError('余额查询失败：HTTP ' + str(response.status_code))
        try:
            result = {**provider.normalize_account_usage(response.json()), 'base_url': cfg['base_url'], 'fetched_at': db.now()}
        except (ValueError, AttributeError, TypeError) as error:
            raise provider.ProviderError('余额接口未返回有效 JSON') from error
    with provider.settings_lock():
        current = copy.deepcopy(pool.config())
        live = _find(current, eid)
        if live['base_url'] != entry['base_url'] or pool.endpoint_key(live) != key:
            raise HTTPException(409, '查询期间接口已修改，请重新查询')
        live['account_usage'] = result
        _commit(current)
        if eid == current['active_endpoint_id']:
            _reset_current(live)
    return result


@router.get('/settings')
def get_settings():
    return settings()


@router.put('/strategy')
@provider.settings_atomic
def save_strategy(body: dict):
    _initialize()
    pool.save_settings({'strategy': body})
    return settings()


@router.post('/endpoints')
def create_endpoint(body: dict):
    return save_endpoint(body)


@router.put('/endpoints/{eid}')
def update_endpoint(eid: str, body: dict):
    return save_endpoint(body, eid)


@router.delete('/endpoints/{eid}')
def delete_endpoint(eid: str):
    return remove(eid)


@router.post('/endpoints/{eid}/activate')
def activate_endpoint(eid: str):
    return activate(eid)


@router.post('/endpoints/{eid}/models')
def models(eid: str):
    return inspect_endpoint(eid, 'models')


@router.post('/endpoints/{eid}/balance')
def balance(eid: str):
    return inspect_endpoint(eid, 'balance')
