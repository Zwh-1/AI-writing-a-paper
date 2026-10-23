"""Balance contract and secret handling. Only fake credentials; no paid requests."""
import httpx
import pytest
from test_workbench import client
from app import db, provider, security

@pytest.mark.parametrize("base", ["https://relay.example", "https://relay.example/", "https://relay.example/v1", "https://relay.example/v1///"])
def test_usage_url_has_one_v1(base):
    assert provider.usage_endpoint(base) == "https://relay.example/v1/usage"

@pytest.mark.parametrize("payload,amount,active,unit", [
    ({"remaining":0,"balance":99,"is_active":False,"isValid":True,"unit":"CNY"}, "0",False,"CNY"),
    ({"remaining":None,"quota":{"remaining":"1.23000000","unit":"USD"},"isValid":True}, "1.23000000",True,"USD"),
    ({"balance":"10.15508055","is_active":"false"}, "10.15508055",False,"USD"),
    ({"data":{"remaining":"2.5","is_active":1}}, "2.5",True,"USD"),
    ({"remaining":None,"quota":{"remaining":None},"balance":3}, "3",True,"USD"),
])
def test_nullish_precedence(payload,amount,active,unit):
    result=provider.normalize_account_usage(payload)
    assert result["remaining_exact"]==amount
    assert result["is_valid"] is active
    assert result["unit"]==unit

def test_unknown_amount_and_default_validity_are_explicit():
    result=provider.normalize_account_usage({})
    assert result["remaining"] is None and result["remaining_exact"] is None
    assert result["balance_known"] is False
    assert result["validity_source"]=="default_assumed"
    assert result["unit_source"]=="default_USD"
    assert provider.normalize_account_usage({"is_active":"unknown"})["is_valid"] is None

@pytest.mark.parametrize("amount", [True, {}, [], "NaN", "Infinity", "oops", "1e9999"])
def test_invalid_amount_is_not_silently_zero(amount):
    with pytest.raises(provider.ProviderError):provider.normalize_account_usage({"remaining":amount})

def setup_fake(client,monkeypatch,handler):
    monkeypatch.delenv("PAPER_API_KEY",raising=False)
    assert client.put("/api/v1/settings",json={"api_key":"sk-fake-usage-1234","provider":{"base_url":"https://relay.example/v1/","protocol":"responses"}}).status_code==200
    monkeypatch.setattr(provider,"client",lambda cfg:httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=False))

def test_authenticated_bearer_request_and_normalized_history(client,monkeypatch):
    def handler(request):
        assert str(request.url)=="https://relay.example/v1/usage"
        assert request.method=="GET"
        assert request.headers["Authorization"]=="Bearer sk-fake-usage-1234"
        assert "x-goog-api-key" not in request.headers
        return httpx.Response(200,json={"remaining":0,"is_active":False,"unit":"USD","echo_secret":"sk-fake-usage-1234"})
    setup_fake(client,monkeypatch,handler)
    response=client.post("/api/v1/provider/account-usage")
    assert response.status_code==200,response.text
    assert response.json()["remaining"]==0 and response.json()["is_valid"] is False
    history=client.get("/api/v1/provider/account-usage/history")
    assert len(history.json())==1
    assert "sk-fake" not in response.text+history.text
    assert "echo_secret" not in db.dumps(db.setting("account_usage"))
    assert security.get_key()=="sk-fake-usage-1234"

@pytest.mark.parametrize("status", [401,403,404,429,500])
def test_http_failure_retains_key_and_last_receipt(client,monkeypatch,status):
    setup_fake(client,monkeypatch,lambda r:httpx.Response(status,json={"error":"sk-fake-usage-1234"}))
    old={"remaining_exact":"9","fetched_at":"old"};db.set_setting("account_usage",old)
    response=client.post("/api/v1/provider/account-usage")
    assert response.status_code>=400 and "sk-fake" not in response.text
    assert db.setting("account_usage")==old
    assert security.get_key()=="sk-fake-usage-1234"

def test_non_json_and_transport_errors_are_safe(client,monkeypatch):
    setup_fake(client,monkeypatch,lambda r:httpx.Response(200,text="sk-fake-usage-1234"))
    assert client.post("/api/v1/provider/account-usage").status_code>=400
    def fail(request):raise httpx.ReadTimeout("sk-fake-usage-1234",request=request)
    monkeypatch.setattr(provider,"client",lambda cfg:httpx.Client(transport=httpx.MockTransport(fail)))
    r=client.post("/api/v1/provider/account-usage")
    assert r.status_code>=400 and "sk-fake" not in r.text

def test_history_bounded_and_cache_invalidated_only_when_account_changes(client,monkeypatch):
    setup_fake(client,monkeypatch,lambda r:httpx.Response(200,json={"remaining":1}))
    for _ in range(32):assert client.post("/api/v1/provider/account-usage").status_code==200
    assert len(db.setting("account_usage_history"))==30
    client.put("/api/v1/settings",json={"provider":{"model":"another"}})
    assert client.get("/api/v1/settings").json()["account_usage"] is not None
    client.put("/api/v1/settings",json={"provider":{"base_url":"https://other.example"}})
    assert client.get("/api/v1/settings").json()["account_usage"] is None
    db.set_setting("account_usage",{"remaining":1})
    client.put("/api/v1/settings",json={"api_key":"sk-fake-usage-new-5678"})
    assert client.get("/api/v1/settings").json()["account_usage"] is None
