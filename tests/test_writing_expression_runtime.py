"""Runtime behavior for continuous prose review and task-bound continuations."""
from copy import deepcopy

import pytest

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project, response, write
from test_writing_flow_safety import _basis, _running
from app import db, provider, tasks, writing_flow


def candidate():
    return [{'text':'模块由接口层统一调用。'}, {'text':'这些权限因此显著提高性能。'}]


@pytest.mark.parametrize('index,excerpt',[(True,'模块'),(-1,'模块'),(2,'模块'),(0,'未出现的原句')])
def test_discourse_rejects_invented_locations(index,excerpt):
    with pytest.raises(ValueError):
        writing_flow._parse_discourse_response(db.dumps({'coverage_complete':True,'findings':[
            {'kind':'coherence','paragraph_index':index,'excerpt':excerpt,'message':'实际问题'}]}),candidate())


def test_discourse_preserves_exact_locations_and_does_not_grant_fact_support():
    review=writing_flow._parse_discourse_response(db.dumps({'coverage_complete':True,'findings':[
        {'kind':'argument','paragraph_index':1,'excerpt':'这些权限因此显著提高性能。',
         'related_paragraph_indices':[0],'message':'前文没有建立权限与性能的关系。'}]}),candidate())
    finding=review['findings'][0]
    assert finding['paragraph_index']==1 and finding['related_paragraph_indices']==[0]
    assert finding['repair_query']==candidate()[1]['text']
    assert 'support' not in finding


def test_discourse_runs_without_source_claims_and_retains_full_candidate(writing_project,monkeypatch):
    task=_running(writing_project);context,manifest=_basis(writing_project)
    seen=[]
    def transport(system,user,images=None):
        payload=db.loads(user);seen.append(payload)
        assert payload['operation']=='review_candidate_discourse'
        assert payload['rules']==context.get('rules',[])
        assert [row['text'] for row in payload['candidate_paragraphs']]==[p['text'] for p in candidate()]
        return response({'coverage_complete':True,'findings':[],'summary':'给定连续候选。'})
    monkeypatch.setattr(provider,'generate',transport)
    review=writing_flow._discourse(task,{'id':writing_project['sid']},candidate(),context,manifest)
    assert review['status']=='checked' and review['coverage_complete']
    assert len(seen)==1 and review['human_review']=='pending'
    assert not review.get('global_coherence')


def test_discourse_cache_reuses_exact_candidate_and_rechecks_source_permissions(writing_project,monkeypatch):
    from fastapi import HTTPException
    task=_running(writing_project);context,manifest=_basis(writing_project);calls=[]
    context['rules']='本小节保留必要的步骤顺序。'
    def transport(system,user,images=None):
        calls.append(db.loads(user))
        return response({'coverage_complete':True,'findings':[]})
    monkeypatch.setattr(provider,'generate',transport)
    first=writing_flow._discourse(task,{},candidate(),context,manifest)
    second=writing_flow._discourse(task,{},candidate(),context,manifest)
    assert len(calls)==1 and first['run_id']==second['run_id']
    updated=candidate();updated[1]['text']='随后解释权限的具体约束。'
    third=writing_flow._discourse(task,{},updated,context,manifest)
    assert len(calls)==2 and third['candidate_text_hash']!=first['candidate_text_hash']
    writing_project['client'].patch('/api/v1/files/'+writing_project['file']['id'],json={'egress':'local_only'})
    with pytest.raises((HTTPException,ValueError)):
        writing_flow._discourse(task,{},candidate(),context,manifest)
    assert len(calls)==2


def test_discourse_budget_stop_keeps_candidate_and_does_not_send(writing_project,monkeypatch):
    task=_running(writing_project);context,manifest=_basis(writing_project)
    paragraphs=candidate();before=deepcopy(paragraphs)
    monkeypatch.setattr(writing_flow,'_stage_fits',lambda *args:False)
    monkeypatch.setattr(provider,'generate',lambda *args:pytest.fail('Oversize review must not send'))
    result=writing_flow._discourse(task,{'id':writing_project['sid']},paragraphs,context,manifest)
    assert result['status']=='incomplete' and not result['coverage_complete']
    assert result['findings'][0]['repairable'] is False
    assert paragraphs==before


def test_invalid_discourse_reply_stays_unassessed_without_rewriting(writing_project,monkeypatch):
    task=_running(writing_project);context,manifest=_basis(writing_project)
    monkeypatch.setattr(provider,'generate',lambda *args:response({'coverage_complete':True,'findings':[
        {'paragraph_index':7,'kind':'style','excerpt':'不存在','message':'猜测'}]}))
    result=writing_flow._discourse(task,{'id':writing_project['sid']},candidate(),context,manifest)
    assert not result['coverage_complete'] and result['status']=='incomplete'
    assert writing_flow._repairable(result['findings'])==[]


def test_discourse_resolves_preserved_blocks_and_keeps_them_read_only(writing_project,monkeypatch):
    task=_running(writing_project);context,manifest=_basis(writing_project)
    context['existing_blocks']=[{'node_id':'locked','text':'保留段落讨论授权。','protected':True}]
    paragraphs=[{'preserve_node_id':'locked'},{'text':'后文承接授权条件。'}]
    def transport(system,user,images=None):
        first=db.loads(user)['candidate_paragraphs'][0]
        assert first['text']=='保留段落讨论授权。' and first['read_only']
        return response({'coverage_complete':True,'findings':[{'paragraph_index':0,'kind':'style',
            'excerpt':'保留段落讨论授权。','message':'保留段的语言疑点只可报告。'}]})
    monkeypatch.setattr(provider,'generate',transport)
    result=writing_flow._discourse(task,{},paragraphs,context,manifest)
    assert result['findings'][0]['repairable'] is False
    assert writing_flow._repairable(result['findings'])==[]
    context['existing_blocks']=[]
    monkeypatch.setattr(provider,'generate',lambda *args:pytest.fail('Missing retained text must not send'))
    missing=writing_flow._discourse(task,{},paragraphs,context,manifest)
    assert not missing['coverage_complete'] and missing['unresolved_paragraph_indices']==[0]


def test_sparse_cards_assign_a_real_task_to_every_continuation():
    section={'id':'s','target_words':5000}
    context={'writing_brief':{'points':[{'id':'p1'},{'id':'p2'}],
             'argument_checkpoints':[{'point_id':'p1'},{'point_id':'p2'}]}}
    cards=[{'id':'a1','point_ids':['p1'],'question':'授权条件'},
           {'id':'a2','point_ids':['p2'],'question':'状态读取'}]
    before=deepcopy(context);assignments=[]
    for index in range(5):
        unit=writing_flow._unit_request_context(section,context,cards,index,5,[{'text':'已完成的分析。'}])
        assigned=unit['argument_unit']['paragraph_cards'];assert len(assigned)==1
        assignments.append(assigned[0]['id'])
        continuation=unit['argument_unit']['continuation']
        assert 1<=continuation['index']<=continuation['total']
        assert [row['point_id'] for row in unit['writing_brief']['argument_checkpoints']]==assigned[0]['point_ids']
    assert assignments==['a1','a1','a1','a2','a2']
    assert context==before


def test_located_expression_finding_reaches_revision_and_is_reviewed_again(writing_project,monkeypatch):
    e=writing_project;reviews=[]
    def transport(system,user,images=None):
        payload=db.loads(user)
        if payload.get('operation')=='review_candidate_discourse':
            reviews.append(payload)
            rule_reviews=[{'rule_id':r['id'],'status':'satisfied','paragraph_indices':[0],
                          'quote':payload['candidate_paragraphs'][0]['text'],'reason':'保留合成规则的边界。'}
                          for r in payload.get('rule_applications',[])]
            if len(reviews)==1:
                return response({'coverage_complete':True,'rule_reviews':rule_reviews,'findings':[{'kind':'coherence','paragraph_index':1,
                    'excerpt':'上述讨论具有重要意义。','message':'第二段只是笼统评价，需说明它承接的具体对象。'}]})
            return response({'coverage_complete':True,'rule_reviews':rule_reviews,'findings':[]})
        result=e['transport'](system,user,images)
        if 'claim_reviews' in system:return result
        data=db.loads(result['text'])
        data['paragraphs'].append({'text':'本节接着讨论接口的授权条件。' if payload.get('candidate_to_repair') else '上述讨论具有重要意义。'})
        return response(data)
    monkeypatch.setattr(provider,'generate',transport)
    _,result=write(e)
    assert len(reviews)==2
    assert result['writing_quality']['repair_rounds'][0]['accepted']
    assert result['writing_quality']['discourse']['coverage_complete']
    assert '本节接着讨论接口的授权条件。' in result['text']
    assert '上述讨论具有重要意义。' not in result['text']


def test_disabled_semantic_review_does_not_make_a_discourse_request(writing_project,monkeypatch):
    writing_flow.save_settings(writing_project['pid'],{'semantic_review':False})
    monkeypatch.setattr(provider,'generate',lambda *args:pytest.fail('Disabled review must not send'))
    result=writing_flow._discourse({'project_id':writing_project['pid']},{},candidate(),{}, {})
    assert result['status']=='not_checked' and not result['coverage_complete']


def test_style_repair_cannot_trade_fewer_style_findings_for_lost_source_support(writing_project,monkeypatch):
    e=writing_project;count=0
    def transport(system,user,images=None):
        nonlocal count
        payload=db.loads(user)
        if payload.get('operation')=='review_candidate_discourse':
            return response({'coverage_complete':True,'findings':([] if count>1 else [
                {'paragraph_index':0,'kind':'style','excerpt':payload['candidate_paragraphs'][0]['text'],
                 'message':'需要澄清表述'+str(index)} for index in range(3)])})
        result=e['transport'](system,user,images)
        if 'claim_reviews' in system:
            count+=1
            if count>1:
                data=db.loads(result['text'])
                for claim in data['claim_reviews']:claim['support']='unknown'
                return response(data)
            return result
        data=db.loads(result['text'])
        if payload.get('candidate_to_repair'):
            data['paragraphs'][0]['text']='相关文献指出模块职责清晰有助于维护。'
            data['paragraphs'][0]['claim_spans'][0]['text']=data['paragraphs'][0]['text']
        data['paragraphs'].append({'text':'本节随后讨论授权条件。'})
        return response(data)
    monkeypatch.setattr(provider,'generate',transport)
    _,result=write(e)
    assert result['writing_quality']['repair_rounds'][0]['accepted'] is False
    assert '相关文献指出' not in result['text']
    assert result['writing_quality']['semantic']['claims'][0]['support']=='supported'
