"""Dual provider routing with fake credentials and controlled HTTP responses."""
import json
import httpx
import pytest
from test_workbench import client,project,file
from app import db,provider,security,tasks,model_routing,material_summary


def configured(client):
    pid=project(client)
    assert client.put('/api/v1/settings',json={'api_key':'fake-ds-key','provider':{'base_url':'https://ds.example/v1','model':'deepseek-test','max_attempts':1,'json_mode':True}}).status_code==200
    response=client.put('/api/v1/gpt-fallback/settings',json={'api_key':'fake-gpt-key','provider':{'base_url':'https://www.ecoapi.cc/v1','model':'gpt-6.1-sol'}})
    assert response.status_code==200,response.text
    return pid


def mock(monkeypatch,callback):
    monkeypatch.setattr(provider,'client',lambda c:httpx.Client(transport=httpx.MockTransport(callback)))


def ok(text='{"ok":true}',**kwargs):
    return httpx.Response(200,json={'choices':[{'message':{'content':text},'finish_reason':'stop'}],'usage':{'prompt_tokens':7,'completion_tokens':5},**kwargs})


def call(pid,images=None,payload=None,manifest=None):
    task=tasks.enqueue(pid,'provider_probe',payload or {})
    return tasks.model_call(task,'Return JSON with ok true.','hello',manifest or {'file_ids':[]},images)


def test_primary_is_deepseek_and_keeps_gpt_credential_isolated(client,monkeypatch):
    pid=configured(client);requests=[]
    def handle(r):
        requests.append(r);assert r.url.host=='ds.example'
        assert r.headers['Authorization']=='Bearer fake-ds-key'
        assert json.loads(r.content)['model']=='deepseek-test'
        return ok()
    mock(monkeypatch,handle)
    result=call(pid)
    assert len(requests)==1 and result['provider_snapshot']['model']=='deepseek-test'
    run=db.one('SELECT * FROM generation_runs')
    assert db.loads(run['manifest'])['provider_route']=='deepseek'


@pytest.mark.parametrize('status,body',[(401,{'error':'bad key'}),(402,{'error':'quota'}),(403,{'error':'permission'}),(404,{'error':'model unavailable'}),(400,{'error':'image input unsupported'}),(429,{'error':'rate limit'})])
def test_explicit_rejection_switches_once_and_records_actual_gpt(client,monkeypatch,status,body):
    pid=configured(client);requests=[]
    def handle(r):
        requests.append(r)
        if r.url.host=='ds.example':return httpx.Response(status,json=body,headers={'retry-after':'0'})
        assert str(r.url)=='https://www.ecoapi.cc/v1/chat/completions'
        assert r.headers['Authorization']=='Bearer fake-gpt-key'
        data=json.loads(r.content)
        assert data['model']=='gpt-6.1-sol' and 'max_completion_tokens' in data and 'max_tokens' not in data
        assert 'thinking' not in data
        return ok()
    mock(monkeypatch,handle);result=call(pid)
    assert len(requests)==2 and result['provider_snapshot']['model']=='gpt-6.1-sol'
    runs=db.rows('SELECT * FROM generation_runs ORDER BY created_at')
    assert [r['status'] for r in runs]==['rejected','completed']
    assert db.loads(runs[1]['manifest'])['fallback_reason']
    assert db.one("SELECT SUM(budget_charge) AS n FROM generation_budget_usage WHERE status<>'rejected'")['n']==2


@pytest.mark.parametrize('failure',['timeout','503','invalid_json','truncated','bad_request'])
def test_uncertain_or_executed_requests_never_switch_provider(client,monkeypatch,failure):
    pid=configured(client);requests=[]
    def handle(r):
        requests.append(r)
        if failure=='timeout':raise httpx.ReadTimeout('fake-ds-key',request=r)
        if failure=='503':return httpx.Response(503,json={'error':'temporarily unavailable'})
        if failure=='bad_request':return httpx.Response(400,json={'error':'max tokens is invalid'})
        if failure=='invalid_json':return httpx.Response(200,text='not json')
        return httpx.Response(200,json={'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]})
    mock(monkeypatch,handle)
    with pytest.raises(provider.ProviderError):call(pid)
    assert len(requests)==1 and requests[0].url.host=='ds.example'
    assert len(db.rows('SELECT * FROM generation_runs'))==1


def test_images_use_gpt_when_primary_has_no_image_support(client,monkeypatch):
    pid=configured(client);cfg=provider.config();cfg['streaming']=True;db.set_setting('provider',cfg);requests=[]
    def handle(r):
        requests.append(r);assert r.url.host=='www.ecoapi.cc'
        data=json.loads(r.content)
        assert data['stream'] is False and 'response_format' not in data
        assert data['messages'][1]['content'][1]['type']=='image_url'
        return ok('image text')
    mock(monkeypatch,handle);result=call(pid,images=[{'mime':'image/png','data':'fake-image'}])
    assert len(requests)==1 and result['text']=='image text'
    assert db.one('SELECT model FROM generation_runs')['model']=='gpt-6.1-sol'


def test_missing_backup_does_not_send_image_to_text_model(client,monkeypatch):
    pid=configured(client);client.put('/api/v1/gpt-fallback/settings',json={'api_key':''})
    mock(monkeypatch,lambda r:pytest.fail('No image request may be sent'))
    with pytest.raises(provider.ProviderError,match='本地 OCR'):call(pid,images=[{'mime':'image/png','data':'fake'}])
    assert not db.rows('SELECT * FROM generation_runs')


def test_no_egress_and_budget_checks_apply_to_gpt_too(client,monkeypatch):
    pid=configured(client);f=file(client,pid);client.patch('/api/v1/files/'+f['id'],json={'egress':'local_only'})
    mock(monkeypatch,lambda r:pytest.fail('Forbidden source must not reach a provider'))
    with pytest.raises(ValueError,match='禁止外发'):call(pid,payload={'provider_profile':'gpt'},manifest={'file_ids':[f['id']]})
    client.patch('/api/v1/projects/'+pid,json={'budget':0})
    with pytest.raises(ValueError,match='项目预算不足'):call(pid,payload={'provider_profile':'gpt'})


def test_explicit_summary_scope_is_preserved_until_fallback(client,monkeypatch):
    pid=configured(client);requests=[]
    def handle(r):
        requests.append(r);return ok()
    mock(monkeypatch,handle)
    with provider.request_scope({**provider.DEFAULT,'base_url':'https://summary.example/v1','model':'deepseek-summary'},'fake-summary-key'):
        result=call(pid)
    assert requests[0].headers['Authorization']=='Bearer fake-summary-key'
    assert result['provider_snapshot']['model']=='deepseek-summary'
    assert provider.config()['model']=='deepseek-test'


def test_settings_and_model_list_do_not_leak_or_mix_credentials(client,monkeypatch):
    configured(client)
    encrypted=(security.DATA/'gpt-fallback.dpapi').read_bytes()
    assert b'fake-gpt-key' not in encrypted and security.get_key()=='fake-ds-key'
    r=client.get('/api/v1/gpt-fallback/settings');assert 'fake-gpt-key' not in r.text and r.json()['ready']
    mock(monkeypatch,lambda r:httpx.Response(200,json={'data':[{'id':'gemini-retired'},{'id':'deepseek-test'},{'id':'gpt-6.1-sol'}]}))
    assert client.post('/api/v1/gpt-fallback/models').json()['models']==[{'id':'gpt-6.1-sol','name':'gpt-6.1-sol'}]
    assert not db.setting('capabilities',{})
    assert security.redact('fake-ds-key fake-gpt-key')=='[已隐藏密钥] [已隐藏密钥]'
    assert client.put('/api/v1/gpt-fallback/settings',json={'provider':{'model':'gemini-retired'}}).status_code==422
    assert client.put('/api/v1/settings',json={'provider':{'protocol':'gemini'}}).status_code==422
    assert client.put('/api/v1/settings',json={'provider':{'model':'gemini-retired'}}).status_code==422
    assert (security.DATA/'gpt-fallback.dpapi').read_bytes()==encrypted


def test_migration_preserves_old_ecoapi_key_for_gpt_and_reuses_deepseek(client,monkeypatch):
    monkeypatch.delenv('PAPER_API_KEY',raising=False);monkeypatch.delenv('PAPER_SUMMARY_API_KEY',raising=False);monkeypatch.delenv('PAPER_GPT_API_KEY',raising=False)
    security.save_key('fake-original-eco-key');security.save_key('fake-summary-key','material-summary')
    db.set_setting('provider',{**provider.DEFAULT,'base_url':'https://www.ecoapi.cc','model':'gemini-retired'})
    db.set_setting('material_summary_provider',{**material_summary.DEFAULT,'model':'deepseek-test'})
    result=model_routing.migrate_from_summary('gpt-6.1-sol')
    assert result['gpt_ready'] and provider.config()['model']=='deepseek-test'
    assert security.get_key()=='fake-summary-key' and security.get_key('gpt-fallback')=='fake-original-eco-key'
    assert model_routing.config()['base_url']=='https://www.ecoapi.cc/v1'
    assert not db.setting('capabilities') and not db.setting('account_usage_history')
    assert (security.DATA/'backups'/result['database_backup']).exists()
    assert model_routing.migrate_from_summary()['already_migrated']


def test_gpt_probe_records_its_own_capability_without_overwriting_primary(client,monkeypatch):
    pid=configured(client);db.set_setting('capabilities',{'model':'deepseek-test','generation':'not_tested'})
    mock(monkeypatch,lambda r:ok())
    r=client.post('/api/v1/gpt-fallback/probe',json={'project_id':pid});assert r.status_code==200
    assert tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?',(r.json()['id'],))['status']=='succeeded'
    assert db.setting('gpt_fallback_capabilities')['generation']=='verified'
    assert db.setting('capabilities')['model']=='deepseek-test'


@pytest.mark.parametrize('profile',['primary','gpt'])
def test_image_probe_validates_actual_image_and_provider(client,monkeypatch,profile):
    pid=configured(client);requests=[]
    def handle(r):
        requests.append(r);assert json.loads(r.content)['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
        return ok('{"code":"A7X2"}')
    mock(monkeypatch,handle)
    response=client.post('/api/v1/provider/probe-image',json={'project_id':pid,'provider_profile':profile});assert response.status_code==200
    assert tasks.run_one()
    task=client.get('/api/v1/tasks/'+response.json()['id']).json()
    assert task['status']=='succeeded' and task['result']['image_input']=='verified_by_sample'
    assert len(requests)==1 and requests[0].url.host==('ds.example' if profile=='primary' else 'www.ecoapi.cc')


def test_primary_image_probe_never_silently_tests_backup(client,monkeypatch):
    pid=configured(client);requests=[]
    def handle(r):
        requests.append(r);return httpx.Response(400,json={'error':'image unsupported'})
    mock(monkeypatch,handle)
    response=client.post('/api/v1/provider/probe-image',json={'project_id':pid,'provider_profile':'primary'})
    assert tasks.run_one()
    assert client.get('/api/v1/tasks/'+response.json()['id']).json()['status']=='failed'
    assert len(requests)==1 and requests[0].url.host=='ds.example'


def test_unknown_outcome_requires_acknowledgement_before_gpt_retry(client):
    pid=configured(client);task=tasks.enqueue(pid,'rules_ai',{'file_ids':[]})
    db.execute("UPDATE tasks SET status='outcome_unknown' WHERE id=?",(task['id'],))
    route='/api/v1/tasks/'+task['id']+'/retry'
    assert client.post(route,json={'provider_profile':'gpt'}).status_code==422
    response=client.post(route,json={'provider_profile':'gpt','acknowledge_possible_duplicate_charge':True})
    assert response.status_code==200 and db.loads(response.json()['payload'])['provider_profile']=='gpt'
    assert db.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']=='outcome_unknown'
