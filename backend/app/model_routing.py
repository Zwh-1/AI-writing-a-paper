"""DeepSeek primary and separately authenticated GPT backup, with audited calls."""
import hashlib,math,re
from contextlib import contextmanager
from urllib.parse import urlsplit
from fastapi import HTTPException
from . import db,provider,security

PROFILE='gpt-fallback'
DEFAULT={**provider.DEFAULT,'base_url':'https://www.ecoapi.cc/v1','protocol':'openai','model':'','enabled':True,'supports_images':True,'streaming':False,'max_attempts':5,'json_mode':True,'input_price':None,'output_price':None}


def _route_identity(task,payload):
    return hashlib.sha256(db.dumps([task['project_id'],payload.get('provider_endpoint_id'),
                                   payload.get('provider_profile')]).encode()).hexdigest()


def _automatic_route(task,payload):
    saved=db.setting('model_routing_task:'+task['id'],{})
    return saved if isinstance(saved,dict) and saved.get('identity')==_route_identity(task,payload) else {}


def _save_automatic_route(task,payload,failures,reason):
    db.set_setting('model_routing_task:'+task['id'],{
        'identity':_route_identity(task,payload),'route':'gpt',
        'consecutive_failures':failures,'reason':provider.redact(reason),'updated_at':db.now()})


def _check_cancelled(task):
    live=db.one('SELECT cancel_requested,status FROM tasks WHERE id=? AND project_id=?',
                (task['id'],task['project_id']))
    if live and (live['cancel_requested'] or live['status']=='cancelled'):
        raise InterruptedError('用户已请求取消')


@contextmanager
def _task_snapshot(task,preserve_primary_scope=False):
    """A task's automatic GPT route must never become another task's primary."""
    existing=provider._REQUEST.get()
    foreign=existing and existing.get('task_id') not in (None,task['id'])
    isolate=foreign and (not preserve_primary_scope or existing.get('route')=='gpt')
    token=provider._REQUEST.set(None) if isolate else None
    try:yield provider._REQUEST.get()
    finally:
        if token is not None:provider._REQUEST.reset(token)


def config():return {**DEFAULT,**db.setting('gpt_fallback_provider',{})}


def ready(c=None):
    c=c or config()
    return bool(c.get('enabled') and c.get('model') and security.key_status(PROFILE)['configured'])


@provider.settings_atomic
def settings():
    c=config();caps=db.setting('gpt_fallback_capabilities',{})
    if caps.get('base_url')!=c['base_url'] or caps.get('protocol')!=c['protocol']:caps={}
    return {'provider':c,'credential':security.key_status(PROFILE),'capabilities':caps,'ready':ready(c)}


@provider.settings_atomic
def save_settings(body):
    if not isinstance(body.get('provider',{}),dict):raise HTTPException(422,'GPT 配置格式无效')
    previous=config();c={**previous,**{k:v for k,v in body.get('provider',{}).items() if k in DEFAULT}}
    url=urlsplit(c['base_url'])
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:raise HTTPException(422,'GPT 接口须使用不含凭据的 HTTPS 地址')
    c['base_url']=c['base_url'].rstrip('/')
    if c['protocol'] not in ('openai','responses'):raise HTTPException(422,'GPT 仅支持 Chat Completions 或 Responses')
    if not isinstance(c['model'],str) or c['model'] and not re.fullmatch(r'(?:[\w.-]+/)?gpt[\w.:-]*',c['model'],re.I):raise HTTPException(422,'备选模型须使用服务商返回的 GPT 模型 ID')
    for key in ('enabled','supports_images','json_mode','streaming'):
        if not isinstance(c[key],bool):raise HTTPException(422,'GPT 开关格式无效')
    for key,low,high in [('max_output_tokens',128,1000000),('timeout_seconds',15,3600),('context_limit',512,2000000),('max_request_cost',0.000001,1000000),('max_attempts',1,5)]:
        v=c[key]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not low<=v<=high:raise HTTPException(422,'GPT 配置数值无效：'+key)
        if key=='max_attempts' and type(v) is not int:raise HTTPException(422,'最多尝试次数（含首次）应为1至5的整数')
    for key in ('input_price','output_price'):
        v=c[key]
        if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0):raise HTTPException(422,'GPT Token 单价须为非负有限数值')
    if 'api_key' in body:
        if not isinstance(body['api_key'],str):raise HTTPException(422,'GPT API key 必须是单行文字')
        security.save_key(body['api_key'].strip(),PROFILE)
    db.set_setting('gpt_fallback_provider',c)
    if 'api_key' in body or any(c[k]!=previous[k] for k in ('base_url','protocol','model')):db.set_setting('gpt_fallback_capabilities',{})
    return settings()


def snapshot():
    with provider.settings_lock():
        c=config();key=security.get_key(PROFILE)
    if not c['enabled']:raise provider.ProviderError('GPT 备选已关闭')
    if not key or not c['model']:raise provider.ProviderError('请在“GPT 备选”保存有权限的密钥并选择 GPT 模型')
    return c,key


@provider.settings_atomic
def models():
    c=config();key=security.get_key(PROFILE)
    with provider.request_scope(c,key):
        try:
            with provider.client(c) as h:r=h.get(provider.endpoint(c['base_url'],'/v1/models'),headers=provider.headers(c['protocol']))
        except provider.httpx.RequestError as e:raise provider.ProviderError('GPT 模型查询连接失败：'+type(e).__name__) from e
        provider.response_error(r,'GPT 模型查询')
        try:items=r.json().get('data',[])
        except (ValueError,AttributeError) as e:raise provider.ProviderError('GPT 模型列表不是有效 JSON') from e
        values=[{'id':m['id'],'name':m.get('displayName') or m['id']} for m in items if isinstance(m,dict) and isinstance(m.get('id'),str) and re.fullmatch(r'(?:[\w.-]+/)?gpt[\w.:-]*',m['id'],re.I)]
        db.set_setting('gpt_fallback_capabilities',{'base_url':c['base_url'],'protocol':c['protocol'],'models':values,'tested_at':db.now(),'generation':'not_tested'})
    return {'models':values,'message':'已取得 GPT 模型列表' if values else '当前密钥没有返回 GPT 模型，请在 EcoAPI 开通权限或更换密钥'}


@contextmanager
def task_scope(task):
    """Assemble writing with the same route, key and limits used for sending."""
    from .summary_billing import priced_config
    payload=db.loads(task.get('payload'),{})
    strict_primary=payload.get('provider_profile')=='primary' and task['kind']=='provider_image_probe'
    forced=payload.get('provider_profile')=='gpt' or (not strict_primary and _automatic_route(task,payload).get('route')=='gpt')
    with provider.settings_lock(),_task_snapshot(task,preserve_primary_scope=True) as existing:
        if forced:
            cfg,key=(dict(existing['config']),existing['key']) if existing and existing.get('route')=='gpt' else (config(),security.get_key(PROFILE))
        else:
            cfg,key=_primary_snapshot(payload,existing)
    if forced and not (cfg.get('enabled') and cfg.get('model') and key):
        raise provider.ProviderError('请在“GPT 备选”保存有权限的密钥并选择 GPT 模型')
    options={**(existing or {}).get('request_options',{}),**payload.get('request_options',{})}
    cfg=provider.apply_request_options(priced_config(cfg,key),options)
    with provider.request_scope(cfg,key,route='gpt' if forced else 'deepseek',request_options=options):
        provider._REQUEST.get()['task_id']=task['id']
        yield


def _primary_snapshot(payload,existing=None):
    """Resolve a saved endpoint for this task without activating it globally."""
    if existing and existing['config'].get('provider_endpoint_id'):
        return dict(existing['config']),existing['key']
    endpoint_id=payload.get('provider_endpoint_id')
    if endpoint_id is None:
        return (dict(existing['config']),existing['key']) if existing else (provider.config(),provider.get_key())
    if not isinstance(endpoint_id,str) or not endpoint_id.strip():
        raise provider.ProviderError('任务 DeepSeek 接口标识无效')
    from . import deepseek_pool,deepseek_profiles
    endpoint=next((ep for ep in deepseek_pool.config()['endpoints'] if ep.get('id')==endpoint_id),None)
    if endpoint is None:raise provider.ProviderError('任务指定的 DeepSeek 接口不存在')
    if not endpoint.get('enabled',True):raise provider.ProviderError('任务指定的 DeepSeek 接口已关闭')
    if not isinstance(endpoint.get('model'),str) or not endpoint['model'].strip():
        raise provider.ProviderError('任务指定的 DeepSeek 接口尚未选择模型')
    key=deepseek_pool.endpoint_key(endpoint)
    if not key:raise provider.ProviderError('任务指定的 DeepSeek 接口尚未配置密钥')
    endpoint=deepseek_profiles._autobind_pricing(endpoint)
    # The marker remains in copied request configurations, including billing scopes.
    return {**deepseek_pool._endpoint_to_provider(endpoint),'provider_endpoint_id':endpoint_id},key


def call(executor,task,system,user,manifest,images=None):
    from .tasks import event
    payload=db.loads(task.get('payload'),{});profile=payload.get('provider_profile')
    _check_cancelled(task)
    strict_primary=profile=='primary' and task['kind']=='provider_image_probe'
    automatic={} if strict_primary else _automatic_route(task,payload)
    forced=profile=='gpt' or automatic.get('route')=='gpt'
    with provider.settings_lock(),_task_snapshot(task) as existing:
        primary,key=(provider.config(),'') if forced else _primary_snapshot(payload,existing)
        backup=config();backup_key=security.get_key(PROFILE) if backup['enabled'] and backup['model'] else ''
        if forced and existing and existing.get('route')=='gpt':backup,backup_key=dict(existing['config']),existing['key']
    available=bool(backup['enabled'] and backup['model'] and backup_key)
    def execute(c,k,route,reason=None):
        from .summary_billing import priced_config
        options={**(existing or {}).get('request_options',{}),**manifest.get('request_options',{})}
        if manifest.get('streaming_text') and not images:options['streaming']=True
        selected=provider.apply_request_options(priced_config(c,k),options)
        if manifest.get('agent_transport') or (existing or {}).get('config',{}).get('agent_transport'):
            agent_cfg=(existing or {}).get('config',{})
            selected.update(agent_transport=True,streaming=False,json_mode=agent_cfg.get('json_mode',True))
            if agent_cfg.get('max_output_tokens'):
                selected['max_output_tokens']=min(int(selected['max_output_tokens']),int(agent_cfg['max_output_tokens']))
        if images:selected.update(streaming=False,json_mode=False)
        if route=='gpt':selected.pop('thinking_enabled',None)
        elif selected.get('thinking_enabled') is False:selected.pop('reasoning_effort',None)
        meta={**manifest,'provider_route':route,'request_options':options}
        if route=='gpt':meta.pop('provider_endpoint_id',None)
        if selected.get('provider_endpoint_id'):meta['provider_endpoint_id']=selected['provider_endpoint_id']
        if reason:meta.update(fallback_reason=provider.redact(reason),automatic_retry_index=0)
        with provider.route_attempt_budget(route,selected.get('max_attempts',5)):
            with provider.request_scope(selected,k,route='gpt' if route=='gpt' else (existing or {}).get('route') or 'deepseek',request_options=options):
                provider._REQUEST.get()['task_id']=task['id']
                result=executor(task,system,user,meta,images)
        if route=='gpt' and (automatic or reason):
            _save_automatic_route(task,payload,0,reason or automatic.get('reason',''))
            # A whole-paper scope can span many stages. Its later packing must
            # see GPT's actual limits too, without changing global settings.
            if existing and existing.get('task_id')==task['id']:
                existing.update(config=dict(selected),key=k,route='gpt')
        return result
    if forced:
        if not available:raise provider.ProviderError('GPT 备选尚未配置完成，请在接口与设置中检测并选择 GPT 模型')
        return execute(backup,backup_key,'gpt',automatic.get('reason'))
    if images and not primary.get('supports_images',False) and not strict_primary:
        if not available or not backup.get('supports_images'):raise provider.ProviderError('当前 DeepSeek 未启用图片能力，GPT 备选也未就绪；可先使用本地 OCR，或完成 GPT 图片模型配置')
        event(task['id'],'使用 GPT 识别图片',40)
        return execute(backup,backup_key,'gpt','当前 DeepSeek 接口未启用图片输入')
    try:return execute(primary,key,'deepseek')
    except provider.ProviderError as e:
        if strict_primary or not provider.safe_fallback_failure(e) or not available:raise
        # Retryable failures first exhaust DeepSeek's own bounded allowance;
        # explicit credential/model rejections can still switch immediately.
        if not e.fallback_allowed and not getattr(e,'attempt_budget_exhausted',False):raise
        if images and not backup.get('supports_images'):raise
        run_id=getattr(e,'run_id',None)
        if getattr(e,'has_stream_output',False) or run_id and db.one('SELECT 1 FROM stream_chunks WHERE run_id=? LIMIT 1',(run_id,)):raise
        _check_cancelled(task)
        failures=getattr(e,'consecutive_failures',0)
        event(task['id'],f'DeepSeek 连续{failures}次安全失败，自动切换 GPT 备选' if failures else 'DeepSeek 明确拒绝本次请求，自动切换 GPT 备选',
              manifest.get('progress_percent',45),{'provider_route':'gpt','deepseek_consecutive_failures':failures})
        _save_automatic_route(task,payload,failures,str(e))
        return execute(backup,backup_key,'gpt',str(e))


@provider.settings_atomic
def migrate_from_summary(gpt_model=''):
    """Move the existing EcoAPI credential to GPT and reuse the user's DeepSeek profile."""
    from pathlib import Path
    import sqlite3
    from . import material_summary
    if db.setting('deepseek_gpt_migration'):
        return {'already_migrated':True,'primary_model':provider.config()['model'],'gpt_model':config()['model']}
    if db.one("SELECT id FROM tasks WHERE status IN ('queued','running') LIMIT 1"):raise ValueError('请在当前任务结束后迁移模型设置')
    ds=material_summary.config();ds_key=security.get_key(material_summary.PROFILE)
    if not ds_key:raise ValueError('请先配置 DeepSeek 密钥')
    old=provider.config();old_key=security.get_key()
    previous_gpt=security.get_key(PROFILE)
    reusable=old['base_url'].rstrip('/').removesuffix('/v1')=='https://www.ecoapi.cc'
    if not previous_gpt and old_key and not reusable:raise ValueError('原主接口不是 EcoAPI，请单独配置 GPT 密钥后迁移')
    primary={**provider.DEFAULT,**old,**{k:ds[k] for k in ('base_url','model','protocol','context_limit','timeout_seconds','input_price','output_price','currency','max_request_cost','max_attempts')},'streaming':False,'json_mode':True,'supports_images':False,'thinking_enabled':ds['thinking_enabled']}
    fallback={**DEFAULT,**db.setting('gpt_fallback_provider',{}),'base_url':'https://www.ecoapi.cc/v1','protocol':'openai','model':gpt_model or config()['model'],'enabled':True}
    if 'gemini' in primary['model'].lower():raise ValueError('DeepSeek 模型配置无效')
    if fallback['model'] and not re.fullmatch(r'(?:[\w.-]+/)?gpt[\w.:-]*',fallback['model'],re.I):raise ValueError('GPT 模型配置无效')
    folder=Path(security.DATA)/'backups';folder.mkdir(exist_ok=True)
    backup_path=folder/('before-deepseek-gpt-'+db.uid()+'.sqlite3')
    src=db.connect();dst=sqlite3.connect(backup_path)
    try:src.backup(dst)
    finally:dst.close();src.close()
    original_path=Path(security.DATA)/'credential.dpapi'
    original_bytes=original_path.read_bytes() if original_path.exists() else None
    if not previous_gpt and old_key:security.save_key(old_key,PROFILE)
    try:
        security.save_key(ds_key)
        with db.transaction():
            db.set_setting('provider',primary);db.set_setting('gpt_fallback_provider',fallback)
            for k in ('capabilities','gpt_fallback_capabilities'):db.set_setting(k,{})
            db.set_setting('account_usage',None);db.set_setting('account_usage_history',[])
            db.set_setting('deepseek_gpt_migration',{'completed_at':db.now(),'backup':backup_path.name})
            db.audit(None,'model_routing_migrated',{'primary_model':primary['model'],'gpt_model':fallback['model'],'backup':backup_path.name,'credentials_included_in_database_backup':False})
    except Exception:
        if original_bytes is None:original_path.unlink(missing_ok=True)
        else:original_path.write_bytes(original_bytes)
        raise
    return {'primary_model':primary['model'],'gpt_model':fallback['model'],'gpt_ready':ready(fallback),'database_backup':backup_path.name}
