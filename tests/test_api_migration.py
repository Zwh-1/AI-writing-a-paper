"""Relay migration: fake credentials and controlled HTTP responses only."""
import httpx
from test_workbench import client
from app import db,provider,security

def test_deepseek_default_and_gpt_usage_path(client):
 assert provider.DEFAULT['base_url']=='https://api.x5m5x.com/v1'
 assert provider.usage_endpoint('https://www.ecoapi.cc/v1/')=='https://www.ecoapi.cc/v1/usage'

def test_changing_gateway_keeps_key_and_clears_previous_balance_and_capabilities(client,monkeypatch):
 monkeypatch.delenv('PAPER_API_KEY',raising=False)
 client.put('/api/v1/settings',json={'api_key':'sk-fake-relay-migration-1234','provider':{'base_url':'https://old.example','model':'keep-model'}})
 encrypted=(security.DATA/'credential.dpapi').read_bytes();db.set_setting('account_usage',{'remaining':99,'base_url':'https://old.example'});db.set_setting('capabilities',{'base_url':'https://old.example','protocol':'openai','models':[{'id':'old-model'}],'generation':'verified'})
 r=client.put('/api/v1/settings',json={'provider':{'base_url':'https://www.ecoapi.cc'}})
 assert r.status_code==200,r.text
 assert r.json()['account_usage'] is None and r.json()['capabilities']=={}
 assert r.json()['provider']['model']=='keep-model'
 assert (security.DATA/'credential.dpapi').read_bytes()==encrypted
 assert security.get_key()=='sk-fake-relay-migration-1234'

def test_protocol_change_drops_model_capabilities_but_keeps_account_balance(client):
 db.set_setting('capabilities',{'models':[{'id':'other-protocol'}]});db.set_setting('account_usage',{'remaining':1})
 r=client.put('/api/v1/settings',json={'provider':{'protocol':'responses'}})
 assert r.status_code==200 and r.json()['capabilities']=={} and r.json()['account_usage']['remaining']==1

def test_changing_model_resets_generation_status_without_dropping_scoped_list(client):
 cfg=provider.config();db.set_setting('capabilities',{'base_url':cfg['base_url'],'protocol':cfg['protocol'],'models':[{'id':'candidate'}],'generation':'verified'})
 r=client.put('/api/v1/settings',json={'provider':{'model':'new-model'}})
 assert r.json()['capabilities']['generation']=='not_tested'
 assert r.json()['capabilities']['models'][0]['id']=='candidate'

def test_legacy_capabilities_with_unknown_gateway_are_not_displayed(client):
 db.set_setting('capabilities',{'models':[{'id':'old-provider'}],'generation':'verified'})
 assert client.get('/api/v1/settings').json()['capabilities']=={}

def test_cloudflare_html_is_not_reported_as_bad_key_or_zero_balance(client,monkeypatch):
 monkeypatch.delenv('PAPER_API_KEY',raising=False);client.put('/api/v1/settings',json={'api_key':'sk-fake-cloudflare-5678'})
 def handler(request):
  assert request.headers['Authorization']=='Bearer sk-fake-cloudflare-5678'
  return httpx.Response(403,headers={'content-type':'text/html','server':'cloudflare'},text='<title>Attention Required! | Cloudflare</title>sk-fake-cloudflare-5678')
 monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handler)))
 for route in ('/api/v1/provider/account-usage','/api/v1/provider/models'):
  r=client.post(route);assert r.status_code==502,r.text
  assert 'Cloudflare' in r.text and '尚不能判断密钥是否有效' in r.text
  assert 'sk-fake' not in r.text
 assert security.get_key()=='sk-fake-cloudflare-5678'
 assert db.setting('account_usage') is None
