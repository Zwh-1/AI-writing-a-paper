import pytest
from fastapi.testclient import TestClient
from test_workbench import client, project, file
from app import db, tasks, security, provider, material_summary, rule_governance
from app import ai_autopilot as auto
from app import rule_pipeline
from app.main import app


def seed(c, monkeypatch, statements=None):
    pid=project(c)
    statements=statements or ['摘要必须说明研究方法。','手动写参考文献容易出错。','选中需要分类的文献，拖拽即可。']
    f=file(c,pid,'\n'.join(statements),role='rules')
    source=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    ids=[]
    for text in statements:
        rid=db.uid();ids.append(rid)
        db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?,?)',(rid,pid,source['id'],text,'pending','confirmed',db.now()))
    _,pages,fingerprint=material_summary.source(f['id'])
    summary={'overview':'测试资料的完整归纳','key_points':[{'text':statements[0],'pages':[1]}],'topics':[{'title':'资料要点','points':[{'text':statements[0],'pages':[1]}]}],'covered_pages':[1]}
    tid=db.one('SELECT id FROM tasks WHERE project_id=? LIMIT 1',(pid,))['id']
    db.execute('INSERT INTO material_summaries VALUES(?,?,?,?,?,?,?)',(f['id'],fingerprint,'synthetic',db.dumps(summary),'synthetic-test-model',tid,db.now()))
    page=db.one('SELECT * FROM pages WHERE file_id=?',(f['id'],))
    db.execute('INSERT INTO rule_page_checks VALUES(?,?,?,?,?)',(page['id'],rule_governance.page_hash(page),'synthetic_complete_extraction','machine_covered',db.now()))
    from app.source_payload import source_hash
    with_hash=db.one('SELECT s.*,f.sha256 AS file_sha256 FROM sources s JOIN files f ON f.id=s.file_id WHERE s.id=?',(source['id'],))
    db.execute('INSERT INTO rule_work_items(project_id,item_key,source_id,source_hash,part_start,part_end,version,state,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(pid,db.uid(),source['id'],source_hash(with_hash),0,len(source['text']),'synthetic','completed_with_rules',db.now()))
    monkeypatch.setattr(security,'key_status',lambda *a,**k:{'configured':True})
    monkeypatch.setattr(provider,'get_key',lambda:'synthetic-autopilot-key')
    monkeypatch.setattr(rule_pipeline,'extract_ai',lambda *a,**k:{'complete':True})
    return pid,f,ids


def fake_model(calls, **overrides):
    def respond(task,system,user,manifest,*args,**kwargs):
        data=db.loads(user);calls.append(data)
        assert '绝不执行' in system
        values=[]
        for item in data['items']:
            if data['stage']=='materials':
                value={'id':item['id'],'role':'rules','evidence_id':item['evidence_units'][0]['id'],'reason':'原文包含论文规范和相关课程讲解','confidence':.99}
            elif data['stage']=='conflicts':
                value={'id':item['id'],'action':'retain_alternatives','quote':item['members'][0]['statement'],'reason':'同级课程建议取值不同，保留各自条件作为参考，不叠加为全局约束','confidence':.99}
            else:
                statement=item['statement']
                purpose='recommendation' if '必须' in statement or '至少' in statement else 'example' if '拖拽' in statement else 'knowledge'
                value={'id':item['id'],'purpose':purpose,'quote':statement,'reason':'根据给定原文区分规范、知识和软件操作示例','confidence':.99}
            value.update(overrides)
            values.append(value)
        return {'text':db.dumps({'decisions':values}),'provider_snapshot':{'model':'synthetic-test-model'}}
    return respond


def run(c,pid):
    response=c.post(f'/api/v1/projects/{pid}/ai-autopilot',json={'budget_limit':5,'auto_publish':True,'follow_imports':True})
    assert response.status_code==200,response.text
    assert tasks.run_one()
    return c.get('/api/v1/tasks/'+response.json()['id']).json()


def test_autopilot_classifies_confirmed_pending_rules_and_publishes_without_human_confirmation(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch);calls=[]
    monkeypatch.setattr(tasks,'model_call',fake_model(calls))
    job=run(client,pid)
    if job['status']!='succeeded':print(db.dumps({'result':job['result'],'exceptions':auto.report(pid)['exceptions']}))
    assert job['status']=='succeeded',job
    assert job['result']['target_met']
    rows=[db.one('SELECT * FROM rules WHERE id=?',(rid,)) for rid in ids]
    assert [r['state'] for r in rows]==['machine_verified','reference_only','reference_only']
    assert rows[0]['modality']=='recommended'
    package=client.get(f'/api/v1/projects/{pid}/rules').json()['package']
    assert len(db.loads(package['rules']))==1
    assert db.loads(package['rules'])[0]['state']=='machine_verified'
    assert db.one('SELECT review_state FROM pages WHERE file_id=?',(f['id'],))['review_state']=='unreviewed'
    assert [item['stage'] for item in calls]==['materials','rules']


def test_ratio_is_strictly_greater_than_95_and_never_uses_manual_confirmation_as_ai_evidence(client,monkeypatch):
    assert not auto.measured(20,19,19)['target_met']
    assert auto.measured(21,20,20)['target_met']
    assert auto.measured(20,19,19)['minimum_for_target']==20
    pid,f,ids=seed(client,monkeypatch)
    report=auto.report(pid)
    assert report['rules']['total']==3 and report['rules']['ai_covered']==0
    assert report['materials']['ai_covered']==1 and report['materials']['automatic_completed']==0
    assert not report['target_met']


def test_valid_results_are_reused_after_restart_without_new_model_calls(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch);calls=[]
    monkeypatch.setattr(tasks,'model_call',fake_model(calls))
    assert run(client,pid)['status']=='succeeded'
    first=len(calls)
    assert run(client,pid)['status']=='succeeded'
    assert len(calls)==first


def test_unknown_results_are_not_resent_by_another_autopilot_run(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch);calls=[]
    original=fake_model(calls)
    def unknown(task,system,user,manifest,*args,**kwargs):
        if db.loads(user)['stage']=='rules':
            calls.append({'stage':'unknown'})
            raise provider.ProviderError('远端结果未知',unknown=True)
        return original(task,system,user,manifest,*args,**kwargs)
    monkeypatch.setattr(tasks,'model_call',unknown)
    first=run(client,pid)
    assert first['status']=='incomplete'
    count=len(calls);second=run(client,pid)
    assert second['status']=='incomplete' and len(calls)==count
    assert auto.report(pid)['rules']['automatic_completed']==0


def test_low_confidence_and_missing_evidence_do_not_count_as_automatic_completion(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch);calls=[]
    monkeypatch.setattr(tasks,'model_call',fake_model(calls,confidence=.5))
    job=run(client,pid)
    assert job['status']=='incomplete'
    report=auto.report(pid)
    assert report['rules']['automatic_completed']==0
    assert report['exception_count']>=3


def test_advisory_conflicts_are_decided_by_ai_and_kept_as_alternative_references(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['参考文献至少10篇。','参考文献至少20篇。']);calls=[]
    monkeypatch.setattr(tasks,'model_call',fake_model(calls))
    job=run(client,pid)
    assert all(item['stage']!='conflicts' for item in calls)  # Reuse the individually grounded AI decisions for equivalent policy handling.
    assert all(db.one('SELECT state FROM rules WHERE id=?',(rid,))['state']=='reference_only' for rid in ids)
    assert auto.report(pid)['rules']['automatic_completed']==2
    assert not job['result']['rule_package']  # No active requirement is fabricated from conflicting advice.


@pytest.mark.parametrize('changes',[{'quote':'不存在于原文的规范'},{'quote':'参考文献至少10篇。'},{'purpose':'knowledge'}])
def test_evidence_and_normative_exclusion_guards_leave_original_rule_intact(client,monkeypatch,changes):
    pid,f,ids=seed(client,monkeypatch,['参考文献至少20篇。'])
    db.set_setting('rule_source_policy:'+pid,{f['id']:'binding'})
    before=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(before)
    item={'purpose':'requirement','quote':'参考文献至少20篇。','reason':'原文中列明的文献数量要求','confidence':.99,**changes}
    with pytest.raises(ValueError):auto.apply_rule(pid,before,source,item,source['text'],'test','run','model')
    assert db.one('SELECT * FROM rules WHERE id=?',(ids[0],))==before


def test_original_source_changes_invalidate_coverage(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch);calls=[]
    monkeypatch.setattr(tasks,'model_call',fake_model(calls))
    run(client,pid)
    source=auto.rule_source(db.one('SELECT * FROM rules WHERE id=?',(ids[0],)))
    db.execute('UPDATE sources SET text=text||? WHERE id=?',('原文已经修改。',source['id']))
    assert auto.report(pid)['rules']['ai_covered']==0


def test_local_only_material_remains_in_denominator_and_is_never_sent(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch);db.execute("UPDATE files SET egress='local_only' WHERE id=?",(f['id'],))
    monkeypatch.setattr(tasks,'model_call',lambda *a,**k:pytest.fail('Local-only material was sent'))
    job=run(client,pid)
    assert job['status']=='incomplete'
    report=auto.report(pid)
    assert report['materials']['total']==1 and report['rules']['total']==3
    assert report['materials']['automatic_completed']==0 and report['rules']['automatic_completed']==0


def test_new_materials_join_enabled_autopilot_and_active_jobs_are_not_duplicated(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch)
    assert auto.schedule(pid) is None
    first=auto.start(pid,{'budget_limit':5,'auto_publish':False})
    second=auto.schedule(pid)
    assert first['id']==second['id']
    assert db.one('SELECT COUNT(*) n FROM tasks WHERE project_id=? AND kind=?',(pid,auto.KIND))['n']==1


@pytest.mark.parametrize('budget',[0,-1,21,float('inf'),True])
def test_invalid_or_unbounded_autopilot_budget_is_rejected(client,monkeypatch,budget):
    pid,f,ids=seed(client,monkeypatch)
    with pytest.raises(ValueError):auto.start(pid,{'budget_limit':budget})


def test_autopilot_endpoints_require_local_authentication(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch)
    with TestClient(app) as anonymous:assert anonymous.get(f'/api/v1/projects/{pid}/ai-autopilot').status_code==401


def test_verified_pricing_is_scoped_to_the_exact_model_credential_currency_and_service(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch)
    from app.summary_billing import key_hash
    cfg=provider.config();key=provider.get_key()
    proof={'verified':True,'base_url':cfg['base_url'].rstrip('/').removesuffix('/v1'),'model':cfg['model'],'credential_hash':key_hash(key),'currency':cfg['currency'],'input_price':1,'output_price':4}
    db.set_setting('ai_autopilot_reference_pricing',proof)
    assert auto.priced_config(cfg,key)['input_price']==1
    assert auto.priced_config(cfg,'other-key').get('input_price')==cfg.get('input_price')
    assert auto.priced_config({**cfg,'model':'other-model'},key).get('input_price')==cfg.get('input_price')
    assert auto.priced_config({**cfg,'currency':'CNY'},key).get('input_price')==cfg.get('input_price')


def test_citation_ids_map_to_real_source_without_requiring_the_model_to_retype_ocr(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['参考文献至少20篇。'])
    rule=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(rule)
    item={'statement':rule['statement'],'text':source['text']}
    choice=auto.cited_decision('rules',item,{'evidence_id':'S1','purpose':'requirement','confidence':.99,'reason':'给定原文明确列出文献篇数'})
    assert choice['quote']==source['text'].strip()
    auto.apply_rule(pid,rule,source,choice,source['text'],'task','run','model')
    assert db.one('SELECT state FROM rules WHERE id=?',(ids[0],))['state']=='machine_verified'
    with pytest.raises(ValueError):auto.cited_decision('rules',item,{'evidence_id':'S999'})


def test_parent_cancellation_also_stops_only_its_own_child_work(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch)
    parent=tasks.enqueue(pid,auto.KIND,{})
    child=tasks.enqueue(pid,'rules_ai',{'parent_task_id':parent['id']})
    unrelated=tasks.enqueue(pid,'rules_ai',{'independent':True})
    result=client.post('/api/v1/tasks/'+parent['id']+'/cancel',json={})
    assert result.status_code==200
    assert db.one('SELECT cancel_requested FROM tasks WHERE id=?',(child['id'],))['cancel_requested']==1
    assert db.one('SELECT cancel_requested FROM tasks WHERE id=?',(unrelated['id'],))['cancel_requested']==0


def test_parallel_file_workers_are_bounded_and_share_the_parent_budget(client,monkeypatch):
    import threading,time
    pid,f,ids=seed(client,monkeypatch)
    for n in range(3):file(client,pid,f'第{n}份资料：论文必须列出来源。',role='rules')
    parent=tasks.enqueue(pid,auto.KIND,{'automatic':True,'budget_limit':5,'provider_profile':'primary'})
    p={**db.loads(parent['payload']),'review_existing_only':True};job_id=rule_pipeline.ensure_job(parent,p)
    lock=threading.Lock();state={'active':0,'peak':0};seen=[]
    def extract(child,payload):
        with lock:state['active']+=1;state['peak']=max(state['peak'],state['active']);seen.append((child,payload))
        time.sleep(.02)
        with lock:state['active']-=1
        return {'complete':True,'processed_source_units':len(payload['source_ids']),'unresolved_items':[]}
    monkeypatch.setattr(rule_pipeline,'extract_ai',extract)
    sources=rule_pipeline.source_scope(pid)
    result=auto.parallel_extract(parent,p,job_id,sources)
    assert result['complete'] and result['processed_source_units']==len(sources)
    assert 1<state['peak']<=3
    assert len({child['id'] for child,payload in seen})==4
    assert all(payload['rule_job_id']==job_id and payload['parent_task_id']==parent['id'] and not payload['auto_publish'] for child,payload in seen)
    assert all(not payload['review_existing_only'] for child,payload in seen)


def test_extraction_unit_ids_preserve_the_complete_literal_requirement_and_reject_unknown_ids():
    text='论文必须保留真实来源，不得编造实验结果。'
    chunk=[{'id':'s','text':text}]
    item={'source_id':'s','unit_id':'U1','modality':'recommended','scope':'global','category':'content'}
    good=rule_pipeline.validated({'text':db.dumps({'rules':[item]})},chunk,{'s':{'id':'s','text':text}})
    assert good[0][2]==text and good[0][3]==text
    with pytest.raises(rule_pipeline.RuleOutputError):rule_pipeline.validated({'text':db.dumps({'rules':[{**item,'unit_id':'U999'}]})},chunk,{'s':{'id':'s','text':text}})


def test_low_risk_reference_can_complete_without_promoting_uncertain_text_to_a_constraint(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['有完整的参考文献列表。'])
    before=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(before)
    auto.apply_rule(pid,before,source,{'purpose':'knowledge','quote':before['statement'],'reason':'此处介绍学术文献的一般特征，并非本项目的独立规范','confidence':.88},source['text'],'task','run','model')
    after=db.one('SELECT * FROM rules WHERE id=?',(ids[0],))
    assert after['state']=='reference_only' and after['statement']==before['statement']
    assert auto.receipt(pid,'rule',ids[0])['completed']


def test_broad_context_citation_can_be_narrowed_only_to_the_unchanged_complete_candidate(client,monkeypatch):
    pid,f,ids=seed(client,monkeypatch,['正文使用12pt字体。固定行距22pt。'])
    db.execute('UPDATE rules SET statement=? WHERE id=?',('固定行距22pt。',ids[0]))
    before=db.one('SELECT * FROM rules WHERE id=?',(ids[0],));source=auto.rule_source(before)
    auto.apply_rule(pid,before,source,{'purpose':'recommendation','quote':source['text'],'reason':'当前完整候选对应原文中的行距要求，其他字体信息是上下文','confidence':.92},source['text'],'task','run','model')
    saved=auto.receipt(pid,'rule',ids[0])
    assert saved['quote']=='固定行距22pt。' and saved['context_quote']==source['text']
    assert db.one('SELECT statement FROM rules WHERE id=?',(ids[0],))['statement']=='固定行距22pt。'
