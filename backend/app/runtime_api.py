"""Authenticated runtime diagnostics and explicit drain controls; no restarts."""
from fastapi import APIRouter, HTTPException
from . import adapter, db, runtime_identity, security

router = APIRouter()


@router.get('/runtime/status')
def status():
    return runtime_identity.status()


@router.put('/runtime/drain')
@db.atomic
def drain(body: dict):
    if set(body) != {'enabled'} or type(body['enabled']) is not bool:
        raise HTTPException(422, '仅支持布尔值 enabled')
    before = runtime_identity.is_draining()
    db.set_setting('runtime_drain', body['enabled'])
    db.audit(None, 'runtime_drain_changed', {'before': before, 'enabled': body['enabled']})
    return runtime_identity.drain_status()


@router.get('/projects/{pid}/runtime-metrics')
def metrics(pid: str):
    security.require_project(pid)
    groups = {}
    for row in db.rows('SELECT status,model,protocol,usage,manifest FROM generation_runs WHERE project_id=? ORDER BY created_at', (pid,)):
        meta = db.loads(row['manifest'], {})
        stage = meta.get('writing_stage') or meta.get('stage') or meta.get('purpose') or 'other'
        value = groups.setdefault(stage, {'stage': stage, 'requests': 0,
            'input_tokens': None, 'output_tokens': None, 'cache_hit_tokens': None,
            'cache_miss_tokens': None, 'cache_unknown_runs': 0, 'cache_known_runs': 0,
            'local_response_reuses': 0, 'layouts': {}})
        value['requests'] += 1
        layout = meta.get('prompt_layout_version', 'legacy')
        value['layouts'][layout] = value['layouts'].get(layout, 0) + 1
        from .provider_identity import effective
        protocol=effective(row)['effective']['protocol']
        usage = adapter.normalize_usage(db.loads(row['usage'], {}), protocol or 'unknown')
        for key in ('input_tokens', 'output_tokens'):
            if type(usage[key]) is int:value[key] = (value[key] or 0) + usage[key]
        if usage['cache_hit_tokens'] is None or usage['cache_miss_tokens'] is None:
            value['cache_unknown_runs'] += 1
        else:
            value['cache_known_runs'] += 1
            for key in ('cache_hit_tokens', 'cache_miss_tokens'):
                value[key] = (value[key] or 0) + usage[key]
    for row in db.rows("SELECT detail FROM audit WHERE project_id=? AND action='writing_response_cache_reused'", (pid,)):
        stage = db.loads(row['detail'], {}).get('stage', 'other')
        if stage in groups:groups[stage]['local_response_reuses'] += 1
    for value in groups.values():
        denominator = (value['cache_hit_tokens'] or 0) + (value['cache_miss_tokens'] or 0)
        value['weighted_hit_rate'] = value['cache_hit_tokens'] / denominator if denominator else None
    return {'stages': [groups[name] for name in sorted(groups)],
            'scope': '累计已保存记录；未知用量不补零。本地响应复用避免HTTP，不计为服务商输入缓存命中。'}
