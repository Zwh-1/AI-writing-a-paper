"""Behavioral coverage for document automation; all remote calls are synthetic."""
import json
import pytest
from test_workbench import client,project,file
from app import db,tasks,provider,rule_ai,rule_governance,summary_billing,source_payload,rules


def response(user):
    return {'text':db.dumps({'rules':[{'source_id':s['source_id'],'quote':s['text'],'statement':s['text'],'modality':'mandatory','scope':'global','category':'content'} for s in db.loads(user)['sources']]}),'provider_snapshot':{'model':'deepseek-test'}}


def start(client,pid,f,**changes):
    result=client.post(f'/api/v1/projects/{pid}/rules/ai-extract',json={'file_ids':[f['id']],'automatic':True,'budget_limit':5,**changes})
    assert result.status_code==200,result.text
    return result.json()


def test_large_ocr_locator_never_leaves_process(client,monkeypatch):
    pid=project(client);f=file(client,pid,'摘要必须说明研究方法。',role='rules')
    s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    db.execute('UPDATE sources SET locator=? WHERE id=?',(db.dumps({'page':1,'recognition':[{'text':'OCR_ONLY_SECRET'*10000}],'origin':{'path':'PRIVATE_PATH'}}),s['id']))
    seen=[]
    def fake(task,system,user,manifest,**kwargs):
        seen.append(user);assert len(user)<1000 and 'OCR_ONLY_SECRET' not in user and 'PRIVATE_PATH' not in user
        return response(user)
    monkeypatch.setattr(tasks,'model_call',fake);t=start(client,pid,f);assert tasks.run_one()
    saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['status']=='succeeded' and saved['result']['complete'],saved
    assert len(seen)==1 and db.one('SELECT state FROM rules')['state']=='machine_verified'
    assert 'OCR_ONLY_SECRET' in db.one('SELECT locator FROM sources')['locator']


def test_rule_evidence_must_be_inside_actual_part():
    chunk=[{'id':'s','text':'摘要必须简明。'}]
    data={'text':json.dumps({'rules':[{'source_id':'s','quote':'参考文献至少20篇','statement':'参考文献至少20篇','modality':'mandatory'}]})}
    with pytest.raises(rule_ai.RuleOutputError):rule_ai.validated(data,chunk,{'s':{'text':'摘要必须简明。参考文献至少20篇'}})


def test_single_job_covers_more_than_120_sources(client,monkeypatch):
    pid=project(client);f=file(client,pid,'摘要必须说明研究方法。',role='rules');first=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    with db.transaction():
        for i in range(1000):
            text=f'第{i}项：论文必须记录真实数据。'
            db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),f['id'],first['page_id'],'{}',text,text,'rule_candidate','native',0))
    seen=[]
    def fake(task,system,user,manifest,**kwargs):seen.extend(s['source_id'] for s in db.loads(user)['sources']);return response(user)
    monkeypatch.setattr(tasks,'model_call',fake);t=start(client,pid,f);assert tasks.run_one()
    saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['status']=='succeeded' and saved['result']['processed_source_units']==1001,saved
    assert len(seen)==len(set(seen))==1001
    t2=start(client,pid,f);assert tasks.run_one()
    assert len(seen)==1001
    assert client.get('/api/v1/tasks/'+t2['id']).json()['result']['processed_source_units']==1001


def test_unknown_item_is_isolated_and_not_automatically_resent(client,monkeypatch):
    pid=project(client);f=file(client,pid,'摘要必须说明研究方法。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    for i in range(5):db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),f['id'],s['page_id'],'{}','论文必须引用真实数据。','论文必须引用真实数据。','rule_candidate','native',0))
    calls=[]
    def fake(task,system,user,manifest,**kwargs):
        calls.append(manifest['batch'])
        if manifest['batch']==0:raise provider.ProviderError('remote unknown',unknown=True)
        return response(user)
    monkeypatch.setattr(tasks,'model_call',fake);t=start(client,pid,f);tasks.run_one()
    saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['status']=='incomplete' and 1 in calls
    before=list(calls);t2=start(client,pid,f);tasks.run_one()
    assert calls==before
    assert db.one("SELECT COUNT(*) n FROM rule_work_items WHERE state='unresolved_remote'")['n']==4


def test_machine_rule_can_publish_without_fake_human_confirmation(client,monkeypatch):
    pid=project(client);f=file(client,pid,'摘要必须说明研究方法。',role='rules')
    monkeypatch.setattr(tasks,'model_call',lambda task,system,user,manifest,**kw:response(user))
    t=start(client,pid,f,auto_publish=True);tasks.run_one();saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['result'].get('rule_package'),saved
    r=db.one('SELECT * FROM rules');assert r['state']=='machine_verified' and r['modality']=='recommended'
    assert db.one('SELECT review_state FROM pages')['review_state']=='unreviewed'
    package=db.one('SELECT * FROM rule_packages');assert db.loads(package['rules'])[0]['state']=='machine_verified'
    decision=db.one('SELECT id FROM rule_decisions')
    assert client.post(f'/api/v1/projects/{pid}/rules/decisions/{decision["id"]}/restore').status_code==200
    assert db.one('SELECT state FROM rules')['state']=='candidate'


def test_changed_source_invalidates_machine_publication(client):
    pid=project(client);f=file(client,pid,'摘要必须说明研究方法。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),pid,s['id'],s['text'],'pending',db.now()))
    rule_governance.review_project(pid)
    db.execute('UPDATE sources SET text=? WHERE id=?',('原文已变化',s['id']))
    result=client.post(f'/api/v1/projects/{pid}/rule-packages')
    assert result.status_code==422 and 'RULE_MACHINE_REVIEW_STALE' in result.text


def test_review_preserves_examples_and_quarantines_binding_conflicts(client):
    pid=project(client);f=file(client,pid,'参考文献至少20篇。\n参考文献至少30篇。\n错误示例：论文必须编造数据。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    db.set_setting('rule_source_policy:'+pid,{f['id']:'binding'})
    for statement in ('参考文献至少20篇。','参考文献至少30篇。','论文必须编造数据。'):
        db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),pid,s['id'],statement,'pending',db.now()))
    result=rule_governance.review_project(pid)
    assert db.one("SELECT COUNT(*) n FROM rules WHERE state='machine_verified'")['n']==0
    assert db.one("SELECT COUNT(*) n FROM rule_decisions WHERE outcome='conflict'")['n']==2


def test_dynamic_reservation_keeps_unknown_price_conservative():
    cfg={**provider.DEFAULT,'input_price':1,'output_price':2,'max_output_tokens':1000}
    amount,evidence=summary_billing.request_reservation(cfg,'规则','原文')
    assert 0<amount<0.01 and evidence['prices_known']
    amount,evidence=summary_billing.request_reservation({**cfg,'output_price':None},'规则','原文')
    assert amount==2 and not evidence['prices_known']


def test_automatic_budget_parameters_validated(client):
    pid=project(client);f=file(client,pid,'论文必须真实。',role='rules')
    for changes in ({'budget_limit':True},{'budget_limit':-1},{'budget_limit':21},{'gpt_budget':6,'budget_limit':5}):
        r=client.post(f'/api/v1/projects/{pid}/rules/ai-extract',json={'file_ids':[f['id']],'automatic':True,**changes})
        assert r.status_code==422


def test_completed_remote_response_is_replayed_after_process_interruption(client,monkeypatch):
    from app import security
    pid=project(client);f=file(client,pid,'论文必须提供真实数据。',role='rules');calls=[]
    def interrupted(task,system,user,manifest,**kwargs):
        calls.append(1);output=response(user)
        meta={**manifest,'output_blob':security.put_blob(output['text'].encode())}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(db.uid(),task['id'],pid,'completed','deepseek-test','openai',db.dumps(meta),2,db.now()))
        raise InterruptedError('simulated process interruption after remote success')
    monkeypatch.setattr(tasks,'model_call',interrupted);t=start(client,pid,f);tasks.run_one()
    retry=client.post('/api/v1/tasks/'+t['id']+'/retry',json={'acknowledge_possible_duplicate_charge':True}).json()
    tasks.run_one();saved=client.get('/api/v1/tasks/'+retry['id']).json()
    assert saved['status']=='succeeded' and calls==[1],saved
    assert saved['result']['processed_source_units']==1


def test_reconciled_group_anchor_cannot_erase_job_spending(client):
    pid=project(client);old=tasks.enqueue(pid,'provider_probe',{});job=tasks.enqueue(pid,'rules_ai',{})
    db.execute('INSERT INTO rule_jobs VALUES(?,?,?,?,?,?,?)',(job['id'],pid,1,1,1,'{}',db.now()))
    older=db.uid();newer=db.uid();group=db.uid()
    for rid,tid,manifest in ((older,old['id'],{}),(newer,job['id'],{'rule_job_id':job['id'],'provider_route':'gpt'})):
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(rid,tid,pid,'completed','gpt-test','openai',db.dumps(manifest),2,db.now()))
    db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',(group,pid,'https://relay.example','fingerprint','gpt-test',0.3,'USD',older,'{}',db.now()))
    for rid in (older,newer):db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(rid,group))
    assert db.one('SELECT budget_charge FROM generation_budget_usage WHERE id=?',(newer,))['budget_charge']==0
    assert summary_billing.job_charge(job['id'])==0.3
    assert summary_billing.job_charge(job['id'],'gpt')==0.3


def test_existing_paraphrase_is_semantically_reviewed_and_reversible(client,monkeypatch):
    pid=project(client);f=file(client,pid,'摘要必须说明研究方法。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],));rid=db.uid()
    original='摘要应交代采用的方法。'
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(rid,pid,s['id'],original,'pending',db.now()))
    def fake(task,system,user,manifest,**kwargs):
        assert manifest['purpose']=='rule_verification'
        return {'text':db.dumps({'decisions':[{'rule_id':rid,'relation':'equivalent','kind':'requirement','quote':s['text']}]})}
    monkeypatch.setattr(tasks,'model_call',fake);t=start(client,pid,f,review_existing_only=True);tasks.run_one()
    saved=client.get('/api/v1/tasks/'+t['id']).json();assert saved['status']=='succeeded',saved
    r=db.one('SELECT * FROM rules WHERE id=?',(rid,));assert r['state']=='machine_verified' and r['statement']==original
    decision=db.one('SELECT * FROM rule_decisions WHERE rule_id=? ORDER BY id DESC LIMIT 1',(rid,))
    rule_governance.restore(pid,decision['id']);assert db.one('SELECT statement FROM rules WHERE id=?',(rid,))['statement']==original


def test_semantic_guard_does_not_accept_negation_or_numeric_reversal():
    from app.rule_verification import guards
    assert not guards('摘要可以编造数据','摘要不得编造数据')
    assert not guards('参考文献至少20篇','参考文献至少30篇')
    assert guards('参考文献至少20篇','参考文献不少于20篇')
    assert not guards('参考文献至少二十篇','参考文献至少三十篇')
    assert not guards('摘要至少20字','摘要至少20篇')
    assert guards('参考文献至少二十篇','参考文献不少于20篇')


def test_invalid_candidate_does_not_discard_independent_valid_objects():
    from app.rule_pipeline import validated_partial
    chunk=[{'id':'a','text':'参考文献不少于20篇。'},{'id':'b','text':'论文必须真实。'}]
    payload={'rules':[{'source_id':'a','quote':'参考文献不少于20篇','statement':'参考文献不少于20篇','modality':'mandatory'},{'source_id':'b','quote':'完全不存在的依据','statement':'完全不存在的依据','modality':'mandatory'}]}
    good,issues=validated_partial({'text':db.dumps(payload)},chunk,{s['id']:s for s in chunk})
    assert len(good)==1 and good[0][1]=='a' and set(issues)=={'b'}


def test_zero_budget_retains_local_results_without_provider_generation(client,monkeypatch):
    pid=project(client);f=file(client,pid,'论文必须提供真实数据。',role='rules')
    monkeypatch.setattr(summary_billing,'reconcile',lambda *a,**k:{'reconciled':False})
    def forbidden(*a,**k):raise AssertionError('provider generation must not be called')
    monkeypatch.setattr(provider,'generate',forbidden)
    from app import provider_stream
    monkeypatch.setattr(provider_stream,'generate',forbidden)
    t=start(client,pid,f,budget_limit=0)
    with provider.request_scope(provider.DEFAULT,'fake-key'):tasks.run_one()
    saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['status']=='budget_blocked',saved
    assert db.one('SELECT state FROM rules')['state']=='machine_verified'
    assert not db.rows('SELECT * FROM generation_runs')


def test_active_job_is_idempotent_after_payload_checkpoint_update(client):
    pid=project(client);f=file(client,pid,'论文必须真实。',role='rules');first=start(client,pid,f)
    payload=db.loads(first['payload']);payload['rule_job_id']=first['id']
    db.execute("UPDATE tasks SET payload=?,status='running' WHERE id=?",(db.dumps(payload),first['id']))
    repeated=start(client,pid,f);assert repeated['id']==first['id']


def test_group_resolution_preserves_explicit_user_choice(client):
    pid=project(client);f=file(client,pid,'参考文献至少20篇。参考文献至少30篇。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    db.set_setting('rule_source_policy:'+pid,{f['id']:'binding'});ids=[]
    for statement in ('参考文献至少20篇。','参考文献至少30篇。'):
        rid=db.uid();ids.append(rid);db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(rid,pid,s['id'],statement,'pending',db.now()))
    rule_governance.review_project(pid)
    result=client.post(f'/api/v1/projects/{pid}/rules/resolve-conflict',json={'rule_id':ids[1]})
    assert result.status_code==200,result.text
    assert db.one('SELECT state FROM rules WHERE id=?',(ids[1],))['state']=='confirmed'
    assert db.one('SELECT state FROM rules WHERE id=?',(ids[0],))['state']=='rejected'
    assert not rules.conflicts(pid,mandatory_only=True)


def test_cached_input_accounting_releases_only_exactly_matched_bill(client,monkeypatch):
    import httpx
    from app import material_summary,security
    pid=project(client);cfg={**material_summary.config(),'currency':'USD'}
    db.set_setting('material_summary_provider',cfg);security.save_key('fake-cache-bill-key','material-summary')
    task=tasks.enqueue(pid,'rules_ai',{})
    for tokens,cached in ((100,20),(200,40)):
        usage={'input_tokens':tokens,'output_tokens':25,'raw':{'prompt_tokens':tokens,'completion_tokens':25,'prompt_tokens_details':{'cached_tokens':cached}}}
        meta={'provider':cfg,'credential_hash':summary_billing.key_hash('fake-cache-bill-key')}
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),task['id'],pid,'completed',cfg['model'],'openai',db.dumps(meta),db.dumps(usage),2,db.now()))
    def handle(request):return httpx.Response(200,json={'remaining':20,'unit':'USD','model_stats':[{'model':cfg['model'],'requests':2,'input_tokens':240,'output_tokens':50,'cost':0.02}]})
    monkeypatch.setattr(provider,'client',lambda cfg:httpx.Client(transport=httpx.MockTransport(handle)))
    result=summary_billing.reconcile(pid);assert result['reconciled'],result
    assert summary_billing.budget(pid)['used']==0.02
    evidence=db.loads(db.one('SELECT evidence FROM generation_billing_groups')['evidence'])
    assert evidence['token_accounting']['input_tokens']['reported_cached_tokens']==60
    assert evidence['token_accounting']['input_tokens']['method']=='input_minus_reported_cached'


def test_grade_qualified_requirements_remain_conditional_references(client):
    pid=project(client);f=file(client,pid,'A级（86-100分）论文必须提供创新成果。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    db.set_setting('rule_source_policy:'+pid,{f['id']:'binding'})
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(db.uid(),pid,s['id'],s['text'],'pending',db.now()))
    rule_governance.review_project(pid)
    assert db.one('SELECT state FROM rules')['state']=='reference_only'
    assert db.one('SELECT outcome FROM rule_decisions')['outcome']=='conditional_reference'


def test_semantically_classified_knowledge_leaves_candidate_queue(client):
    pid=project(client);f=file(client,pid,'这里介绍文献检索的基本概念。',role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],));s['file_sha256']=f['sha256']
    rid=db.uid();db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(rid,pid,s['id'],'文献检索知识概述','pending',db.now()))
    r=db.one('SELECT * FROM rules WHERE id=?',(rid,));rule_governance.record(pid,r,r,s,'knowledge',['模型复核为知识说明'])
    rule_governance.review_project(pid)
    assert db.one('SELECT state FROM rules WHERE id=?',(rid,))['state']=='reference_only'


def test_rule_tables_are_installed_on_already_upgraded_database(client):
    for name in ('rule_job_items','rule_work_items','rule_decisions','rule_page_checks','rule_verification_attempts','rule_jobs'):
        db.execute('DROP TABLE '+name)
    version=db.one('PRAGMA user_version')['user_version']
    db.init()
    assert db.one('PRAGMA user_version')['user_version']==version
    assert db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='rule_work_items'")


def test_job_budget_reconciles_its_active_provider_before_resuming(client,monkeypatch):
    from app import security,model_routing
    pid=project(client);f=file(client,pid,'论文必须使用真实数据。',role='rules')
    security.save_key('fake-budget-backup','gpt-fallback');db.set_setting('gpt_fallback_provider',{**model_routing.DEFAULT,'model':'gpt-6.1-sol'})
    charge={'amount':2};checked=[]
    monkeypatch.setattr(summary_billing,'job_charge',lambda *a,**k:charge['amount'])
    def reconcile(pid,profile):
        checked.append(profile)
        if profile=='material-summary':charge['amount']=0.5
        return {'reconciled':True}
    monkeypatch.setattr(summary_billing,'reconcile',reconcile)
    monkeypatch.setattr(tasks,'model_call',lambda task,system,user,manifest,**kw:response(user))
    t=start(client,pid,f,budget_limit=3,auto_publish=True);tasks.run_one()
    saved=client.get('/api/v1/tasks/'+t['id']).json()
    assert saved['status']=='succeeded',saved
    assert checked==['material-summary']
    before=db.one('SELECT COUNT(*) n FROM rule_packages')['n']
    again=start(client,pid,f,budget_limit=3,auto_publish=True);tasks.run_one()
    assert client.get('/api/v1/tasks/'+again['id']).json()['status']=='succeeded'
    assert db.one('SELECT COUNT(*) n FROM rule_packages')['n']==before


def test_completed_job_finalizes_only_its_unmatched_billing(client,monkeypatch):
    from app.rule_pipeline import finalize_billing
    pid=project(client);task=tasks.enqueue(pid,'rules_ai',{});rid=db.uid()
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(rid,task['id'],pid,'completed','deepseek-test','openai',db.dumps({'provider_route':'deepseek'}),2,db.now()))
    called=[]
    def reconcile(pid,profile):
        called.append(profile);db.execute('UPDATE generation_runs SET cost=? WHERE id=?',(0.03,rid));return {'reconciled':True,'amount':0.03}
    monkeypatch.setattr(summary_billing,'reconcile',reconcile)
    result=finalize_billing(task)
    assert called==['material-summary'] and result[0]['reconciled']
    assert summary_billing.budget(pid)['used']==0.03
