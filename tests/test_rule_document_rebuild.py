import json
import pytest
from test_workbench import client,project,file
from test_document_reading import synthetic_slides
from app import db,parsers,provider,tasks,rules,rule_workspace,rule_rebuild as rebuild,knowledge_units as units,model_install


@pytest.fixture(autouse=True)
def local_models(monkeypatch):
    original=model_install.status
    monkeypatch.setattr(model_install,'status',lambda name:{**original(name),'ready':False})


def fake_model(monkeypatch,unknown=False):
    calls=[]
    def call(task,system,user,manifest,**kwargs):
        data=json.loads(user[user.find('{'):]);calls.append(data)
        if unknown:raise provider.ProviderError('request unknown',unknown=True)
        items=[]
        for unit in data['units']:
            text=unit['text']
            quote='摘要必须说明研究方法。' if '摘要必须说明研究方法。' in text else text.strip()
            items.append({'unit_id':unit['id'],'quote':quote,'purpose':'recommendation','scope':'abstract','conditions':[],'reason':'依据完整原文中的明确表述。','confidence':.99})
        return {'text':db.dumps({'covered_unit_ids':data['target_unit_ids'],'needs_context':[],'items':items}),'run_id':None}
    monkeypatch.setattr(tasks,'model_call',call)
    return calls


def run(pid,**extra):
    task=tasks.enqueue(pid,'rule_document_rebuild',{'budget_limit':3,'auto_publish':True,**extra})
    return rebuild.process(task,db.loads(task['payload']))


def test_full_rebuild_retires_old_catalog_but_keeps_immutable_package(client,monkeypatch):
    pid=project(client);original=file(client,pid,'摘要必须说明研究方法。',role='rules');oldid=db.uid()
    source=db.one('SELECT * FROM sources WHERE file_id=?',(original['id'],))
    db.execute('INSERT INTO rules(id,project_id,source_id,statement,modality,state,created_at) VALUES(?,?,?,?,?,?,?)',(oldid,pid,source['id'],'旧版截断条款','recommended','reference_only',db.now()))
    calls=fake_model(monkeypatch);result=run(pid)
    assert result['complete'] and result['archived_rule_count']==1 and result['rule_package']['version']==1
    visible=rule_workspace.workspace(pid)['rules'];assert len(visible)==1 and visible[0]['id']!=oldid
    assert db.one('SELECT statement FROM rules WHERE id=?',(oldid,))['statement']=='旧版截断条款'
    assert calls and all('directory' in c and c['target_unit_ids'] for c in calls)
    head=db.one('SELECT p.* FROM knowledge_heads h JOIN knowledge_parses p ON p.id=h.parse_id WHERE h.file_id=?',(original['id'],))
    assert units.parse_current(head), 'Derived rule context must not invalidate original text'
    assert rule_workspace.diagnostics(pid)['can_publish']


def test_failed_batch_keeps_previous_rules_and_does_not_claim_success(client,monkeypatch):
    pid=project(client);file(client,pid,'摘要必须说明研究方法。',role='rules')
    rid=client.post(f'/api/v1/projects/{pid}/rules',json={'statement':'旧的人工要求','modality':'mandatory','state':'confirmed'}).json()['id']
    calls=fake_model(monkeypatch,unknown=True);result=run(pid)
    assert not result['complete'] and result['unresolved_batches']
    assert db.one('SELECT id FROM rules WHERE id=?',(rid,))
    assert not db.setting('retired_rule_ids:'+pid,[])
    assert not db.rows('SELECT * FROM rule_packages')
    assert len(calls)==1


def test_teaching_examples_stay_with_full_slide_and_cannot_become_norms(client,monkeypatch):
    pid=project(client);original,_=parsers.register_file(pid,'摘要课件.pptx',synthetic_slides(),role='rules');parsers.parse_file(original['id'])
    calls=fake_model(monkeypatch);result=run(pid)
    assert result['complete']
    assert '正确示例' in calls[0]['units'][0]['text'] and '错误示例' in calls[0]['units'][0]['text']
    assert all(row['state']!='machine_verified' for row in rule_workspace.entries(pid))


def test_explicit_context_and_full_unit_coverage_are_required():
    batch={'target_unit_ids':['U1'],'units':[{'id':'U1','text':'摘要必须说明研究方法。'}]}
    with pytest.raises(ValueError):rebuild.validate({'covered_unit_ids':[],'items':[]},batch)
    result,_=rebuild.validate({'covered_unit_ids':['U1'],'items':[{'unit_id':'U1','quote':'杜撰的话','purpose':'requirement','reason':'明确规范依据','conditions':[],'confidence':.99}]},batch)
    assert result[0]['purpose']=='uncertain' and result[0]['quote']==batch['units'][0]['text'] and result[0]['confidence']==0
    assert rebuild.validate({'needs_context':['U99'],'items':[]},batch)==([],['U99'])


def test_model_condition_interpretation_does_not_become_verified_constraint():
    batch={'target_unit_ids':['U1'],'units':[{'id':'U1','text':'摘要必须\n说明研究方法。'}]}
    result,_=rebuild.validate({'covered_unit_ids':['U1'],'items':[{'unit_id':'U1','quote':'摘要必须说明研究方法。','purpose':'requirement','reason':'完整原文中的规范','conditions':['所有类型论文都必须如此'],'confidence':.99}]},batch)
    assert result[0]['quote']=='摘要必须\n说明研究方法。'
    assert result[0]['conditions']==[] and result[0]['condition_notes']==['所有类型论文都必须如此']
    assert result[0]['purpose']=='conditional'


@pytest.mark.parametrize('bad_text',['{"covered_unit_ids":[','{"covered_unit_ids":[],"items":[invalid]}'])
def test_invalid_model_output_retries_complete_pages_without_rebilling_completed_response(client,monkeypatch,bad_text):
    from app import ai_autopilot,security
    pid=project(client);original=file(client,pid,'摘要必须说明研究方法。\n\n参考文献至少20篇。',role='rules')
    cfg={**provider.config(),'max_output_tokens':2048}
    plans,documents=rebuild.plan(pid,cfg);batch=plans[0];task=tasks.enqueue(pid,'rule_document_rebuild',{'budget_limit':3})
    calls=[]
    def fake(task,system,user,manifest,**kwargs):
        body=json.loads(user[user.find('{'):]);calls.append(body['target_unit_ids'])
        if len(body['target_unit_ids'])>1:
            return {'text':bad_text}
        return {'text':db.dumps({'covered_unit_ids':body['target_unit_ids'],'items':[],'needs_context':[]})}
    monkeypatch.setattr(tasks,'model_call',fake)
    result=rebuild.request_batch(task,task['id'],batch,documents[original['id']],cfg,task['id'])
    assert result['complete'] and result['split_complete_original_units']
    assert len(calls)==3 and all(len(c)==1 for c in calls[1:])
    assert rebuild.request_batch(task,task['id'],batch,documents[original['id']],cfg,task['id'])==result
    assert len(calls)==3


def test_restart_does_not_reuse_old_sentence_extraction_checkpoints(client,monkeypatch):
    pid=project(client);original=file(client,pid,'摘要必须说明研究方法。',role='rules')
    db.set_setting('retired_rule_ids:'+pid,[])
    calls=fake_model(monkeypatch);task=tasks.enqueue(pid,'rule_document_rebuild',{'budget_limit':3})
    result=rebuild.process(task,db.loads(task['payload']));assert result['complete']
    before=len(calls)
    assert rebuild.process(task,db.loads(task['payload']))['complete']
    assert len(calls)==before


def test_new_catalog_does_not_hide_manual_rules(client,monkeypatch):
    pid=project(client);file(client,pid,'摘要必须说明研究方法。',role='rules')
    rid=client.post(f'/api/v1/projects/{pid}/rules',json={'statement':'作者新增说明','modality':'recommended'}).json()['id']
    fake_model(monkeypatch);run(pid)
    assert rid in {r['id'] for r in rule_workspace.entries(pid)}


def test_grading_document_does_not_become_unconditional_requirement(client,monkeypatch):
    from app import material_policy
    pid=project(client);original=file(client,pid,'A级标准：摘要必须说明研究方法。',role='rules')
    material_policy.set_usages(pid,original['id'],{'usages':['writing_guidance','grading'],'source_kind':'grading_criteria','note':'合成测试评分条件'})
    fake_model(monkeypatch);result=run(pid)
    assert result['complete']
    assert all(row['state']!='machine_verified' for row in rule_workspace.entries(pid))


def test_unresolved_meaning_keeps_whole_original_as_unverified_candidate(client,monkeypatch):
    pid=project(client);original=file(client,pid,'摘要必须说明研究方法。',role='rules')
    def fake(task,system,user,manifest,**kwargs):
        body=json.loads(user[user.find('{'):])
        return {'text':db.dumps({'items':[],'needs_context':body['target_unit_ids']})}
    monkeypatch.setattr(tasks,'model_call',fake)
    result=run(pid)
    assert result['complete']
    current=rule_workspace.entries(pid)
    assert len(current)==1 and current[0]['state']=='candidate' and current[0]['modality']=='pending'
    assert current[0]['statement']=='摘要必须说明研究方法。'
    assert '保留完整原页待核对' in current[0]['decision_note']


def test_context_limit_does_not_mark_an_unsent_range_as_read(client,monkeypatch):
    pid=project(client);original=file(client,pid,'问题原文。\n\n第一处条件。\n\n第二处条件。\n\n第三处条件。',role='rules')
    cfg={**provider.config(),'max_output_tokens':2048};plans,docs=rebuild.plan(pid,cfg);values=docs[original['id']]
    batch={**plans[0],'target_unit_ids':[values[0]['id']],'units':[values[0]]};calls=[]
    def fake(task,system,user,manifest,**kwargs):
        sent=json.loads(user[user.find('{'):]);calls.append(sent)
        return {'text':db.dumps({'items':[],'needs_context':[values[len(calls)]['id']]})}
    monkeypatch.setattr(tasks,'model_call',fake)
    task=tasks.enqueue(pid,'rule_document_rebuild',{'budget_limit':3})
    observed=rebuild.request_batch(task,task['id'],batch,values,cfg,task['id'])
    assert observed['semantic_unresolved'] and len(calls)==3
    assert {u['id'] for u in observed['units']}=={u['id'] for u in calls[-1]['units']}
    assert values[-1]['id'] not in {u['id'] for u in observed['units']}
