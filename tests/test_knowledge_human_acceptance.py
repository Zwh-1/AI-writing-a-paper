import json

import pytest
from fastapi import HTTPException

from test_workbench import client,project,file
from app import db,knowledge_units,tasks
from app import knowledge_comparisons as comparisons,knowledge_acceptance as acceptance


def study(client,count=1,generated=True):
    pid=project(client);original=file(client,pid,'方法仅适用于单用户环境，并发场景尚未验证。')
    knowledge_units.build(pid,original['id']);uid=db.one('SELECT id FROM knowledge_units')['id']
    categories=[category for category,n,_ in comparisons.CATEGORIES for _ in range(n)]
    cases=[];results=[]
    for i in range(count):
        cid=db.uid();question=f'合成测试任务{i+1}：核对单用户条件与未验证范围。'
        db.execute('INSERT INTO knowledge_evaluation_cases VALUES(?,?,?,?,?,?,?)',(cid,pid,categories[i%80],question,'{}','unlabelled',db.now()))
        comparisons.update_case(pid,cid,{'question':question,'state':'labelled','reviewer':'合成标注人','compared_original':True,
            'real_task_confirmed':True,'gold':{'unit_ids':[uid]}})
        cases.append(db.one('SELECT * FROM knowledge_evaluation_cases WHERE id=?',(cid,)))
        answer={'id':'synthetic-artifact','run_id':'synthetic-private-run','manifest':{'variant':'D','provider':'synthetic-provider'},
                'answer':{'blocks':[{'kind':'limitation','text':'仅适用于单用户；并发场景未验证。','sources':[]}],'gaps':[]}} if generated else None
        results.append({'case_id':cid,'category':categories[i%80],'question':question,'gold_state':'labelled',
            'variants':{'D':{'unit_ids':[uid],'evidence_ids':[],'recall':1.,'mrr':1.,'elapsed_seconds':1.,
                'answer':answer,'human_semantic_review':None}}})
    task=tasks.enqueue(pid,'knowledge_compare',{});identity=db.uid()
    manifest={'generation':'model_candidates' if generated else 'retrieval_only','blind_order':{'组4':'D'},
              'cases_snapshot':cases,'input_snapshot':comparisons.input_snapshot(pid)}
    db.execute('INSERT INTO knowledge_comparisons VALUES(?,?,?,?,?,?,?)',(identity,pid,task['id'],db.dumps(manifest),db.dumps(results),'awaiting_human_review',db.now()))
    return pid,identity,cases,uid


def review_body(case,**extra):
    return {'case_id':case['id'],'blind_group':'组4','reviewer':'合成独立评审','compared_original':True,
        'note':'合成测试中逐句对照原件，保留条件与否定。','supported_claims':19,'checkable_claims':20,
        'qualifiers_preserved':4,'qualifiers_required':4,'wrong_citations':0,'unsupported_expansions':0,**extra}


def test_label_requires_real_task_and_original_attestations(client):
    pid=project(client);source=file(client,pid);knowledge_units.build(pid,source['id']);uid=db.one('SELECT id FROM knowledge_units')['id']
    cid=db.uid();db.execute('INSERT INTO knowledge_evaluation_cases VALUES(?,?,?,?,?,?,?)',(cid,pid,'locating','合成问题','{}','unlabelled',db.now()))
    body={'state':'labelled','reviewer':'合成标注','gold':{'unit_ids':[uid]}}
    for extra in ({},{'compared_original':True},{'real_task_confirmed':True}):
        with pytest.raises(HTTPException):comparisons.update_case(pid,cid,{**body,**extra})
    assert db.one('SELECT state FROM knowledge_evaluation_cases WHERE id=?',(cid,))['state']=='unlabelled'
    comparisons.update_case(pid,cid,{**body,'compared_original':True,'real_task_confirmed':True})
    gold=db.loads(db.one('SELECT gold_json FROM knowledge_evaluation_cases WHERE id=?',(cid,))['gold_json'])
    assert gold['source_refs'][0]['id']==uid and comparisons.gold_current(pid,gold)


def test_blind_packet_and_review_response_hide_implementation_metadata(client):
    pid,cid,cases,_=study(client)
    packet=comparisons.blind(pid,cid);encoded=json.dumps(packet)
    assert 'synthetic-private-run' not in encoded and 'synthetic-provider' not in encoded and 'manifest' not in encoded
    assert packet['cases'][0]['groups'][0]['answer']['blocks']
    saved=comparisons.review(pid,cid,review_body(cases[0]));encoded=json.dumps(saved)
    assert saved['blinded'] and 'variants' not in saved and '"D"' not in encoded
    listing=client.get(f'/api/v1/projects/{pid}/knowledge/evaluation/comparisons').json()
    assert listing[0]['id']==cid and 'manifest_json' not in listing[0] and 'result' not in listing[0]


def test_annotator_cannot_be_independent_blind_reviewer(client):
    pid,cid,cases,_=study(client)
    with pytest.raises(HTTPException) as error:comparisons.review(pid,cid,review_body(cases[0],reviewer=' 合成标注人 '))
    assert error.value.detail['code']=='INDEPENDENT_REVIEWER_REQUIRED'
    assert not db.rows('SELECT * FROM knowledge_review_events')


def test_revealed_mapping_cannot_produce_new_blind_reviews(client):
    pid,cid,cases,_=study(client);comparisons.blind(pid,cid,reveal=True)
    with pytest.raises(HTTPException) as error:comparisons.review(pid,cid,review_body(cases[0]))
    assert error.value.detail['code']=='BLIND_ALREADY_REVEALED'


def test_pilot_rate_does_not_equal_80_case_acceptance(client):
    pid,cid,cases,_=study(client);comparisons.review(pid,cid,review_body(cases[0]))
    report=comparisons.report(pid,cid)
    assert report['groups']['D']['support']['rate']==.95
    assert not report['groups']['D']['human_acceptance_complete'] and not report['semantic_quality_verified']


def test_retrieval_only_has_no_semantic_accuracy_acceptance(client):
    pid,cid,cases,_=study(client,80,generated=False)
    for case in cases:comparisons.review(pid,cid,review_body(case))
    report=comparisons.report(pid,cid)
    assert report['groups']['D']['independent_blind_cases']==80
    assert report['groups']['D']['generated_answer_cases']==0 and not report['semantic_quality_verified']


def test_80_current_labels_and_independent_blind_reviews_are_required(client):
    pid,cid,cases,_=study(client,80)
    for case in cases:comparisons.review(pid,cid,review_body(case))
    report=comparisons.report(pid,cid)
    assert report['groups']['D']['category_coverage_complete'] and report['semantic_quality_verified']
    # Historical blinded reviews remain valid when mapping is revealed later.
    comparisons.blind(pid,cid,reveal=True)
    assert comparisons.report(pid,cid)['semantic_quality_verified']
    db.execute('UPDATE knowledge_evaluation_cases SET question=? WHERE id=?',('已修改的问题',cases[0]['id']))
    assert not comparisons.report(pid,cid)['semantic_quality_verified']


def test_open_review_can_keep_metrics_but_never_count_as_independent(client):
    pid,cid,cases,_=study(client)
    body=review_body(cases[0]);body.pop('blind_group');body['variant']='D'
    comparisons.review(pid,cid,body)
    report=comparisons.report(pid,cid)
    assert report['groups']['D']['reviewed_cases']==1 and report['groups']['D']['independent_blind_cases']==0
    assert comparisons.blind(pid,cid)['revealed']
    with pytest.raises(HTTPException) as error:comparisons.review(pid,cid,review_body(cases[0]))
    assert error.value.detail['code']=='BLIND_ALREADY_REVEALED'


def test_zero_denominator_reviews_cannot_pad_80_real_task_acceptance(client):
    pid,cid,cases,_=study(client,80)
    for i,case in enumerate(cases):
        comparisons.review(pid,cid,review_body(case,**({'supported_claims':0,'checkable_claims':0} if i else {})))
    report=comparisons.report(pid,cid)
    assert report['groups']['D']['reviewed_cases']==80
    assert report['groups']['D']['measurable_review_cases']==1
    assert report['groups']['D']['support']['rate']==.95
    assert not report['semantic_quality_verified']


def test_annotation_suggestion_matches_question_and_never_uses_unrelated_paper(client):
    pid=project(client);source=file(client,pid,'单用户条件下测试，未检查并发。'*12);knowledge_units.build(pid,source['id'])
    other=file(client,pid,'独立论文讨论区块链性能。'*12);knowledge_units.build(pid,other['id'])
    for category,name in [('locating',source['name']),('locating','不存在的论文.pdf'),('project',source['name'])]:
        db.execute('INSERT INTO knowledge_evaluation_cases VALUES(?,?,?,?,?,?,?)',(db.uid(),pid,category,'围绕《'+name+'》核对真实问题','{}','unlabelled',db.now()))
    packet=acceptance.annotation_packet(pid)
    linked=[c for c in packet['cases'] if c['source_suggestion']]
    assert len(linked)==1 and linked[0]['source_suggestion']['file_id']==source['id']
    assert all(c['preparation_gap'] for c in packet['cases'] if not c['source_suggestion'])
    assert not packet['human_acceptance'] and not db.rows('SELECT * FROM knowledge_review_events')


def test_empty_project_has_no_real_paper_acceptance_target(client):
    pid=project(client);status=acceptance.status(pid)
    assert not status['real_paper_readiness']['has_acceptance_target']
    assert not status['all_complete'] and not status['verified_comparison_ids']
    response=client.post(f'/api/v1/projects/{pid}/knowledge/real-paper/acceptance',json={
        'expected_snapshot':status['real_paper_readiness']['snapshot'],'reviewer':'合成评审','note':'不能用于真实验收',
        'compared_original':True,'real_paper_confirmed':True,'result':'accepted'})
    assert response.status_code==422 and not db.rows("SELECT * FROM audit WHERE action='knowledge_real_paper_acceptance'")


@pytest.mark.parametrize('change',['body','citation','source'])
def test_real_paper_review_is_explicit_and_invalidates_on_content_change(client,change):
    from test_workbench import section,save
    pid=project(client);original=file(client,pid,'仅适用于单用户环境，未验证并发。');sid=section(client,pid)
    save(client,sid,'合成测试正文：研究仅验证单用户环境。')
    revision=db.one('SELECT current_revision_id FROM sections WHERE id=?',(sid,))['current_revision_id'];reference=db.uid();citation=db.uid()
    db.execute('INSERT INTO bibliography(id,project_id,metadata,file_id,created_at) VALUES(?,?,?,?,?)',(reference,pid,'{"title":"合成文献"}',original['id'],db.now()))
    source=db.one('SELECT id FROM sources WHERE file_id=?',(original['id'],))['id']
    db.execute('INSERT INTO citations(id,revision_id,node_id,reference_id,reference_snapshot,source_ids) VALUES(?,?,?,?,?,?)',
        (citation,revision,'synthetic-node',reference,'{"title":"合成文献"}',db.dumps([source])))
    state=acceptance.real_paper_status(pid)
    assert state['has_acceptance_target'] and not state['human_quality_accepted']
    body={'expected_snapshot':state['snapshot'],'reviewer':'合成实际评审','note':'合成夹具：确认正文与原件，非真实项目验收',
        'compared_original':True,'real_paper_confirmed':True,'result':'accepted'}
    assert acceptance.review_real_paper(pid,body)['human_quality_accepted']
    if change=='body':db.execute('UPDATE revisions SET text=text||? WHERE id=?',('已修订',revision))
    elif change=='citation':db.execute('UPDATE citations SET excerpt=? WHERE id=?',('已修改引用',citation))
    else:db.execute('UPDATE sources SET text=text||? WHERE id=?',('原文修订',source))
    assert not acceptance.real_paper_status(pid)['human_quality_accepted']
    with pytest.raises(HTTPException) as error:acceptance.review_real_paper(pid,body)
    assert error.value.detail['code']=='REAL_PAPER_CHANGED'
