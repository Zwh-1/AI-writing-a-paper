"""Explicit user permission and peak-hour limits for the official DeepSeek API."""
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException

from . import db

try:
    from zoneinfo import ZoneInfo
    SHANGHAI = ZoneInfo('Asia/Shanghai')
except (ImportError, KeyError):
    # Windows portable distributions may omit the IANA database. Modern
    # Shanghai time is UTC+08:00; never inherit the host's local timezone.
    SHANGHAI = timezone(timedelta(hours=8), 'Asia/Shanghai')


SETTING = 'official_api_policy'
CODE = 'OFFICIAL_API_DISABLED'
PEAK_CODE = 'OFFICIAL_API_PEAK_HOURS_BLOCKED'
WARNING = '当前 DeepSeek 官方 API 花费高，缓存命中率非常低，开启后可能产生较高费用。'
WINDOWS = ((time(9), time(12)), (time(14), time(18)))


def current_time():
    return datetime.now(SHANGHAI)


def is_official(configuration):
    """Match the destination's DNS name, never an endpoint label or model ID."""
    value = configuration.get('base_url', '') if isinstance(configuration, dict) else configuration
    if not isinstance(value, str):
        return False
    try:
        host = (urlsplit(value).hostname or '').lower().rstrip('.')
    except ValueError:
        return False
    return host == 'deepseek.com' or host.endswith('.deepseek.com')


def status(moment=None):
    saved = db.setting(SETTING, {})
    saved = saved if isinstance(saved, dict) else {}
    # A historical peak-hour override never grants general official-API consent.
    allow_api = saved.get('allow_official_api') is True
    allow_peak = saved.get('allow_official_peak_hours') is True
    local = moment if moment is not None else current_time()
    local = local.replace(tzinfo=SHANGHAI) if local.tzinfo is None else local.astimezone(SHANGHAI)
    window = next(((start, end) for start, end in WINDOWS
                   if start <= local.time().replace(tzinfo=None) < end), None) if local.weekday() < 5 else None
    peak = window is not None
    blocked = not allow_api or (peak and not allow_peak)
    next_allowed = (datetime.combine(local.date(), window[1], SHANGHAI).isoformat()
                    if allow_api and peak and not allow_peak else None)
    return {'allow_official_api': allow_api, 'allow_official_peak_hours': allow_peak,
            'warning': WARNING, 'timezone': 'Asia/Shanghai',
            'is_peak_hours': peak, 'in_restricted_hours': peak, 'blocked': blocked,
            'next_allowed_at': next_allowed,
            'restricted_weekdays': [1, 2, 3, 4, 5],
            'restricted_windows': [{'start': '09:00', 'end': '12:00'},
                                   {'start': '14:00', 'end': '18:00'}]}


@db.atomic
def save(body):
    fields = ('allow_official_api', 'allow_official_peak_hours')
    if (not isinstance(body, dict) or not body or not set(body).issubset(fields)
            or any(type(value) is not bool for value in body.values())):
        raise HTTPException(422, '仅支持布尔值 allow_official_api、allow_official_peak_hours，至少提供一项')
    before = status()
    values = {field: body.get(field, before[field]) for field in fields}
    db.set_setting(SETTING, values)
    db.audit(None, 'official_api_policy_changed',
             {**{field + '_before': before[field] for field in fields}, **values,
              'timezone': 'Asia/Shanghai'})
    return status()


def assert_allowed(configuration, moment=None):
    """Reject before sending HTTP; the caller must not retry or fail over it."""
    if not is_official(configuration):
        return
    state = status(moment)
    if not state['blocked']:
        return
    from . import provider
    if not state['allow_official_api']:
        code = CODE
        message = ('DeepSeek 官方 API 默认禁用，本次未发送请求。'
                   '仅在“接口与设置”手动开启“允许使用 DeepSeek 官方 API”后才可调用；'
                   '开启后仍受工作日高峰时段限制。')
        transport_type = 'OfficialAPIPolicy'
    else:
        code = PEAK_CODE
        resume = state['next_allowed_at'][11:16]
        message = ('北京时间工作日09:00–12:00、14:00–18:00默认禁止使用官方API；'
                   f'本次未发送请求。可在{resume}后继续，或在“接口与设置”单独手动开启高峰时段使用。')
        transport_type = 'OfficialAPIPeakHoursPolicy'
    error = provider.ProviderError(
        f'{code}：{message}{WARNING}',
        unknown=False, retryable=False, fallback_allowed=False)
    error.code = code
    error.details = {'code': code, **state}
    error.blocked_until = state['next_allowed_at']
    error.failure_phase = 'policy_before_http'
    error.transport_error_type = transport_type
    raise error
