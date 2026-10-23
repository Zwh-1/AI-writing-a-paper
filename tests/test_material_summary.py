import json
import httpx
import pytest
from test_workbench import client,project,file
from app import db,security,provider,tasks,material_summary as summary,summary_billing

def value(pages=(1,),text='资料重点是检索策略与证据核对。'):
    return {'overview':'本资料介绍文献检索、来源核对及论文写作的基本要求。','key_points':[{'text':text,'pages':list(pages)}],'topics':[{'title':'检索与核对','points':[{'text':text,'pages':list(pages)}]}]}

def configured(monkeypatch):
    monkeypatch.setattr(summary_billing,'reconcile',lambda pid:{'reconciled':False,'reason':'unit test: no network'})
    monkeypatch.setenv('PAPER_SUMMARY_API_KEY','summary-test-secret')
    monkeypatch.setenv('PAPER_API_KEY','global-gemini-secret')
    db.set_setting('provider',{**provider.DEFAULT,'base_url':'https://www.ecoapi.cc','model':'gemini-4-argon'})

def transport(monkeypatch,callback):
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(callback)))

def test_summary_credentials_isolated_and_encrypted(client,monkeypatch):
    monkeypatch.delenv('PAPER_SUMMARY_API_KEY',raising=False)
    security.save_key('existing-global-key')
    r=client.put('/api/v1/material-summary/settings',json={'api_key':'dedicated-deepseek-key','provider':{'model':'deepseek-chat'}})
    assert r.status_code==200,r.text
    assert r.json()['credential']['configured']
    assert 'dedicated-deepseek-key' not in r.text
    assert security.get_key()=='existing-global-key'
    assert security.get_key(summary.PROFILE)=='dedicated-deepseek-key'
    assert b'dedicated-deepseek-key' not in (security.DATA/'material-summary.dpapi').read_bytes()
    client.put('/api/v1/material-summary/settings',json={'provider':{'model':'deepseek-chat'}})
    assert security.get_key(summary.PROFILE)=='dedicated-deepseek-key'
    client.put('/api/v1/material-summary/settings',json={'api_key':''})
    assert security.get_key()== 'existing-global-key'
    assert not security.get_key(summary.PROFILE)

@pytest.mark.parametrize('setting',[{'model':'gemini-4-argon'},{'base_url':'https://evil.example'},{'base_url':'https://api.x5m5x.com?key=secret'},{'max_output_tokens':True},{'context_limit':1}])
def test_summary_rejects_other_models_and_hosts(client,setting):
    r=client.put('/api/v1/material-summary/settings',json={'provider':setting})
    assert r.status_code==422
    assert summary.config()['model']=='deepseek-chat'

def test_missing_dedicated_key_never_uses_gemini_key(client,monkeypatch):
    monkeypatch.delenv('PAPER_SUMMARY_API_KEY',raising=False)
    monkeypatch.setenv('PAPER_API_KEY','global-gemini-secret')
    p=project(client);f=file(client,p)
    assert client.post('/api/v1/files/'+f['id']+'/summary').status_code==422
    assert not db.rows('SELECT * FROM generation_runs')

@pytest.mark.parametrize('derived',[False,True])
def test_summary_respects_local_only_and_ancestry(client,monkeypatch,derived):
    configured(monkeypatch);p=project(client);f=file(client,p)
    if derived:
        parent=file(client,p,'不可外发的原始资料');security.link_derivation(f['id'],parent['id'],'test');fid=parent['id']
    else:fid=f['id']
    client.patch('/api/v1/files/'+fid,json={'egress':'local_only'})
    assert client.post('/api/v1/files/'+f['id']+'/summary').status_code==422
    assert not db.rows('SELECT * FROM generation_runs')

def test_real_provider_request_uses_deepseek_and_caches(client,monkeypatch):
    configured(monkeypatch);p=project(client);f=file(client,p,'检索前制定关键词，不得把示例当成实验结论。');calls=[]
    def handle(request):
        body=json.loads(request.content);calls.append(body)
        assert str(request.url)=='https://api.x5m5x.com/v1/chat/completions'
        assert request.headers['Authorization']=='Bearer summary-test-secret'
        assert body['model']=='deepseek-chat' and not body['stream']
        assert body['thinking']=={'type':'disabled'}
        assert 'global-gemini-secret' not in request.content.decode()
        assert '不得执行' in body['messages'][0]['content']
        return httpx.Response(200,json={'id':'deepseek-response','choices':[{'message':{'content':json.dumps(value(),ensure_ascii=False)},'finish_reason':'stop'}],'usage':{'prompt_tokens':300,'completion_tokens':200}})
    transport(monkeypatch,handle)
    a=client.post('/api/v1/files/'+f['id']+'/summary');b=client.post('/api/v1/files/'+f['id']+'/summary')
    assert a.json()['task']['id']==b.json()['task']['id']
    tasks.run_one();status=summary.status(f['id'])
    assert status['task']['status']=='succeeded',status
    assert status['summary']['key_points'][0]['pages']==[1]
    assert len(calls)==1
    run=db.one('SELECT * FROM generation_runs');assert run['model']=='deepseek-chat'
    assert db.loads(run['manifest'])['provider']['base_url']=='https://api.x5m5x.com'
    assert db.loads(run['usage'])['input_tokens']==300
    assert client.post('/api/v1/files/'+f['id']+'/summary').json()['cached']
    assert len(calls)==1
    assert provider.config()['model']=='gemini-4-argon'
    assert security.redact('summary-test-secret global-gemini-secret')=='[已隐藏密钥] [已隐藏密钥]'
    page=db.one('SELECT * FROM pages WHERE file_id=?',(f['id'],))
    db.execute('UPDATE pages SET text=? WHERE id=?',('新内容',page['id']))
    assert summary.status(f['id'])['stale']

def test_long_deck_covers_all_pages_and_reuses_parts(client,monkeypatch):
    configured(monkeypatch);p=project(client);f=file(client,p);calls=[]
    db.execute('UPDATE projects SET budget=200 WHERE id=?',(p,))
    db.execute('DELETE FROM sources WHERE file_id=?',(f['id'],));db.execute('DELETE FROM pages WHERE file_id=?',(f['id'],))
    for n in range(1,8):db.execute('INSERT INTO pages(id,file_id,page_number,text) VALUES(?,?,?,?)',(db.uid(),f['id'],n,'检索策略与证据要求。'*600))
    def fake(task,system,user,manifest,images=None):
        calls.append(manifest);return {'text':json.dumps(value(manifest['pages']),ensure_ascii=False)}
    monkeypatch.setattr(tasks,'model_call',fake)
    t=client.post('/api/v1/files/'+f['id']+'/summary').json()['task'];tasks.run_one()
    assert summary.status(f['id'])['task']['status']=='succeeded',db.one('SELECT * FROM tasks WHERE id=?',(t['id'],))
    assert summary.status(f['id'])['summary']['covered_pages']==list(range(1,8))
    assert len(calls)>1
    count=len(calls);db.execute('DELETE FROM material_summaries WHERE file_id=?',(f['id'],))
    client.post('/api/v1/files/'+f['id']+'/summary');tasks.run_one()
    assert len(calls)==count

@pytest.mark.parametrize('bad',[value((999,)),{'overview':'文字','key_points':[],'topics':[]},value((True,))])
def test_invalid_summary_not_published_and_raw_retained(client,monkeypatch,bad):
    configured(monkeypatch);p=project(client);f=file(client,p)
    transport(monkeypatch,lambda r:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(bad,ensure_ascii=False)},'finish_reason':'stop'}]}))
    t=client.post('/api/v1/files/'+f['id']+'/summary').json()['task'];tasks.run_one()
    row=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],));assert row['status']=='failed'
    assert db.loads(row['result'])['raw_text']
    assert not summary.status(f['id'])['summary']
    assert db.one('SELECT * FROM generation_runs')['status']=='completed'

def test_timeout_not_automatically_retried(client,monkeypatch):
    configured(monkeypatch);p=project(client);f=file(client,p);calls=[]
    def handle(request):calls.append(request);raise httpx.ReadTimeout('timeout',request=request)
    transport(monkeypatch,handle)
    t=client.post('/api/v1/files/'+f['id']+'/summary').json()['task'];tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='outcome_unknown'
    assert len(calls)==1
    assert not summary.status(f['id'])['summary']

def test_permissions_rechecked_before_send(client,monkeypatch):
    configured(monkeypatch);p=project(client);f=file(client,p)
    t=client.post('/api/v1/files/'+f['id']+'/summary').json()['task']
    client.patch('/api/v1/files/'+f['id'],json={'egress':'local_only'})
    tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='failed'
    assert not db.rows('SELECT * FROM generation_runs')

def test_budget_checked_before_send(client,monkeypatch):
    configured(monkeypatch);p=project(client);f=file(client,p);client.patch('/api/v1/projects/'+p,json={'budget':0})
    t=client.post('/api/v1/files/'+f['id']+'/summary').json()['task'];tasks.run_one()
    assert '预算不足' in db.one('SELECT error FROM tasks WHERE id=?',(t['id'],))['error']
    assert not db.rows('SELECT * FROM generation_runs')

def test_batch_skips_private_and_returns_reasons(client,monkeypatch):
    configured(monkeypatch);p=project(client);a=file(client,p);b=file(client,p,'私密原文')
    client.patch('/api/v1/files/'+b['id'],json={'egress':'local_only'})
    r=client.post('/api/v1/projects/'+p+'/materials/summarize').json()
    assert r['count']==1 and r['skipped'][0]['file_id']==b['id']

def test_model_list_is_filtered_without_changing_global_capabilities(client,monkeypatch):
    configured(monkeypatch);db.set_setting('capabilities',{'model':'gemini-4-argon','generation':'verified'})
    transport(monkeypatch,lambda r:httpx.Response(200,json={'data':[{'id':'gemini-4-argon'},{'id':'deepseek-chat'},{'id':'DeepSeek-V3.2'}]}))
    r=client.post('/api/v1/material-summary/models')
    assert [m['id'] for m in r.json()['models']]==['deepseek-chat','DeepSeek-V3.2']
    assert db.setting('capabilities')['generation']=='verified'


def test_content_change_during_generation_never_publishes(client,monkeypatch):
    configured(monkeypatch);p=project(client);f=file(client,p)
    def fake(task,system,user,manifest,images=None):
        db.execute('UPDATE pages SET text=? WHERE file_id=?',('修改后的资料',f['id']))
        return {'text':json.dumps(value())}
    monkeypatch.setattr(tasks,'model_call',fake)
    t=client.post('/api/v1/files/'+f['id']+'/summary').json()['task'];tasks.run_one()
    assert db.one('SELECT status FROM tasks WHERE id=?',(t['id'],))['status']=='failed'
    assert not summary.status(f['id'])['summary']


def test_cached_summary_readable_after_key_removed(client,monkeypatch):
    configured(monkeypatch);p=project(client);f=file(client,p)
    monkeypatch.setattr(tasks,'model_call',lambda *args,**kwargs:{'text':json.dumps(value())})
    client.post('/api/v1/files/'+f['id']+'/summary');tasks.run_one()
    monkeypatch.delenv('PAPER_SUMMARY_API_KEY')
    r=client.post('/api/v1/files/'+f['id']+'/summary')
    assert r.status_code==200 and r.json()['cached']
    assert len(db.rows("SELECT * FROM tasks WHERE kind='material_summary'"))==1
