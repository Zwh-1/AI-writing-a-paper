"""Settings and key-persistence regressions. Every secret in this file is a fake test value."""
import httpx,pytest
from test_workbench import client
from app import db,security,provider

@pytest.fixture(autouse=True)
def no_environment_key(monkeypatch):monkeypatch.delenv('PAPER_API_KEY',raising=False)

def test_save_key_then_detect_models_keeps_saved_credential(client,monkeypatch):
 fake='sk-fake-settings-regression-1234';saved=client.put('/api/v1/settings',json={'api_key':fake,'provider':{'model':'fake-model','max_request_cost':2}});assert saved.status_code==200,saved.text;assert saved.json()['credential']['configured'];assert saved.json()['credential']['masked'].endswith('1234');assert fake not in saved.text
 def handle(request):
  assert request.headers['Authorization']=='Bearer '+fake
  return httpx.Response(200,json={'data':[{'id':'fake-model'}]})
 monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)));detected=client.post('/api/v1/provider/models');assert detected.status_code==200,detected.text;assert detected.json()['models'][0]['id']=='fake-model';state=client.get('/api/v1/settings').json();assert state['credential']['configured'];assert security.get_key()==fake

def test_saving_provider_without_new_key_preserves_ciphertext(client):
 fake='sk-fake-existing-key-5678';client.put('/api/v1/settings',json={'api_key':fake});encrypted=(security.DATA/'credential.dpapi').read_bytes();r=client.put('/api/v1/settings',json={'provider':{'model':'new-model'}});assert r.status_code==200,r.text;assert (security.DATA/'credential.dpapi').read_bytes()==encrypted;assert security.get_key()==fake

def test_invalid_new_key_does_not_commit_provider_settings_or_erase_old_key(client):
 client.put('/api/v1/settings',json={'api_key':'sk-fake-original-1111','provider':{'model':'original-model'}});encrypted=(security.DATA/'credential.dpapi').read_bytes();r=client.put('/api/v1/settings',json={'api_key':'invalid\nkey','provider':{'model':'must-not-save'}});assert r.status_code==422;assert provider.config()['model']=='original-model';assert (security.DATA/'credential.dpapi').read_bytes()==encrypted

def test_dpapi_verification_failure_keeps_existing_key_and_configuration(client,monkeypatch):
 client.put('/api/v1/settings',json={'api_key':'sk-fake-original-2222','provider':{'model':'original-model'}});encrypted=(security.DATA/'credential.dpapi').read_bytes()
 monkeypatch.setattr(security,'protect',lambda data,decrypt=False:b'not-the-original-value')
 r=client.put('/api/v1/settings',json={'api_key':'sk-fake-replacement-3333','provider':{'model':'must-not-save'}});assert r.status_code==503;assert '校验失败' in r.text;assert (security.DATA/'credential.dpapi').read_bytes()==encrypted;assert provider.config()['model']=='original-model'

def test_atomic_write_failure_keeps_original_and_cleans_temporary_file(client,monkeypatch):
 client.put('/api/v1/settings',json={'api_key':'sk-fake-original-4444'});encrypted=(security.DATA/'credential.dpapi').read_bytes()
 def fail(*args):raise PermissionError('simulated directory permissions')
 monkeypatch.setattr(security.os,'replace',fail);r=client.put('/api/v1/settings',json={'api_key':'sk-fake-replacement-5555'});assert r.status_code==503;assert (security.DATA/'credential.dpapi').read_bytes()==encrypted;assert not list(security.DATA.glob('.credential-*.tmp'))

def test_small_valid_decimal_prices_and_reservations_can_be_saved(client):
 r=client.put('/api/v1/settings',json={'provider':{'input_price':0.0003,'output_price':0.0015,'max_request_cost':0.125}});assert r.status_code==200,r.text;assert r.json()['provider']['max_request_cost']==0.125

def test_non_string_key_returns_visible_validation_error(client):
 r=client.put('/api/v1/settings',json={'api_key':{'wrong':'type'}});assert r.status_code==422;assert '单行文字' in r.text;assert not (security.DATA/'credential.dpapi').exists()
