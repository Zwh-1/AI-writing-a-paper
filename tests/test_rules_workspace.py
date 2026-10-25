"""Regression coverage for complete rule review and source-bound extraction."""
import pytest
from test_workbench import client,project,file,section,save
from app import db,rules,tasks,reviews,rule_ai

def add(c,p,**changes):
    response=c.post(f'/api/v1/projects/{p}/rules',json={'statement':'对研究内容进行核对',**changes})
    assert response.status_code==200,response.text
    return response.json()

def workspace(c,p):
    response=c.get(f'/api/v1/projects/{p}/rules');assert response.status_code==200,response.text
    return response.json()

@pytest.mark.parametrize('state',['candidate','confirmed','rejected'])
def test_new_rule_preserves_selected_state(client,state):
    p=project(client);r=add(client,p,state=state)
    assert r['state']==state
    w=workspace(client,p);assert w['readiness']['counts'][state]==1
    assert w['readiness']['can_publish']==(state=='confirmed')

@pytest.mark.parametrize('field,value',[('state','bad'),('modality','bad'),('category','bad'),('scope',''),('statement','   ')])
def test_rule_metadata_rejected_without_writes(client,field,value):
    p=project(client);response=client.post(f'/api/v1/projects/{p}/rules',json={'statement':'合法条款',field:value})
    assert response.status_code==422
    assert db.rows('SELECT * FROM rules')==[]

@pytest.mark.parametrize('checker,value',[('word_range',{'min':300,'max':500}),('required_terms',['方法','结果']),('required_sections',['abstract','conclusion']),('format_value',{'key':'font_size','expected':12}),('reference_recent',{'years':5,'minimum':0.5,'anchor_year':2026})])
def test_edit_keeps_structured_execution_and_conditions(client,checker,value):
    p=project(client);r=add(client,p,checker=checker,value=value)
    cond={'field':'paper_type','op':'eq','value':'software_system'}
    response=client.patch('/api/v1/rules/'+r['id'],json={'statement':'修订后的要求','checker':checker,'value':value,'operations':['revise'],'conditions':cond,'state':'candidate'})
    assert response.status_code==200,response.text
    saved=workspace(client,p)['rules'][0]
    assert db.loads(saved['value'])==value and db.loads(saved['operations'])==['revise']
    assert saved['conditions']==cond and saved['state']=='candidate'

def test_dirty_package_and_immutable_snapshot(client):
    p=project(client);r=add(client,p)
    package=rules.publish(p);assert not workspace(client,p)['readiness']['dirty']
    client.patch('/api/v1/rules/'+r['id'],json={'statement':'新的要求'})
    assert workspace(client,p)['readiness']['dirty']
    assert db.loads(db.one('SELECT rules FROM rule_packages WHERE id=?',(package['id'],))['rules'])[0]['statement']=='对研究内容进行核对'
    rules.publish(p);assert not workspace(client,p)['readiness']['dirty']

def test_publish_diagnostics_and_rejection_share_gate(client):
    p=project(client);f=file(client,p,'摘要必须说明研究方法。',role='rules');add(client,p)
    w=workspace(client,p);pending=w['readiness']['pending_pages']
    assert len(pending)==1 and pending[0]['file_id']==f['id']
    response=client.post(f'/api/v1/projects/{p}/rule-packages')
    assert response.status_code==422 and response.json()['detail']['code']=='RULE_COVERAGE_INCOMPLETE'
    client.patch('/api/v1/pages/'+pending[0]['id'],json={'review_state':'reviewed'})
    assert workspace(client,p)['readiness']['can_publish']

def test_rule_source_directly_returns_own_file_and_page(client):
    p=project(client);f=file(client,p,'摘要必须说明研究方法。',role='rules');rules.extract(p)
    r=db.one('SELECT * FROM rules WHERE project_id=?',(p,));response=client.get('/api/v1/rules/'+r['id']+'/source')
    assert response.status_code==200,response.text
    source=response.json();assert r['statement'] in source['source']['text']
    assert source['file']['id']==f['id'] and source['page']['page_number']==1

def test_excluded_source_does_not_silently_enter_package(client):
    p=project(client);f=file(client,p,'摘要必须说明研究方法。',role='rules');source=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    r=add(client,p,source_id=source['id'])
    client.patch('/api/v1/pages/'+source['page_id'],json={'review_state':'not_applicable','review_note':'不适用本论文'})
    assert workspace(client,p)['readiness']['blockers'][0]['code']=='RULE_SOURCE_INACTIVE'
    assert client.post(f'/api/v1/projects/{p}/rule-packages').status_code==422
    client.patch('/api/v1/rules/'+r['id'],json={'state':'rejected'});add(client,p,statement='另一个适用的手工条款')
    assert client.post(f'/api/v1/projects/{p}/rule-packages').status_code==200

def test_conflicts_resolve_and_compatible_word_bounds_are_allowed(client):
    p=project(client);add(client,p,checker='word_min',value=300);upper=add(client,p,checker='word_max',value=500)
    assert workspace(client,p)['conflicts']==[] and workspace(client,p)['readiness']['can_publish']
    client.patch('/api/v1/rules/'+upper['id'],json={'value':200})
    assert len(workspace(client,p)['conflicts'])==1
    assert client.post(f'/api/v1/projects/{p}/rule-packages').status_code==422
    client.patch('/api/v1/rules/'+upper['id'],json={'state':'rejected'})
    assert client.post(f'/api/v1/projects/{p}/rule-packages').status_code==200

def test_disjoint_operation_rules_do_not_block_publication(client):
    p=project(client);add(client,p,checker='reference_min',value=10,operations=['generate']);add(client,p,checker='reference_min',value=20,operations=['revise'])
    assert workspace(client,p)['conflicts']==[]

@pytest.mark.parametrize('value',[{'years':None,'minimum':0.5,'anchor_year':2026},{'years':5,'minimum':'oops','anchor_year':2026},{'years':5,'minimum':True,'anchor_year':2026}])
def test_bad_recent_reference_config_returns_validation_error(client,value):
    p=project(client);response=client.post(f'/api/v1/projects/{p}/rules',json={'statement':'文献要求','checker':'reference_recent','value':value})
    assert response.status_code==422

def test_batch_review_is_atomic_and_cannot_confirm_unread_rules(client):
    p=project(client);other=project(client);r=add(client,p);foreign=add(client,other)
    route=f'/api/v1/projects/{p}/rules/batch-review'
    assert client.post(route,json={'rule_ids':[r['id'],foreign['id']],'state':'rejected'}).status_code==422
    assert db.one('SELECT state FROM rules WHERE id=?',(r['id'],))['state']=='confirmed'
    assert client.post(route,json={'rule_ids':[r['id']],'state':'confirmed'}).status_code==422
    for state in ('rejected','candidate'):
        result=client.post(route,json={'rule_ids':[r['id']],'state':state});assert result.status_code==200
        assert db.one('SELECT state FROM rules WHERE id=?',(r['id'],))['state']==state

def ai_fixture(c,p):
    f=file(c,p,'第一条要求必须准确。',role='rules');source=db.one('SELECT * FROM sources WHERE file_id=?',(f['id'],))
    for text in ('第二条要求必须可检验。','第三条要求必须有出处。'):
        sid=db.uid();db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)',(sid,f['id'],source['page_id'],db.dumps({'kind':'paragraph','page':1}),text,text,'rule_candidate','native',0))
    return f


def test_manual_confirmation_preserves_each_selected_rule_and_does_not_publish(client):
    p=project(client)
    chosen=[add(client,p,state='candidate',modality='recommended',scope='abstract',checker='word_range',value={'min':300,'max':500},decision_note='保留原采用依据'),
            add(client,p,state='candidate',modality='mandatory',checker='reference_min',value=20)]
    untouched=add(client,p,state='candidate',modality='recommended')
    for before in chosen:
        response=client.patch('/api/v1/rules/'+before['id'],json={'state':'confirmed'})
        assert response.status_code==200,response.text
        after=response.json()
        assert after['state']=='confirmed'
        assert all(after[key]==value for key,value in before.items() if key!='state')
    assert db.one('SELECT state FROM rules WHERE id=?',(untouched['id'],))['state']=='candidate'
    assert workspace(client,p)['package'] is None
    assert not db.rows('SELECT * FROM generation_runs')


def test_manual_confirmation_does_not_promote_pending_or_examples_to_publishable_rules(client):
    p=project(client)
    for modality in ('pending','example','counterexample'):
        rule=add(client,p,state='candidate',modality=modality)
        result=client.patch('/api/v1/rules/'+rule['id'],json={'state':'confirmed'})
        assert result.status_code==200,result.text
        assert result.json()['modality']==modality
    assert workspace(client,p)['readiness']['publishable_count']==0

def test_ai_batches_have_stable_disjoint_sources_and_deduplicate_queue(client):
    p=project(client);f=ai_fixture(client,p);route=f'/api/v1/projects/{p}/rules/ai-extract'
    assert client.get(f'/api/v1/projects/{p}/rules/ai-sources?file_id='+f['id']).json()['total']==3
    first=client.post(route,json={'file_ids':[f['id']],'source_limit':1,'source_offset':0});assert first.status_code==200,first.text
    repeated=client.post(route,json={'file_ids':[f['id']],'source_limit':1,'source_offset':0});assert repeated.json()['id']==first.json()['id']
    second=client.post(route,json={'file_ids':[f['id']],'source_limit':1,'source_offset':1});assert second.status_code==200
    assert not set(db.loads(first.json()['payload'])['source_ids']) & set(db.loads(second.json()['payload'])['source_ids'])

@pytest.mark.parametrize('changes',[{'source_limit':'x'},{'source_offset':-1},{'source_limit':0},{'source_offset':True},{'source_offset':999}])
def test_ai_invalid_batch_parameters_do_not_enqueue(client,changes):
    p=project(client);f=ai_fixture(client,p)
    response=client.post(f'/api/v1/projects/{p}/rules/ai-extract',json={'file_ids':[f['id']],**changes})
    assert response.status_code==422
    assert not db.rows("SELECT * FROM tasks WHERE kind='rules_ai'")

def test_ai_local_only_rejected_before_enqueue(client):
    p=project(client);f=ai_fixture(client,p);client.patch('/api/v1/files/'+f['id'],json={'egress':'local_only'})
    assert client.post(f'/api/v1/projects/{p}/rules/ai-extract',json={'file_ids':[f['id']]}).status_code==422
    assert not db.rows("SELECT * FROM tasks WHERE kind='rules_ai'")

def test_ai_continuation_and_quote_validation_without_paid_calls(client,monkeypatch):
    p=project(client);f=ai_fixture(client,p);seen=[]
    def fake(task,system,user,manifest,**kwargs):
        source=db.loads(user)['sources'][0];seen.append(source['source_id'])
        return {'text':db.dumps({'rules':[{'source_id':source['source_id'],'quote':source['text'],'statement':source['text'],'modality':'mandatory','scope':'global','category':'content'}]})}
    monkeypatch.setattr(tasks,'model_call',fake)
    for offset in range(3):
        t=client.post(f'/api/v1/projects/{p}/rules/ai-extract',json={'file_ids':[f['id']],'source_limit':1,'source_offset':offset}).json()
        output=rule_ai.extract_ai(t,db.loads(t['payload']));assert output['added']==1
        assert output['next_offset']==(offset+1 if offset<2 else None)
        db.execute("UPDATE tasks SET status='succeeded' WHERE id=?",(t['id'],))
    assert len(set(seen))==3 and len(db.rows('SELECT * FROM rules'))==3
    assert all(r['state']=='candidate' for r in db.rows('SELECT * FROM rules'))
    assert not db.rows('SELECT * FROM generation_runs')

def test_execution_partial_update_preserves_existing_conditions(client):
    p=project(client);cond={'field':'paper_type','op':'eq','value':'software_system'}
    r=add(client,p,conditions=cond)
    response=client.put('/api/v1/rules/'+r['id']+'/execution',json={'checker':'word_min','value':100})
    assert response.status_code==200,response.text
    saved=workspace(client,p)['rules'][0]
    assert saved['conditions']==cond and saved['rule_key']=='word_count'
