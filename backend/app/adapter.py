"""Cross-API adaptive adapter.

Different relays and API providers expose different JSON shapes for the same
logical fields (pricing, balance, usage). This module classifies an endpoint by
its base_url and model, then normalizes each response into a stable internal
contract so the rest of the application never depends on a specific vendor's
field names.

The adapter is deliberately non-mutating: it returns normalized copies and
never writes to the database. Callers (provider, deepseek_pool, expense_evidence)
decide when to persist an observation or a pricing basis.

Public surface:
  detect_api_family(base_url, model=None) -> str
  classify_by_host(base_url) -> str
  normalize_balance(payload) -> dict
  normalize_pricing(payload) -> dict | None
  default_pricing(family, model=None) -> dict | None
  is_same_family(base_a, base_b) -> bool
  normalize_base_url(base) -> str
"""
import hashlib
import math
import re
from decimal import Decimal, InvalidOperation

# ---- API families ----------------------------------------------------------
FAMILY_DEEPSEEK_OFFICIAL = 'deepseek_official'
FAMILY_NEW_API = 'new_api'          # new-api / one-api style relays
FAMILY_X5M5X = 'x5m5x'              # project's primary relay
FAMILY_OPENAI = 'openai'            # generic OpenAI-compatible gateway
FAMILY_UNKNOWN = 'unknown'

# Host-level hints. These are intentionally loose so a relay behind a custom
# domain still resolves through the path/response rather than only the host.
_HOST_HINTS = {
    'deepseek.com': FAMILY_DEEPSEEK_OFFICIAL,
    'new.x5m5x.com': FAMILY_NEW_API,
    'api.x5m5x.com': FAMILY_X5M5X,
    'x5m5x.com': FAMILY_X5M5X,
    'openai.com': FAMILY_OPENAI,
    'api.openai.com': FAMILY_OPENAI,
}

# Pricing defaults per family (USD per million tokens unless noted).
# These are NOT authoritative quotes: they are conservative safety defaults so
# a switching endpoint never hard-crashes on missing pricing fields. They are
# overridden by any imported tariff or configured explicit price.
_DEFAULT_PRICING = {
    FAMILY_DEEPSEEK_OFFICIAL: {'input_price': 0.28, 'output_price': 1.10, 'currency': 'USD'},
    FAMILY_NEW_API: {'input_price': 1.0, 'output_price': 4.0, 'currency': 'USD'},
    FAMILY_X5M5X: {'input_price': 1.0, 'output_price': 4.0, 'currency': 'USD'},
    FAMILY_OPENAI: {'input_price': 1.0, 'output_price': 4.0, 'currency': 'USD'},
}

# Known model families that historically use different price scales.
_MODEL_RE = re.compile(r'(deepseek|gpt|claude|glm|kimi|qwen|ernie|doubao|hunyuan|moonshot)', re.I)


def normalize_base_url(base):
    """Normalize a base URL for family comparison (strip trailing / and /v1)."""
    return str(base or '').rstrip('/').removesuffix('/v1')


def detect_api_family(base_url, model=None):
    """Classify an endpoint into an API family by host, then by model."""
    base = normalize_base_url(base_url)
    host = re.sub(r'^https?://', '', base).split('/')[0].lower()
    # Exact host hints first.
    for key, family in _HOST_HINTS.items():
        if host == key or host.endswith('.' + key):
            return family
    # Model-family fallback.
    if model:
        m = _MODEL_RE.search(str(model))
        if m:
            token = m.group(1).lower()
            if 'deepseek' in token:
                return FAMILY_DEEPSEEK_OFFICIAL if 'deepseek.com' in host else FAMILY_NEW_API
            if 'gpt' in token or 'openai' in token:
                return FAMILY_OPENAI
    return FAMILY_UNKNOWN


def classify_by_host(base_url):
    """Alias used by callers that only have a base_url."""
    return detect_api_family(base_url)


def is_same_family(base_a, base_b):
    """Whether two base URLs belong to the same API family."""
    return detect_api_family(base_a) == detect_api_family(base_b)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _first(*values):
    return next((v for v in values if v is not None), None)


def _to_number(value):
    """Best-effort numeric conversion; returns None for anything unparseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in ('nan', 'infinity', '-infinity', 'inf', '-inf'):
            return None
        try:
            dec = Decimal(text)
            if not dec.is_finite():
                return None
            num = float(dec)
            return num if math.isfinite(num) and num >= 0 else None
        except (InvalidOperation, ValueError, OverflowError):
            return None
    return None


# ---- Balance / usage normalization -----------------------------------------

# Field aliases by family. Each family maps logical field -> candidate keys.
_BALANCE_FIELDS = {
    'remaining': ('remaining', 'balance', 'quota_remaining', 'quota', 'total_balance', 'topped_up_balance', 'granted_balance', 'available'),
    'currency': ('currency', 'unit', 'currency_unit', 'currency_code'),
    'is_valid': ('is_active', 'isValid', 'valid', 'status'),
}

_USAGE_FIELDS = {
    'input_tokens': ('input_tokens', 'prompt_tokens', 'inputTokenCount', 'prompt_tokens_details'),
    'output_tokens': ('output_tokens', 'completion_tokens', 'outputTokenCount', 'completion_tokens_details'),
    'total_tokens': ('total_tokens', 'totalTokenCount'),
}


def normalize_balance(payload):
    """Normalize any balance/usage response into a stable dict.

    Handles:
      - OpenAI style: {"remaining": "1.23", "is_active": true, "unit": "USD"}
      - DeepSeek official: {"is_available": true, "balance_infos": [{"currency": "CNY", "total_balance": "9.60"}]}
      - new-api style: {"data": {"remaining": "1.23", "unit": "USD"}}
      - quota nested: {"quota": {"remaining": "1.23", "unit": "USD"}}
      - unknown/empty: returns explicit None markers, never a fabricated zero.
    """
    if not isinstance(payload, dict):
        raise ValueError('余额接口未返回JSON对象')

    root = payload
    # Unwrap common data wrapper.
    if not any(k in payload for k in ('remaining', 'quota', 'balance', 'is_active', 'isValid',
                                       'balance_infos', 'total_balance')) and isinstance(payload.get('data'), dict):
        root = payload['data']

    quota = root.get('quota') if isinstance(root.get('quota'), dict) else {}
    raw_amount = None
    currency = None
    is_valid = None
    validity_source = 'default_assumed'
    unit_source = 'default_USD'

    # DeepSeek official balance_infos array.
    infos = root.get('balance_infos')
    if isinstance(infos, list) and infos:
        info = infos[0] if isinstance(infos[0], dict) else {}
        raw_amount = _first(info.get('total_balance'), info.get('topped_up_balance'), info.get('granted_balance'))
        currency = _first(info.get('currency'), info.get('unit'))
        is_valid = root.get('is_available') if isinstance(root.get('is_available'), bool) else None
        if is_valid is not None:
            validity_source = 'response'

    if raw_amount is None:
        for field in _BALANCE_FIELDS['remaining']:
            candidate = _first(root.get(field), quota.get(field))
            if candidate is not None:
                raw_amount = candidate
                break

    if currency is None:
        for field in _BALANCE_FIELDS['currency']:
            candidate = _first(root.get(field), quota.get(field))
            if isinstance(candidate, (str, int, float)) and candidate:
                currency = str(candidate).upper()
                unit_source = 'response'
                break

    if is_valid is None:
        found_validity_field = False
        for field in _BALANCE_FIELDS['is_valid']:
            candidate = _first(root.get(field), quota.get(field))
            if candidate is None:
                continue
            found_validity_field = True
            if isinstance(candidate, bool):
                is_valid = candidate
                validity_source = 'response'
                break
            if candidate in (0, 1):
                is_valid = bool(candidate)
                validity_source = 'response'
                break
            if isinstance(candidate, str):
                lowered = candidate.strip().lower()
                if lowered in ('true', '1'):
                    is_valid = True
                    validity_source = 'response'
                    break
                if lowered in ('false', '0'):
                    is_valid = False
                    validity_source = 'response'
                    break
        # 字段存在但值无法识别 → 明确未知（与原行为一致）。
        # 字段完全缺失 → 保守地视为有效，避免误判账号无效。
        if not found_validity_field:
            is_valid = True
            validity_source = 'default_assumed'

    # Convert to numeric (validates the value), but keep the exact original
    # string for remaining_exact so "0" stays "0" instead of becoming "0.0".
    # A present-but-unparseable amount is a contract violation and must not be
    # silently treated as zero (matches the legacy strict behavior).
    if raw_amount is not None:
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, (str, int, float)):
            raise ValueError('余额字段不是有效数值，未按零余额处理')
        numeric = _to_number(raw_amount)
        if numeric is None:
            raise ValueError('无法解析余额字段，未按零余额处理')
    else:
        numeric = None
    exact = None
    if numeric is not None:
        try:
            # Prefer the raw string form for exactness (matches legacy behavior).
            if isinstance(raw_amount, str):
                exact = format(Decimal(raw_amount.strip()), 'f')
            else:
                exact = format(Decimal(str(raw_amount)), 'f')
        except (InvalidOperation, ValueError):
            exact = None

    return {
        'is_valid': is_valid,
        'validity_source': validity_source,
        'remaining': numeric,
        'remaining_exact': exact,
        'unit': currency or 'USD',
        'unit_source': unit_source,
        'balance_known': numeric is not None,
        'response_scope': 'data' if root is not payload else 'root',
    }


def normalize_pricing(payload):
    """Normalize any pricing payload into {'input_price', 'output_price', 'currency', 'unit'}.

    Accepts:
      - {"input_price": 1.0, "output_price": 4.0, "currency": "USD"}
      - {"input": "1.0", "output": "4.0"} (new-api style shorthand)
      - {"pricing": {"input": 1.0, "output": 4.0}, "currency": "CNY"}
      - DeepSeek official style nested under "pricing" or "price"
    Returns None if neither price can be resolved.
    """
    if not isinstance(payload, dict):
        return None
    root = payload
    outer = payload
    for wrapper in ('pricing', 'price', 'data', 'model_pricing', 'rates'):
        if isinstance(root.get(wrapper), dict):
            candidate = root[wrapper]
            # Prefer the deepest wrapper with usable fields.
            if any(k in candidate for k in ('input_price', 'output_price', 'input', 'output', 'prompt', 'completion')):
                root = candidate
                break

    def _price(keys):
        for key in keys:
            v = _to_number(root.get(key))
            if v is not None:
                return v
        return None

    input_price = _price(('input_price', 'input', 'prompt', 'prompt_price', 'inputPrice'))
    output_price = _price(('output_price', 'output', 'completion', 'completion_price', 'outputPrice'))
    if input_price is None and output_price is None:
        return None
    currency = None
    # Currency may live in the inner wrapper or the outer payload.
    for source in (root, outer):
        for key in ('currency', 'unit', 'currency_unit'):
            v = source.get(key)
            if isinstance(v, (str, int, float)) and v:
                currency = str(v).upper()
                break
        if currency:
            break
    return {
        'input_price': input_price,
        'output_price': output_price,
        'currency': currency or 'USD',
        'unit': 'per_million_tokens',
    }


def normalize_usage(usage):
    """Normalize any vendor usage object to {'input_tokens', 'output_tokens'}.

    Handles OpenAI chat completions (prompt_tokens/completion_tokens), Responses
    API (input_tokens/output_tokens), and nested details objects. Returns a dict
    with integer token counts (or None when missing); it never fabricates zeros.
    """
    if not isinstance(usage, dict):
        return {'input_tokens': None, 'output_tokens': None, 'raw': usage}
    input_tokens = None
    output_tokens = None
    for field in _USAGE_FIELDS['input_tokens']:
        v = usage.get(field)
        if isinstance(v, int) and v >= 0:
            input_tokens = v
            break
        if isinstance(v, dict):
            # DeepSeek official details: {"input_tokens_details": {"cached_tokens": n}}
            for inner in v.values():
                if isinstance(inner, int) and inner >= 0:
                    input_tokens = inner
                    break
            if input_tokens is not None:
                break
    for field in _USAGE_FIELDS['output_tokens']:
        v = usage.get(field)
        if isinstance(v, int) and v >= 0:
            output_tokens = v
            break
        if isinstance(v, dict):
            for inner in v.values():
                if isinstance(inner, int) and inner >= 0:
                    output_tokens = inner
                    break
            if output_tokens is not None:
                break
    return {'input_tokens': input_tokens, 'output_tokens': output_tokens, 'raw': usage}


def default_pricing(family, model=None):
    """Conservative default prices for a family.

    This is a SAFETY FALLBACK, not an authoritative quote. It prevents a hard
    crash on missing pricing fields. Any imported tariff or explicitly
    configured price takes precedence.
    """
    return dict(_DEFAULT_PRICING.get(family, _DEFAULT_PRICING.get(FAMILY_UNKNOWN, {
        'input_price': None, 'output_price': None, 'currency': 'USD'})))


def credential_hash(key):
    return hashlib.sha256(str(key or '').encode()).hexdigest()
