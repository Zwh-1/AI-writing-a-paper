"""A paid revision may not replace a structurally valid retained candidate."""
from copy import deepcopy

import pytest

from test_workbench import client  # noqa: F401
from test_writing_flow_integration import writing_project,response  # noqa: F401
from test_writing_flow_safety import _running,_basis
from app import db,provider,security,tasks,writing_flow


def scenario(env,monkeypatch,mode):
    writing_flow.save_settings(env['pid'],{'max_revision_rounds':2,'originality_check':False})
    task=_running(env);section={**security.require_section(env['sid']),'target_words':1000}
    context,manifest=_basis(env);sid=context['sources'][0]['id']
    source=deepcopy(context['sources'][0]);text='模块职责清晰有助于维护。'
    paragraph={'text':text,'source_ids':[sid]}
    candidate=tasks.candidate_from_text(db.dumps({'paragraphs':[paragraph],
        'missing_materials':['必须保留的实际运行记录缺口']}))[0]
    if mode in ('resolve_old_critical','replace_old_critical','improve_old_critical'):
        candidate['paragraphs'][0]['claim_spans']=[{'local_id':'c1','text':'旧的不存在原句。','source_ids':[sid]}]
    if mode=='improve_old_critical':
        candidate['paragraphs'].append({'text':'接口边界应与授权条件保持一致。','source_ids':[sid],
            'claim_spans':[{'local_id':'c2','text':'第二个不在正文中的原句。','source_ids':[sid]}]})
    candidate=tasks.candidate_from_text(db.dumps(candidate))[0]
    before_context,before_manifest=deepcopy(context),deepcopy(manifest)
    calls={'semantic':0,'revision':0};revised_value=None
    def transport(system,user,images=None):
        nonlocal revised_value
        payload=db.loads(user)
        if 'claim_reviews' in system:
            calls['semantic']+=1
            return response({'claim_reviews':[{'claim_id':claim['claim_id'],
                'support':'unsupported' if calls['semantic']==1 else 'supported',
                'source_quotes':[{'source_id':source['id'],'quote':source['text']}],
                'reason':'必须保留的来源支持疑点'} for claim in payload['claims']],
                'point_reviews':[],'findings':[]})
        calls['revision']+=1
        if mode=='contract':return response({})|{'text':'完整返回但不是约定JSON'}
        revised=deepcopy(candidate)
        if mode=='critical':
            revised['paragraphs'][0]['claim_spans']=[{'local_id':'c1','text':'正文不存在的原句。','source_ids':[sid]}]
        elif mode.startswith('marker:'):
            revised['paragraphs'][0]['text']+='['+mode.split(':',1)[1]+':internal-id]'
        elif mode=='oversize':revised['paragraphs'][0]['text']='甲'*1400
        elif mode=='repeated':
            long='接口边界按照授权条件划分，来源版本与签发主体绑定，静态代码仅用于说明实现结构，并不能代替本项目的真实运行记录。'*2
            revised['paragraphs']=[{'text':long,'source_ids':[sid]},{'text':long.replace('，',' '),'source_ids':[sid]}]
        elif mode=='valid':revised['paragraphs'][0]['text']='相关文献指出模块职责清晰有助于维护。'
        elif mode in ('resolve_old_critical','improve_old_critical'):
            revised['paragraphs'][0]['claim_spans'][0]['text']=text
        elif mode=='replace_old_critical':
            revised['paragraphs'][0]['claim_spans'][0]['text']='另一个新的无效原句。'
        revised_value=revised
        return response(revised)
    monkeypatch.setattr(provider,'generate',transport)
    result=writing_flow.finish(task,section,candidate,'按原文检查并修订',context,manifest,tasks.candidate_from_text)
    return task,candidate,result,calls,before_context,before_manifest,revised_value


@pytest.mark.parametrize('mode,code',[
    ('critical','new_local_critical'),('marker:cite','internal_protocol_marker'),
    ('marker:figref','internal_protocol_marker'),('marker:evidence','internal_protocol_marker'),
    ('marker:knowledge_unit','internal_protocol_marker'),('marker:source','internal_protocol_marker'),
    ('marker:document_reading','internal_protocol_marker'),('oversize','chapter_word_limit'),
    ('repeated','new_repeated_paragraph')])
def test_bad_revision_keeps_the_old_candidate_scope_and_skips_paid_semantic(writing_project,monkeypatch,mode,code):
    task,candidate,result,calls,before_context,before_manifest,_=scenario(writing_project,monkeypatch,mode)
    current,context,manifest,report,bundle=result
    assert current['paragraphs']==candidate['paragraphs']
    assert current['missing_materials']==candidate['missing_materials']
    assert context==before_context and bundle is None
    assert all(manifest[key]==value for key,value in before_manifest.items())
    assert 'evidence_bundle' not in manifest
    assert calls=={'semantic':1,'revision':1}
    assert report['status']=='needs_review' and report['human_review']=='pending'
    assert report['semantic']['claims'][0]['support']=='unsupported'
    assert any(f['kind']=='source_support_unsupported' for f in report['findings'])
    rejected=next(f for f in report['findings'] if f['kind']=='revision_rejected')
    assert rejected['human_review']==rejected['semantic_review']=='pending'
    round_=report['repair_rounds'][0]
    assert not round_['accepted'] and round_['rejection_stage']=='local_integrity_before_semantic'
    assert any(e['code']==code for e in round_['errors'])
    assert round_['run_id']==rejected['run_id'] and round_['raw_response_retained']
    run=db.one('SELECT status,manifest FROM generation_runs WHERE id=?',(round_['run_id'],))
    assert run['status']=='completed' and db.loads(run['manifest'])['output_blob']==round_['output_blob']
    assert len(db.rows('SELECT id FROM generation_runs WHERE task_id=?',(task['id'],)))==2


def test_valid_revision_still_enters_semantic_and_can_replace_the_old_candidate(writing_project,monkeypatch):
    _,_,result,calls,_,_,revised=scenario(writing_project,monkeypatch,'valid')
    current,context,manifest,report,bundle=result
    assert calls=={'semantic':2,'revision':1}
    assert current['paragraphs']==revised['paragraphs']
    assert report['repair_rounds'][0]['accepted']
    assert not any(f['kind']=='revision_rejected' for f in report['findings'])
    assert bundle and manifest['evidence_bundle_hash']==bundle['hash']


def test_old_critical_error_may_be_resolved_before_a_revision_is_accepted(writing_project,monkeypatch):
    _,_,result,calls,_,_,_=scenario(writing_project,monkeypatch,'resolve_old_critical')
    current,_,_,report,_=result
    assert calls=={'semantic':2,'revision':1}
    assert report['repair_rounds'][0]['accepted']
    assert not report['mapping']['status']=='issues'
    assert current['paragraphs'][0]['claim_spans'][0]['text']==current['paragraphs'][0]['text']


def test_an_old_critical_count_does_not_license_a_new_unrelated_critical_error(writing_project,monkeypatch):
    _,candidate,result,calls,_,_,_=scenario(writing_project,monkeypatch,'replace_old_critical')
    current,_,_,report,_=result
    assert current['paragraphs']==candidate['paragraphs']
    assert calls=={'semantic':1,'revision':1}
    assert any(e['code']=='new_local_critical' for e in report['repair_rounds'][0]['errors'])


def test_old_critical_findings_may_remain_when_a_revision_really_improves_them(writing_project,monkeypatch):
    _,_,result,calls,_,_,_=scenario(writing_project,monkeypatch,'improve_old_critical')
    _,_,_,report,_=result
    assert calls=={'semantic':2,'revision':2}
    assert report['repair_rounds'][0]['accepted']
    assert report['mapping']['status']=='issues'
    assert report['status']=='needs_review'
    assert sum(f['kind']=='claim_mapping_invalid' for f in report['findings'])==1
    assert report['human_review']=='pending'


def test_invalid_revision_contract_keeps_the_old_scope_and_run_trace(writing_project,monkeypatch):
    _,candidate,result,calls,before_context,_,_=scenario(writing_project,monkeypatch,'contract')
    current,context,_,report,bundle=result
    assert current['paragraphs']==candidate['paragraphs']
    assert context==before_context and bundle is None
    assert calls=={'semantic':1,'revision':1}
    assert report['repair_rounds'][0]['errors'][0]['code']=='candidate_contract'
    assert report['repair_rounds'][0]['raw_response_retained']
