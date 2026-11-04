"""Reversible cache controls; task layout is frozen at creation, never on resume."""
from fastapi import APIRouter, HTTPException
from . import db

router = APIRouter(prefix='/ai-cache-policy')
DEFAULTS = {'new_task_prompt_layout': 'cache_v2', 'stable_block_lru_enabled': True,
            'task_recovery_v2_enabled': True, 'writing_singleflight_enabled': True,
            'unit_matrix_cache_enabled': True, 'provider_cache_hints_enabled': False,
            'new_task_verification_scope': 'claim_window_v2',
            'new_task_revision_protocol': 'restricted_patch_v1'}
WRITING_KINDS = frozenset({'write', 'write_paper', 'agent_step'})


def settings():
    saved = db.setting('ai_cache_policy', {})
    return {**DEFAULTS, **({key: value for key, value in saved.items() if key in DEFAULTS}
                          if isinstance(saved, dict) else {})}


def freeze_payload(kind, payload):
    if kind not in WRITING_KINDS:
        return payload
    value = dict(payload)
    layout = value.get('prompt_layout_version', settings()['new_task_prompt_layout'])
    if layout not in ('legacy', 'cache_v2'):
        raise HTTPException(422, '未知的模型输入布局版本')
    value['prompt_layout_version'] = layout
    value.setdefault('model_view_version', 'writing-model-view.v2' if layout == 'cache_v2' else 'legacy')
    for field, default, supported in (
        ('verification_scope_version', 'new_task_verification_scope', ('legacy_full', 'claim_window_v2')),
        ('revision_protocol_version', 'new_task_revision_protocol', ('full_candidate', 'restricted_patch_v1'))):
        chosen = value.get(field, settings()[default])
        if chosen not in supported:raise HTTPException(422, '未知的写作质量协议版本：' + field)
        value[field] = chosen
    if value.get('evidence_range_schema','evidence.range.v1')!='evidence.range.v1':
        raise HTTPException(422,'未知的不可变阅读范围协议')
    if value.get('dependency_projection_mode','shadow')!='shadow':
        raise HTTPException(422,'依赖影响分析目前仅允许shadow观测模式')
    value.setdefault('evidence_range_schema', 'evidence.range.v1')
    value.setdefault('dependency_projection_mode', 'shadow')
    return value


@router.get('')
def get_policy():
    return settings()


@router.put('')
@db.atomic
def save_policy(body: dict):
    if not body or set(body) - set(DEFAULTS):
        raise HTTPException(422, '缓存设置必须包含至少一个已知字段')
    for key, value in body.items():
        if key == 'new_task_prompt_layout':
            if value not in ('legacy', 'cache_v2'):
                raise HTTPException(422, '新任务布局只能为legacy或cache_v2')
        elif key == 'new_task_verification_scope':
            if value not in ('legacy_full', 'claim_window_v2'):raise HTTPException(422, '未知的独立核验范围协议')
        elif key == 'new_task_revision_protocol':
            if value not in ('full_candidate', 'restricted_patch_v1'):raise HTTPException(422, '未知的局部修订协议')
        elif type(value) is not bool:
            raise HTTPException(422, '缓存开关必须为布尔值')
    if body.get('provider_cache_hints_enabled'):
        raise HTTPException(422, '尚无已验证的服务商缓存参数能力，不能启用通用缓存提示')
    before = settings()
    after = {**before, **body}
    if before['writing_singleflight_enabled'] and not after['writing_singleflight_enabled']:
        raise HTTPException(409, '单飞锁不能热关闭；须先停止接收任务、排空请求并恢复串行执行约束')
    db.set_setting('ai_cache_policy', after)
    db.audit(None, 'ai_cache_policy_changed', {'before': before, 'after': after})
    return after
