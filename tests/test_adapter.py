"""Cross-API adaptive adapter tests.

Covers API family detection, balance/pricing/usage normalization with different
vendor shapes, and the fail-safe defaults. All tests are offline and use no paid
requests.
"""
import pytest
from app import adapter, provider
from app.adapter import (
    FAMILY_DEEPSEEK_OFFICIAL,
    FAMILY_NEW_API,
    FAMILY_OPENAI,
    FAMILY_X5M5X,
    FAMILY_UNKNOWN,
    detect_api_family,
    normalize_balance,
    normalize_pricing,
    normalize_usage,
    default_pricing,
    is_same_family,
)


# ---- Family detection -------------------------------------------------------

@pytest.mark.parametrize("base,expected", [
    ("https://api.deepseek.com", FAMILY_DEEPSEEK_OFFICIAL),
    ("https://api.deepseek.com/v1", FAMILY_DEEPSEEK_OFFICIAL),
    ("https://new.x5m5x.com", FAMILY_NEW_API),
    ("https://api.x5m5x.com", FAMILY_X5M5X),
    ("https://api.x5m5x.com/v1", FAMILY_X5M5X),
    ("https://api.openai.com", FAMILY_OPENAI),
    ("https://unknown.example.com", FAMILY_UNKNOWN),
])
def test_detect_api_family_by_host(base, expected):
    assert detect_api_family(base) == expected


def test_detect_family_falls_back_to_model():
    # Unknown host but DeepSeek model → treat as new-api style relay.
    assert detect_api_family("https://relay.example.com", "deepseek-chat") == FAMILY_NEW_API
    assert detect_api_family("https://relay.example.com", "gpt-5") == FAMILY_OPENAI


def test_same_family():
    assert is_same_family("https://api.x5m5x.com", "https://api.x5m5x.com/v1")
    assert not is_same_family("https://api.x5m5x.com", "https://new.x5m5x.com")
    assert is_same_family("https://new.x5m5x.com", "https://new.x5m5x.com/v1")


# ---- Balance normalization --------------------------------------------------

@pytest.mark.parametrize("payload,amount,active,unit", [
    # OpenAI style
    ({"remaining": 0, "is_active": False, "unit": "CNY"}, "0", False, "CNY"),
    # Quota nested
    ({"quota": {"remaining": "1.23000000", "unit": "USD"}, "isValid": True}, "1.23000000", True, "USD"),
    # String balance with is_active as string
    ({"balance": "10.15508055", "is_active": "false"}, "10.15508055", False, "USD"),
    # data wrapper
    ({"data": {"remaining": "2.5", "is_active": 1}}, "2.5", True, "USD"),
    # quota remaining None, balance fallback
    ({"quota": {"remaining": None}, "balance": 3}, "3", True, "USD"),
    # DeepSeek official balance_infos
    ({"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "9.60"}]}, "9.60", True, "CNY"),
])
def test_normalize_balance_variants(payload, amount, active, unit):
    result = normalize_balance(payload)
    assert result["remaining_exact"] == amount
    assert result["is_valid"] is active
    assert result["unit"] == unit


def test_normalize_balance_empty_is_explicit():
    result = normalize_balance({})
    assert result["remaining"] is None
    assert result["remaining_exact"] is None
    assert result["balance_known"] is False
    assert result["validity_source"] == "default_assumed"
    assert result["unit_source"] == "default_USD"


def test_normalize_balance_unrecognized_validity():
    result = normalize_balance({"is_active": "unknown"})
    assert result["is_valid"] is None
    assert result["validity_source"] == "default_assumed"


@pytest.mark.parametrize("amount", [True, {}, [], "NaN", "Infinity", "oops", "1e9999"])
def test_normalize_balance_invalid_amount_not_zero(amount):
    # A present-but-unparseable amount must raise (never silently zero).
    with pytest.raises(ValueError):
        normalize_balance({"remaining": amount})


# ---- Pricing normalization --------------------------------------------------

def test_normalize_pricing_standard():
    result = normalize_pricing({"input_price": 1.0, "output_price": 4.0, "currency": "USD"})
    assert result == {"input_price": 1.0, "output_price": 4.0, "currency": "USD", "unit": "per_million_tokens"}


def test_normalize_pricing_shorthand():
    result = normalize_pricing({"input": "1.0", "output": "4.0", "unit": "USD"})
    assert result["input_price"] == 1.0
    assert result["output_price"] == 4.0
    assert result["currency"] == "USD"


def test_normalize_pricing_nested_wrapper():
    result = normalize_pricing({"pricing": {"input": 0.5, "output": 1.5}, "currency": "CNY"})
    assert result["input_price"] == 0.5
    assert result["output_price"] == 1.5
    assert result["currency"] == "CNY"


def test_normalize_pricing_none_when_missing():
    assert normalize_pricing({}) is None
    assert normalize_pricing({"note": "no prices"}) is None


# ---- Usage normalization ----------------------------------------------------

def test_normalize_usage_openai():
    result = normalize_usage({"prompt_tokens": 10, "completion_tokens": 5})
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 5


def test_normalize_usage_responses():
    result = normalize_usage({"input_tokens": 20, "output_tokens": 8})
    assert result["input_tokens"] == 20
    assert result["output_tokens"] == 8


def test_normalize_usage_missing_returns_none_not_zero():
    result = normalize_usage({})
    assert result["input_tokens"] is None
    assert result["output_tokens"] is None


def test_normalize_usage_string_tokens_ignored():
    # Strings are not accepted as token counts (must be int).
    result = normalize_usage({"prompt_tokens": "10", "completion_tokens": "5"})
    assert result["input_tokens"] is None
    assert result["output_tokens"] is None


# ---- Default pricing --------------------------------------------------------

def test_default_pricing_known_families():
    assert default_pricing(FAMILY_NEW_API)["input_price"] == 1.0
    assert default_pricing(FAMILY_X5M5X)["output_price"] == 4.0
    assert default_pricing(FAMILY_DEEPSEEK_OFFICIAL)["currency"] == "USD"


def test_default_pricing_unknown_returns_safe_default():
    result = default_pricing(FAMILY_UNKNOWN)
    assert result["currency"] == "USD"


# ---- Provider integration (usage_cost / auto_bind) --------------------------

def test_usage_cost_uses_family_default_when_price_missing():
    # 无任何计价依据时，cost 未知（None），预算按预留上界处理（原语义）。
    cfg = {"base_url": "https://new.x5m5x.com", "model": "deepseek-v4.1-flash-1017"}
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert provider.usage_cost(cfg, usage) is None


def test_usage_cost_applies_basis_when_adaptive_bound():
    # 端点已绑定自适应计价依据（endpoint_probe）时，family default 生效。
    cfg = {"base_url": "https://new.x5m5x.com", "model": "deepseek-v4.1-flash-1017",
           "expense_pricing_basis": {"kind": "endpoint_probe", "base_url": "https://new.x5m5x.com",
                                     "model": "deepseek-v4.1-flash-1017",
                                     "credential_hash": "abc", "currency": "USD",
                                     "input_price": 1.0, "output_price": 4.0}}
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    cost = provider.usage_cost(cfg, usage)
    assert cost == pytest.approx(5.0)


def test_usage_cost_explicit_price_wins():
    cfg = {"base_url": "https://new.x5m5x.com", "model": "deepseek-v4.1-flash-1017",
           "input_price": 0.5, "output_price": 1.5}
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    cost = provider.usage_cost(cfg, usage)
    assert cost == pytest.approx(2.0)


def test_usage_cost_malformed_usage_never_zero():
    assert provider.usage_cost({"base_url": "https://new.x5m5x.com"}, {"input_tokens": "x", "output_tokens": 5}) is None
    assert provider.usage_cost({"base_url": "https://new.x5m5x.com"}, {}) is None


def test_auto_bind_pricing_configured_first():
    cfg = {"base_url": "https://new.x5m5x.com", "input_price": 1.0, "output_price": 4.0}
    resolved, source = provider.auto_bind_pricing(cfg)
    assert source == "configured"
    assert resolved["input_price"] == 1.0


def test_auto_bind_pricing_family_default():
    cfg = {"base_url": "https://new.x5m5x.com", "model": "deepseek-v4.1-flash-1017"}
    resolved, source = provider.auto_bind_pricing(cfg)
    assert source == "family_default"
    assert resolved["input_price"] == 1.0
    assert resolved["output_price"] == 4.0


def test_auto_bind_pricing_response_extracted():
    cfg = {"base_url": "https://new.x5m5x.com"}
    response = {"input_price": 0.8, "output_price": 2.4, "currency": "USD"}
    resolved, source = provider.auto_bind_pricing(cfg, response=response)
    assert source == "response_extracted"
    assert resolved["input_price"] == 0.8
    assert resolved["output_price"] == 2.4
