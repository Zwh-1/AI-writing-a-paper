import json
import pytest
from test_workbench import client,project,file
from app import db,tasks,provider,model_routing,security,rule_governance,rule_ai,summary_billing
from app.rule_verification import fingerprint,guards,task_coverage
from app.rule_review_contract import validate
from app.rule_review_receipts import receipt,billing_baseline,freeze_receipt,backfill_closed_receipts


def candidate(c,p,text='摘要必须说明研究方法。',statement='摘要应交代采用的方法。'):
    f=file(c,p,text,role='rules');s=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],));rid=db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(rid,p,s['id'],statement,'pending',db.now()))
    return f,s,rid


def submit(c,p,**extra):
    result=c.post(f'/api/v1/projects/{p}/rules/ai-extract',json={'automatic':True,'review_existing_only':True,'budget_limit':5,**extra})
    assert result.status_code==200,result.text
    task=result.json();assert tasks.run_one();return c.get('/api/v1/tasks/'+task['id']).json()


def choice(rid,quote,**extra):return {'rule_id':rid,'relation':'equivalent','kind':'requirement','quote':quote,**extra}


def fake_response(values):return {'text':db.dumps({'decisions':values})}


def gpt_ready():
    security.save_key('fake-integrity-gpt','gpt-fallback');db.set_setting('gpt_fallback_provider',{**model_routing.DEFAULT,'model':'gpt-6.1-sol'})


def test_negative_without_evidence_cannot_archive_rule(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p)
    monkeypatch.setattr(tasks,'model_call',lambda *a,**k:fake_response([choice(rid,'',relation='not_a_rule',kind='knowledge')]))
    t=submit(client,p)
    assert t['status']=='incomplete' and t['result']['review']['semantic_checked']==0
    assert t['result']['review']['unreviewed_count']==1
    assert db.one('SELECT state FROM rules WHERE id=?',(rid,))['state']=='candidate'


def test_missing_decision_does_not_count_as_reviewed(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p);other=db.uid()
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(other,p,s['id'],'摘要需交代具体研究方式。','pending',db.now()))
    monkeypatch.setattr(tasks,'model_call',lambda *a,**k:fake_response([choice(rid,s['text'])]))
    t=submit(client,p);r=t['result']['review']
    assert t['status']=='incomplete' and (r['semantic_checked'],r['semantic_total'],r['unreviewed_count'])==(1,2,1)
    assert db.one('SELECT state FROM rules WHERE id=?',(other,))['state']=='candidate'


def test_explicit_retry_only_sends_missing_rule_not_successful_rules(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p);other=db.uid();seen=[]
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,created_at) VALUES(?,?,?,?,?,?)',(other,p,s['id'],'摘要需交代所用的研究方式。','pending',db.now()))
    def fake(task,system,user,manifest,**kwargs):
        ids=[r['rule_id'] for r in db.loads(user)['candidates']];seen.append(ids)
        return fake_response([choice(ids[0],s['text'])])
    monkeypatch.setattr(tasks,'model_call',fake);first=submit(client,p)
    assert first['status']=='incomplete'
    retry=client.post('/api/v1/tasks/'+first['id']+'/retry',json={'acknowledge_possible_duplicate_charge':True,'retry_invalid':True})
    assert retry.status_code==200,retry.text
    assert tasks.run_one();second=client.get('/api/v1/tasks/'+retry.json()['id']).json()
    assert second['status']=='succeeded',second
    assert len(seen[0])==2 and seen[1]==[other]


def test_duplicate_decisions_do_not_choose_the_first(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p)
    monkeypatch.setattr(tasks,'model_call',lambda *a,**k:fake_response([choice(rid,s['text']),choice(rid,s['text'],relation='not_a_rule',kind='knowledge')]))
    t=submit(client,p)
    assert t['status']=='incomplete' and t['result']['review']['semantic_checked']==0
    assert db.one('SELECT state FROM rules WHERE id=?',(rid,))['state']=='candidate'


def test_foreign_rule_id_is_never_applied():
    items=[({'id':'a','statement':'论文必须真实。'},{'id':'s','text':'论文必须真实。'},'fp')]
    valid,issues,warnings=validate({'decisions':[choice('foreign','论文必须真实。')]},items,guards,rule_ai.exact_quote)
    assert not valid and set(issues)=={'a'} and warnings


def test_negative_evidence_also_needs_a_reason():
    items=[({'id':'a','statement':'介绍知识'},{'id':'s','text':'这是检索技术的定义说明。'},'fp')]
    valid,issues,_=validate({'decisions':[choice('a','这是检索技术的定义说明。',relation='not_a_rule',kind='knowledge')]},items,guards,rule_ai.exact_quote)
    assert not valid and 'a' in issues


def test_uncertainty_is_preserved_without_forcing_a_negative_label():
    items=[({'id':'a','statement':'原文含混'},{'id':'s','text':'原文含混，无法确定。'},'fp')]
    valid,issues,_=validate({'decisions':[choice('a','',relation='uncertain',kind='unknown')]},items,guards,rule_ai.exact_quote)
    assert valid['a']['relation']=='uncertain' and not issues


def test_context_quotes_preserve_grade_and_score_conditions():
    source={'id':'s','text':'A级，86-100分。论文必须给出充分论据。'}
    items=[({'id':'a','statement':'A级，86-100分，论文必须给出充分论据。'},source,'fp')]
    values=[choice('a','论文必须给出充分论据。',kind='conditional',context_quotes=['A级，86-100分。'])]
    valid,issues,_=validate({'decisions':values},items,guards,rule_ai.exact_quote)
    assert 'a' in valid and not issues


def test_context_cannot_be_spliced_across_grades():
    source={'id':'s','text':'A级，86-100分。要求严密。B级，70-85分。论文必须给出论据。'}
    items=[({'id':'a','statement':'A级，86-100分，论文必须给出论据。'},source,'fp')]
    values=[choice('a','论文必须给出论据。',kind='conditional',context_quotes=['A级，86-100分。'])]
    valid,issues,_=validate({'decisions':values},items,guards,rule_ai.exact_quote)
    assert not valid and 'a' in issues


def test_selected_gpt_is_not_silently_replaced_by_primary(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p);gpt_ready();seen=[]
    def fake(task,*args,**kwargs):seen.append(db.loads(task['payload'])['provider_profile']);return fake_response([choice(rid,s['text'])])
    monkeypatch.setattr(tasks,'model_call',fake)
    t=submit(client,p,provider_profile='gpt')
    assert t['status']=='succeeded',t
    assert seen==['gpt'] and t['result']['review']['requested_profile']=='gpt'


@pytest.mark.parametrize('review_only',[False,True])
def test_explicit_model_choice_is_preserved_on_retry(client,review_only):
    p=project(client);task=tasks.enqueue(p,'rules_ai',{'provider_profile':'gpt','review_existing_only':review_only})
    db.execute("UPDATE tasks SET status='failed' WHERE id=?",(task['id'],))
    response=client.post('/api/v1/tasks/'+task['id']+'/retry',json={'acknowledge_possible_duplicate_charge':True})
    assert response.status_code==200,response.text
    assert db.loads(response.json()['payload'])['provider_profile']=='gpt'


def test_explicit_gpt_with_disabled_permission_rejected_before_queue(client):
    p=project(client);candidate(client,p);gpt_ready()
    response=client.post(f'/api/v1/projects/{p}/rules/ai-extract',json={'automatic':True,'provider_profile':'gpt','allow_gpt':False,'budget_limit':5})
    assert response.status_code==422 and not db.rows("SELECT * FROM tasks WHERE kind='rules_ai'")


def test_unrelated_source_policy_does_not_invalidate_cache(client):
    p=project(client);f,s,rid=candidate(client,p);r=db.one('SELECT * FROM rules WHERE id=?',(rid,));src=next(x for x in rule_ai.source_scope(p) if x['id']==s['id'])
    before=fingerprint(r,src);db.set_setting('rule_source_policy:'+p,{'unrelated-file':'binding'})
    assert fingerprint(r,src)==before
    db.set_setting('rule_source_policy:'+p,{f['id']:'binding'})
    assert fingerprint(r,src)!=before
    assert fingerprint(r,src,'primary')!=fingerprint(r,src,'auto')


def test_cache_reuse_keeps_original_task_review_history(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p);calls=[]
    def fake(*a,**k):calls.append(1);return fake_response([choice(rid,s['text'],relation='uncertain')])
    monkeypatch.setattr(tasks,'model_call',fake)
    first=submit(client,p);second=submit(client,p)
    assert calls==[1] and second['result']['review']['reused_decisions']==1
    assert task_coverage(first['id'])['checked']==1 and task_coverage(second['id'])['checked']==1


def test_policy_change_preserves_other_file_decisions(client):
    p=project(client);f1,s1,r1=candidate(client,p,statement='摘要必须说明研究方法。')
    db.execute('UPDATE files SET name=? WHERE id=?',('one.txt',f1['id']))
    f2,s2,r2=candidate(client,p,text='论文必须引用真实依据。',statement='论文必须引用真实依据。')
    rule_governance.review_project(p)
    decision=db.one('SELECT * FROM rule_decisions WHERE rule_id=? ORDER BY id DESC LIMIT 1',(r1,))
    result=client.put(f'/api/v1/projects/{p}/rules/source-policy',json={'files':{f2['id']:'binding'}})
    assert result.status_code==200
    assert db.one('SELECT state FROM rules WHERE id=?',(r1,))['state']=='machine_verified'
    assert db.one('SELECT restored FROM rule_decisions WHERE id=?',(decision['id'],))['restored']==0
    assert db.one('SELECT state FROM rules WHERE id=?',(r2,))['state']=='candidate'


def test_descriptive_criterion_is_not_discarded_for_lacking_must(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p,text='有对结果的分析和反思。',statement='论文应对结果进行分析和反思。')
    monkeypatch.setattr(tasks,'model_call',lambda *a,**k:fake_response([choice(rid,s['text'],kind='criterion')]))
    t=submit(client,p)
    assert t['status']=='succeeded',t
    rule=db.one('SELECT * FROM rules WHERE id=?',(rid,))
    assert rule['state']=='machine_verified' and rule['statement']=='论文应对结果进行分析和反思。'


def test_concurrent_human_edit_is_not_overwritten(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p)
    def fake(*a,**k):
        db.execute("UPDATE rules SET statement='用户明确修订',state='confirmed' WHERE id=?",(rid,))
        return fake_response([choice(rid,s['text'])])
    monkeypatch.setattr(tasks,'model_call',fake);t=submit(client,p)
    assert t['status']=='incomplete'
    assert db.one('SELECT statement FROM rules WHERE id=?',(rid,))['statement']=='用户明确修订'


def test_unknown_review_is_not_resent_or_switched_automatically(client,monkeypatch):
    p=project(client);f,s,rid=candidate(client,p);gpt_ready();seen=[]
    def fake(task,*a,**k):seen.append(db.loads(task['payload'])['provider_profile']);raise provider.ProviderError('unknown',unknown=True)
    monkeypatch.setattr(tasks,'model_call',fake)
    first=submit(client,p,allow_gpt=True,gpt_budget=3);assert first['status']=='incomplete'
    second=submit(client,p,allow_gpt=True,gpt_budget=3)
    assert second['status']=='incomplete' and seen==['primary']


@pytest.mark.parametrize('a,b',[('论文建议至少20篇参考文献','论文必须至少20篇参考文献'),('必须编造数据','不需要编造数据'),('论文不得不说明方法','论文不得说明方法')])
def test_modality_and_double_negation_are_not_equivalent(a,b):assert not guards(a,b)


def test_equivalent_upper_bounds_do_not_gain_false_prohibition():assert guards('摘要不超过500字','摘要不得超过500字')


def test_real_models_are_separate_from_cached_model_labels(client):
    p=project(client);t=tasks.enqueue(p,'rules_ai',{})
    db.execute('UPDATE tasks SET result=? WHERE id=?',(db.dumps({'models_used':['gpt-6.1-sol']}),t['id']))
    db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,usage,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(db.uid(),t['id'],p,'completed','deepseek-test','openai','{}',db.dumps({'input_tokens':100,'output_tokens':20}),2,db.now()))
    got=client.get('/api/v1/tasks/'+t['id']).json()['review_receipt']
    assert got['models']==[{'model':'deepseek-test','calls':1}] and got['confirmed_amount'] is None


def test_shared_bill_cannot_be_mistaken_for_task_fee_and_receipt_stays_stable(client):
    p=project(client);old=tasks.enqueue(p,'provider_probe',{});t=tasks.enqueue(p,'rules_ai',{});oid=db.uid();rid=db.uid();gid=db.uid()
    def run(identity,task,meta):db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(identity,task['id'],p,'completed','deepseek-test','openai',db.dumps(meta),2,db.now()))
    run(oid,old,{})
    db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,p,'https://relay.example','key-hash','deepseek-test',1,'USD',oid,'{}',db.now()))
    db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(oid,gid))
    baseline=billing_baseline(p);db.execute('INSERT INTO rule_jobs VALUES(?,?,?,?,?,?,?)',(t['id'],p,5,0,0,db.dumps({'currency':'USD','billing_baseline':baseline}),db.now()))
    t={**t,'payload':db.dumps({'rule_job_id':t['id']})};run(rid,t,{'rule_job_id':t['id']})
    db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(rid,gid));db.execute('UPDATE generation_billing_groups SET amount=1.03 WHERE id=?',(gid,))
    assert receipt(t)['confirmed_amount']==0.03
    freeze_receipt(t);other=db.uid();run(other,old,{})
    db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(other,gid));db.execute('UPDATE generation_billing_groups SET amount=1.08 WHERE id=?',(gid,))
    assert receipt(t)['confirmed_amount']==0.03


def test_old_task_fee_can_be_recovered_only_from_closed_billing_prefixes(client):
    p=project(client);old=tasks.enqueue(p,'provider_probe',{});t=tasks.enqueue(p,'rules_ai',{});oid=db.uid();rid=db.uid();gid=db.uid()
    for identity,task,at in ((oid,old,'2020-01-01T00:00:00+00:00'),(rid,t,'2020-01-01T00:02:00+00:00')):
        db.execute('INSERT INTO generation_runs(id,task_id,project_id,status,model,protocol,manifest,reserved,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(identity,task['id'],p,'completed','deepseek-test','openai','{}',2,at))
    db.execute('INSERT INTO generation_billing_groups VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,p,'https://relay.example','key-hash','deepseek-test',1.03,'USD',oid,'{}',db.now()))
    for identity in (oid,rid):db.execute('INSERT INTO generation_billing_group_runs VALUES(?,?)',(identity,gid))
    for number,amount,at in ((1,1,'2020-01-01T00:01:00+00:00'),(2,1.03,'2020-01-01T00:03:00+00:00')):
        data={'group_id':gid,'requests':number,'amount':amount,'currency':'USD','method':'matched_dedicated_key_model_total_conservative_max'}
        db.execute('INSERT INTO audit(project_id,action,detail,created_at) VALUES(?,?,?,?)',(p,'summary_billing_reconciled',db.dumps(data),at))
    result=backfill_closed_receipts(p)
    assert result==[{'task_id':t['id'],'amount':0.03,'currency':'USD'}]
    assert receipt(t)['confirmed_amount']==0.03
